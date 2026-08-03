"""Portfolio endpoints: account list, Myfxbook sync, MT5 agent ingest, equity."""
from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db import get_db
from app.portfolio import myfxbook
from app import models

router = APIRouter(prefix="/api", tags=["portfolio"])


class AgentSnapshot(BaseModel):
    account_login: str
    name: str | None = None
    equity: float | None = None
    balance: float | None = None
    dd_pct: float | None = None
    open_positions: list | None = None


class MyfxbookSync(BaseModel):
    email: str
    password: str
    days: int = 90


class LinkRequest(BaseModel):
    pass_id: int | None = None   # null unlinks


def _linked_pass(db: Session, pass_id: int | None) -> dict | None:
    if not pass_id:
        return None
    p = db.get(models.Pass, pass_id)
    if p is None:
        return None
    return {"pass_id": p.id, "pass_no": p.pass_no,
            "robustness_score": p.robustness_score,
            "profit_factor": p.profit_factor, "equity_dd_pct": p.equity_dd_pct,
            "back_result": p.back_result, "forward_result": p.forward_result,
            "trades": p.trades}


def _latest(db: Session, account_id: int):
    # id tiebreaks ts: snapshots can share a clock-second, and id is monotonic.
    return (db.query(models.AccountSnapshot)
            .filter_by(account_id=account_id)
            .order_by(desc(models.AccountSnapshot.ts),
                      desc(models.AccountSnapshot.id)).first())


def _upsert_account(db: Session, kind: str, ext_ref: str, label: str) -> models.Account:
    a = db.query(models.Account).filter_by(kind=kind, ext_ref=ext_ref).first()
    if a is None:
        a = models.Account(kind=kind, ext_ref=ext_ref, label=label)
        db.add(a)
        db.flush()
    elif label:
        a.label = label
    return a


@router.get("/accounts")
def list_accounts(db: Session = Depends(get_db)):
    out = []
    for a in db.query(models.Account).all():
        s = _latest(db, a.id)
        latest = None
        if s is not None:
            # gain% is stashed on the current snapshot; recovery factor = gain / max DD
            gain = s.open_positions_json.get("gain") if isinstance(s.open_positions_json, dict) else None
            rf = (gain / s.dd_pct) if (gain is not None and s.dd_pct) else None
            latest = {
                "ts": s.ts.isoformat() if s.ts else None,
                "equity": s.equity, "balance": s.balance, "dd_pct": s.dd_pct,
                "gain": gain, "recovery_factor": rf,
            }
        out.append({
            "id": a.id, "kind": a.kind, "label": a.label, "ext_ref": a.ext_ref,
            "linked_pass_id": a.linked_pass_id,
            "linked": _linked_pass(db, a.linked_pass_id),
            "latest": latest,
        })
    return out


@router.post("/accounts/{account_id}/link")
def link_pass(account_id: int, body: LinkRequest, db: Session = Depends(get_db)):
    """Pin a leaderboard pass to a live account (or unlink with pass_id=null)."""
    a = db.get(models.Account, account_id)
    if a is None:
        raise HTTPException(status_code=404, detail="account not found")
    if body.pass_id is not None and db.get(models.Pass, body.pass_id) is None:
        raise HTTPException(status_code=404, detail="pass not found")
    a.linked_pass_id = body.pass_id
    db.commit()
    return {"account_id": account_id, "linked_pass_id": a.linked_pass_id}


@router.post("/agent/snapshot")
def agent_snapshot(payload: AgentSnapshot,
                   x_agent_token: str | None = Header(None),
                   db: Session = Depends(get_db)):
    """Ingest a snapshot pushed by the local MT5 companion agent (token-auth).

    The agent reads MT5 with the INVESTOR (read-only) password on the user's PC
    and posts here — credentials never reach the server.
    """
    token = os.getenv("MT5_AGENT_TOKEN")
    if not token or x_agent_token != token:
        raise HTTPException(status_code=401, detail="invalid agent token")
    a = _upsert_account(db, "mt5", payload.account_login,
                        payload.name or f"MT5 {payload.account_login}")
    snap = models.AccountSnapshot(
        account_id=a.id, equity=payload.equity, balance=payload.balance,
        dd_pct=payload.dd_pct, open_positions_json=payload.open_positions or [])
    db.add(snap)
    db.commit()
    return {"account_id": a.id, "snapshot_id": snap.id}


def _existing_dates(db: Session, account_id: int) -> set:
    dates = set()
    for (ts,) in db.query(models.AccountSnapshot.ts).filter_by(account_id=account_id):
        if ts:
            dates.add(ts.date())
    return dates


@router.post("/portfolio/myfxbook/sync")
def myfxbook_sync(body: MyfxbookSync, db: Session = Depends(get_db)):
    """Log in to Myfxbook and sync every tracked account. Credentials not stored.

    Backfills the account's daily equity history (so the chart is populated on
    the very first sync), then appends a live current snapshot. Re-syncing is
    idempotent for history — daily points already stored are skipped.
    """
    import datetime

    end = datetime.date.today()
    start = end - datetime.timedelta(days=body.days)
    synced = []
    # One shared client so Myfxbook's session (cookies + token) survives every
    # call — separate connections get rejected as "Invalid session".
    with httpx.Client(timeout=30) as http:
        try:
            session = myfxbook.login(body.email, body.password, client=http)
            accounts = myfxbook.get_accounts(session, client=http)
        except myfxbook.MyfxbookError as e:
            raise HTTPException(status_code=400, detail=f"Myfxbook: {e}")

        for acc in accounts:
            n = myfxbook.normalize_account(acc)
            a = _upsert_account(db, "myfxbook", n["ext_ref"], n["label"])
            have = _existing_dates(db, a.id)

            try:
                daily = myfxbook.get_data_daily(session, n["ext_ref"],
                                                start.isoformat(), end.isoformat(),
                                                client=http)
                points = myfxbook.parse_daily(daily)
            except myfxbook.MyfxbookError:
                points = []

            added = 0
            for p in points:
                if p["ts"].date() in have:
                    continue
                db.add(models.AccountSnapshot(
                    account_id=a.id, ts=p["ts"], balance=p["balance"],
                    equity=p["equity"], dd_pct=None, open_positions_json={}))
                have.add(p["ts"].date())
                added += 1

            db.add(models.AccountSnapshot(
                account_id=a.id, equity=n["equity"], balance=n["balance"],
                dd_pct=n["dd_pct"], open_positions_json={"gain": n["gain"]}))
            synced.append({"account_id": a.id, "label": n["label"],
                           "history_points": added})
        db.commit()
        myfxbook.logout(session, client=http)
    return {"synced": len(synced), "accounts": synced}


@router.get("/accounts/{account_id}/equity")
def equity_series(account_id: int, db: Session = Depends(get_db)):
    rows = (db.query(models.AccountSnapshot)
            .filter_by(account_id=account_id)
            .order_by(models.AccountSnapshot.ts, models.AccountSnapshot.id).all())
    return [{"ts": s.ts.isoformat() if s.ts else None, "equity": s.equity,
             "balance": s.balance, "dd_pct": s.dd_pct} for s in rows]
