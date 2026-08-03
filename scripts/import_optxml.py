"""Proof/utility CLI for MT5 optimization XML.

Summarize (streaming, no DB):
    python -m scripts.import_optxml summarize path/to/results.xml

Persist into the DB under an EA name:
    python -m scripts.import_optxml persist path/to/results.xml "Archangel X" --symbol XAGUSD --tf M1
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.ingest.optresults import summarize, persist_run  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("summarize")
    s.add_argument("path")
    s.add_argument("--top", type=int, default=5)

    p = sub.add_parser("persist")
    p.add_argument("path")
    p.add_argument("ea_name")
    p.add_argument("--symbol", default=None)
    p.add_argument("--tf", default=None)

    args = ap.parse_args()
    t0 = time.time()

    if args.cmd == "summarize":
        result = summarize(args.path, top_n=args.top)
        result["_seconds"] = round(time.time() - t0, 2)
        print(json.dumps(result, indent=2))
        return 0

    if args.cmd == "persist":
        from app.db import SessionLocal, init_db
        init_db()
        db = SessionLocal()
        try:
            run_id, n = persist_run(db, args.path, ea_name=args.ea_name,
                                    symbol=args.symbol, timeframe=args.tf)
        finally:
            db.close()
        print(f"imported run_id={run_id} passes={n} in {time.time() - t0:.2f}s")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
