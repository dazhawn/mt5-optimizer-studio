"""Myfxbook API client (read-only account tracking).

Cloud-side portfolio source: no local footprint, good for accounts you can't
attach a terminal to. Flow: ``login`` -> ``get_accounts`` -> ``logout``. The
pure ``normalize_account`` maps a Myfxbook account object to our snapshot shape
and is unit-tested without any network.

Docs: https://www.myfxbook.com/api
"""
from __future__ import annotations

import datetime
import os
import sys
from urllib.parse import unquote

import httpx

BASE = "https://www.myfxbook.com/api"


class MyfxbookError(RuntimeError):
    pass


def _get(path: str, params: dict, client: httpx.Client | None) -> dict:
    c = client or httpx.Client(timeout=20)
    try:
        resp = c.get(f"{BASE}/{path}", params=params)
        data = resp.json()
    finally:
        if client is None:
            c.close()
    if os.getenv("MYFXBOOK_DEBUG"):  # response structure only, never the password
        sess = str(data.get("session") or "")
        masked = (sess[:4] + "..." + sess[-4:]) if len(sess) > 8 else sess
        print(f"[myfxbook] {path} http={resp.status_code} error={data.get('error')!r} "
              f"message={data.get('message')!r} keys={sorted(data.keys())} "
              f"session_len={len(sess)} session_masked={masked!r}",
              file=sys.stderr, flush=True)
    if data.get("error"):
        raise MyfxbookError(data.get("message") or f"{path} failed")
    return data


def login(email: str, password: str, *, client: httpx.Client | None = None) -> str:
    session = _get("login.json", {"email": email, "password": password}, client).get("session")
    if not session:
        raise MyfxbookError("login returned no session")
    # Myfxbook returns the token URL-encoded (e.g. base64 "=" as "%3D"). Decode it
    # once so the HTTP client re-encodes it correctly instead of double-encoding.
    return unquote(session)


def get_accounts(session: str, *, client: httpx.Client | None = None) -> list[dict]:
    return _get("get-my-accounts.json", {"session": session}, client).get("accounts", [])


def logout(session: str, *, client: httpx.Client | None = None) -> None:
    try:
        _get("logout.json", {"session": session}, client)
    except MyfxbookError:
        pass


def get_data_daily(session: str, account_id: str, start: str, end: str, *,
                   client: httpx.Client | None = None) -> list:
    """Raw ``dataDaily`` for an account. ``start``/``end`` are YYYY-MM-DD."""
    data = _get("get-data-daily.json",
                {"session": session, "id": account_id, "start": start, "end": end},
                client)
    return data.get("dataDaily", [])


def _parse_date(s: str) -> datetime.datetime | None:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%Y %H:%M"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_daily(data_daily: list) -> list[dict]:
    """Flatten Myfxbook ``dataDaily`` into ``[{ts, balance, equity}]``, sorted.

    Myfxbook nests each day as a one-element list; we tolerate both that and a
    flat list of dicts. equity = balance + floatingPL when available.
    """
    out: list[dict] = []
    for entry in data_daily:
        items = entry if isinstance(entry, list) else [entry]
        for it in items:
            if not isinstance(it, dict):
                continue
            ts = _parse_date(str(it.get("date", "")))
            if ts is None:
                continue
            bal = _f(it.get("balance"))
            floating = _f(it.get("floatingPL")) or 0.0
            equity = (bal + floating) if bal is not None else None
            out.append({"ts": ts, "balance": bal, "equity": equity})
    out.sort(key=lambda x: x["ts"])
    return out


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def normalize_account(acc: dict) -> dict:
    """Map a Myfxbook account object onto our snapshot fields."""
    return {
        "ext_ref": str(acc.get("id")),
        "label": acc.get("name") or f"account {acc.get('id')}",
        "equity": _f(acc.get("equity")),
        "balance": _f(acc.get("balance")),
        "dd_pct": _f(acc.get("drawdown")),
        "gain": _f(acc.get("gain")),
        "currency": acc.get("currency"),
        "demo": bool(acc.get("demo")),
    }
