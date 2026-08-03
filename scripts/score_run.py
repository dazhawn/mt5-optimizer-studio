"""Score a persisted run and print the robust leaderboard.

    python -m scripts.score_run 1 --min-trades 100 --top 15
    python -m scripts.score_run 1 --pf 0.4 --dd 0.3 --profit 0.3 --cons 0.0
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.db import SessionLocal, init_db          # noqa: E402
from app.scoring.engine import Weights, score_run  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id", type=int)
    ap.add_argument("--min-trades", type=int, default=100)
    ap.add_argument("--pf", type=float, default=0.30)
    ap.add_argument("--dd", type=float, default=0.25)
    ap.add_argument("--profit", type=float, default=0.25)
    ap.add_argument("--cons", type=float, default=0.20)
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    weights = Weights(profit_factor=args.pf, drawdown=args.dd,
                      profit=args.profit, consistency=args.cons)

    init_db()
    db = SessionLocal()
    try:
        res = score_run(db, args.run_id, weights=weights,
                        min_trades=args.min_trades, top=args.top)
    finally:
        db.close()

    w = res["weights"]
    print(f"run {res['run_id']}: kept {res['kept']} / filtered out {res['filtered_out']}"
          f"  (min_trades={res['min_trades']})")
    print(f"weights: PF={w['profit_factor']:.2f} DD={w['drawdown']:.2f} "
          f"Profit={w['profit']:.2f} Consistency={w['consistency']:.2f}\n")
    print(f"{'rank':>4} {'pass':>7} {'score':>6} {'PF':>9} {'profit':>10} "
          f"{'DD%':>7} {'back':>7} {'fwd':>7} {'f/b':>5} {'trades':>7}")
    for i, s in enumerate(res["top"], 1):
        fb = (s["forward_result"] / s["back_result"]) if s["back_result"] else 0
        print(f"{i:>4} {s['pass_no']:>7} {s['robustness_score']:>6.1f} "
              f"{s['profit_factor']:>9.2f} {s['profit']:>10.2f} "
              f"{s['equity_dd_pct']:>7.2f} {s['back_result']:>7.2f} "
              f"{s['forward_result']:>7.2f} {fb:>5.2f} {s['trades']:>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
