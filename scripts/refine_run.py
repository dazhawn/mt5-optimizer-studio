"""Refine a scored run into a narrowed .set file.

    python -m scripts.refine_run 1 --base path/to/base.set --out refined.set
    python -m scripts.refine_run 1 --top-frac 0.1 --min-top 25   (plan only, no set file)
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.db import SessionLocal, init_db          # noqa: E402
from app.optimize.refine import refine_run         # noqa: E402

ACT = {"narrow": "NARROW", "pin": "pin", "fixed": "·"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id", type=int)
    ap.add_argument("--base", default=None, help="base .set file to rewrite")
    ap.add_argument("--out", default=None, help="where to write the refined .set")
    ap.add_argument("--top-frac", type=float, default=0.10)
    ap.add_argument("--min-top", type=int, default=25)
    args = ap.parse_args()

    base_raw = pathlib.Path(args.base).read_bytes() if args.base else None

    init_db()
    db = SessionLocal()
    try:
        res = refine_run(db, args.run_id, base_raw=base_raw,
                         top_frac=args.top_frac, min_top=args.min_top)
    finally:
        db.close()

    bp = res["best_pass"]
    print(f"run {res['run_id']}: analyzed {res['kept']} scored passes, "
          f"top {res['top_used']} used")
    print(f"best pass #{bp['pass_no']} (score {bp['robustness_score']})")
    s = res["summary"]
    print(f"decisions: {s['narrow']} narrowed · {s['pin']} pinned · {s['fixed']} fixed\n")

    print(f"{'input':<32} {'action':>7}  {'sens':>5}  detail")
    for p in sorted(res["plans"], key=lambda x: (x["action"] != "narrow", -x["sensitivity"])):
        tag = ACT.get(p["action"], p["action"])
        rng = ""
        if p["action"] == "narrow":
            rng = f"[{p['new_start']:g}..{p['new_stop']:g}] "
        print(f"{p['name']:<32} {tag:>7}  {p['sensitivity']:>5.2f}  {rng}{p['reason']}")

    if "search_space" in res:
        ss = res["search_space"]
        print(f"\nsearch space: {ss['before']:,} -> {ss['after']:,} combinations"
              f"  ({ss['reduction_x']:,}x smaller)")

    if args.out and res.get("_refined_bytes"):
        pathlib.Path(args.out).write_bytes(res["_refined_bytes"])
        print(f"wrote refined set file -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
