"""Set-file endpoints: upload → parse → normalized JSON (and persist)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.dialects import detect_dialect, parse_set, render_set
from app import models

router = APIRouter(prefix="/api/setfiles", tags=["setfiles"])


def _param_dict(p) -> dict:
    return {
        "name": p.name, "kind": p.kind, "current": p.current,
        "start": p.start, "step": p.step, "stop": p.stop,
        "optimize": p.optimize,
    }


@router.post("")
async def upload_setfile(
    file: UploadFile = File(...),
    ea_name: str | None = None,
    persist: bool = False,
    db: Session = Depends(get_db),
):
    """Parse an uploaded .set file. Set ``persist=true`` to store it under an EA."""
    raw = await file.read()
    dialect_name, confidence = detect_dialect(raw)
    parsed = parse_set(raw)
    round_trip_ok = render_set(parsed) == raw

    payload = {
        "filename": file.filename,
        "dialect": dialect_name,
        "confidence": round(confidence, 3),
        "encoding": parsed.codec,
        "round_trip_ok": round_trip_ok,
        "counts": {
            "params": len(parsed.params),
            "tunable": len(parsed.tunable()),
            "optimizable": len(parsed.optimizable()),
        },
        "optimizable": [_param_dict(p) for p in parsed.optimizable()],
    }

    if persist:
        ea = db.query(models.EA).filter_by(name=ea_name or file.filename).first()
        if ea is None:
            ea = models.EA(name=ea_name or file.filename, dialect=dialect_name)
            db.add(ea)
            db.flush()
        sf = models.SetFile(ea_id=ea.id, name=file.filename or "unnamed",
                            dialect=dialect_name, encoding=parsed.codec, raw_blob=raw)
        db.add(sf)
        db.flush()
        for p in parsed.params:
            db.add(models.Param(
                set_file_id=sf.id, name=p.name, kind=p.kind,
                current=None if p.current is None else str(p.current),
                start=p.start, step=p.step, stop=p.stop,
                optimize=p.optimize, raw_line=p.raw_line))
        db.commit()
        payload["persisted"] = {"ea_id": ea.id, "set_file_id": sf.id}

    return payload
