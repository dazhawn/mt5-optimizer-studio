"""Run (optimization results) endpoints."""
from __future__ import annotations

import base64
import os
import shutil
import tempfile

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.ingest.optresults import persist_run
from app.scoring.engine import Weights, score_run
from app.optimize.refine import refine_run
from app import models

router = APIRouter(prefix="/api/runs", tags=["runs"])


class PropFirmSpec(BaseModel):
    max_total_dd_pct: float = 10.0
    min_trades: int = 0
    min_profit_factor: float | None = None
    profit_target_pct: float | None = None
    account_size: float | None = None
    only: bool = False   # filter the leaderboard to prop-safe passes


class ScoreRequest(BaseModel):
    min_trades: int = 100
    top: int = 25
    require_profitable: bool = True
    weights: dict[str, float] | None = None  # {profit_factor, drawdown, profit, consistency}
    propfirm: PropFirmSpec | None = None


def _process_import(job_id: int, tmp_path: str, ea_name: str,
                    symbol: str | None, timeframe: str | None) -> None:
    """Background worker: parse + persist a run, updating the job record.

    Runs in a threadpool (BackgroundTasks) with its own DB session so a large
    file (tens of MB / tens of thousands of passes) never blocks the request.
    """
    db = SessionLocal()
    try:
        job = db.get(models.ImportJob, job_id)
        job.status = "processing"
        db.commit()
        run_id, n = persist_run(db, tmp_path, ea_name=ea_name, symbol=symbol,
                                timeframe=timeframe)
        job = db.get(models.ImportJob, job_id)
        job.status, job.run_id, job.passes = "done", run_id, n
        db.commit()
    except Exception as e:  # record the failure so the UI can surface it
        db.rollback()
        job = db.get(models.ImportJob, job_id)
        if job:
            job.status, job.error = "error", str(e)[:1000]
            db.commit()
    finally:
        db.close()
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@router.post("/import")
async def import_run(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    ea_name: str = Form(...),
    symbol: str | None = Form(None),
    timeframe: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Queue an MT5 optimization XML import; parse happens in the background.

    Returns a ``job_id`` immediately so a large upload doesn't hit request
    timeouts. Poll ``GET /api/runs/import/{job_id}`` for progress.
    """
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    job = models.ImportJob(ea_name=ea_name, symbol=symbol, timeframe=timeframe,
                           status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    background.add_task(_process_import, job.id, tmp_path, ea_name, symbol, timeframe)
    return {"job_id": job.id, "status": job.status}


@router.get("/import/{job_id}")
def import_status(job_id: int, db: Session = Depends(get_db)):
    job = db.get(models.ImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {"job_id": job.id, "status": job.status, "run_id": job.run_id,
            "passes": job.passes, "error": job.error}


@router.get("")
def list_runs(db: Session = Depends(get_db)):
    """All imported runs with EA + pass count (feeds the dashboard header)."""
    out = []
    for r in db.query(models.Run).all():
        cnt = db.query(func.count(models.Pass.id)).filter(
            models.Pass.run_id == r.id).scalar()
        out.append({"id": r.id, "ea": r.ea.name if r.ea else None,
                    "symbol": r.symbol, "timeframe": r.timeframe, "passes": cnt})
    return out


@router.post("/{run_id}/score")
def score(run_id: int, req: ScoreRequest, db: Session = Depends(get_db)):
    """Compute robustness scores for a run and persist them onto each pass."""
    weights = Weights(**req.weights) if req.weights else Weights()
    profile = None
    if req.propfirm is not None:
        from app.propfirm.rules import PropFirmProfile
        pf = req.propfirm
        profile = PropFirmProfile(
            max_total_dd_pct=pf.max_total_dd_pct, min_trades=pf.min_trades,
            min_profit_factor=pf.min_profit_factor,
            profit_target_pct=pf.profit_target_pct, account_size=pf.account_size)
    return score_run(db, run_id, weights=weights, min_trades=req.min_trades,
                     require_profitable=req.require_profitable, top=req.top,
                     propfirm=profile,
                     prop_only=req.propfirm.only if req.propfirm else False)


@router.post("/{run_id}/refine")
async def refine(run_id: int, top_frac: float = 0.10, min_top: int = 25,
                 base: UploadFile | None = File(None),
                 db: Session = Depends(get_db)):
    """Analyze a scored run's top passes and (with a base .set) emit a narrowed one.

    Returns the per-input plan + search-space reduction. When ``base`` is
    supplied, ``refined_setfile_b64`` holds the rewritten set file (base64).
    """
    base_raw = await base.read() if base is not None else None
    res = refine_run(db, run_id, base_raw=base_raw, top_frac=top_frac,
                     min_top=min_top)
    refined = res.pop("_refined_bytes", None)
    if refined is not None:
        res["refined_setfile_b64"] = base64.b64encode(refined).decode("ascii")
    return res


@router.get("/{run_id}/leaderboard")
def leaderboard(run_id: int, limit: int = 25, db: Session = Depends(get_db)):
    """Passes for a run ranked by robustness_score. Call POST /score first."""
    q = (db.query(models.Pass)
         .filter(models.Pass.run_id == run_id,
                 models.Pass.robustness_score.isnot(None))
         .order_by(models.Pass.robustness_score.desc())
         .limit(limit))
    return [
        {
            "id": p.id, "pass_no": p.pass_no, "robustness_score": p.robustness_score,
            "back_result": p.back_result, "forward_result": p.forward_result,
            "profit": p.profit, "profit_factor": p.profit_factor,
            "equity_dd_pct": p.equity_dd_pct, "trades": p.trades,
        }
        for p in q
    ]
