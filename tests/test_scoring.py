"""Robustness scoring tests (pure function, no DB)."""
from __future__ import annotations

from app.scoring.engine import Weights, score_passes, _percentile_ranks


def _p(pass_no, pf, profit, dd, back, fwd, trades, id=None):
    return {"id": id or pass_no, "pass_no": pass_no, "profit_factor": pf,
            "profit": profit, "equity_dd_pct": dd, "back_result": back,
            "forward_result": fwd, "trades": trades}


def test_percentile_ranks_basic():
    r = _percentile_ranks([10, 20, 30])
    assert r == [1 / 6, 3 / 6, 5 / 6]           # average-rank percentiles
    assert _percentile_ranks([5, None, 5])[1] is None


def test_hard_filters_drop_lowtrades_and_missing_forward():
    passes = [
        _p(1, 1158, 34, 0.04, 36, 0.13, 8),     # 8 trades -> dropped
        _p(2, 2.0, 4000, 1.5, 99, 98, 300),     # kept
        _p(3, 3.0, 5000, 2.0, 50, None, 200),   # no forward -> dropped
    ]
    res = score_passes(passes, min_trades=100)
    assert res["kept"] == 1
    assert res["filtered_out"] == 2
    kept_ids = {s["pass_no"] for s in res["scored"]}
    assert kept_ids == {2}
    assert set(res["dropped_ids"]) == {1, 3}


def test_dominant_pass_ranks_first():
    passes = [
        _p(1, 5.0, 9000, 0.5, 99, 98, 300),     # best PF, best profit, best DD, best consistency
        _p(2, 2.0, 4000, 2.0, 99, 60, 200),
        _p(3, 3.0, 5000, 1.5, 99, 80, 250),
    ]
    res = score_passes(passes, min_trades=100)
    assert res["scored"][0]["pass_no"] == 1
    assert res["scored"][0]["robustness_score"] == max(
        s["robustness_score"] for s in res["scored"])


def test_consistency_guardrail_breaks_ties():
    # Identical PF/profit/DD; only forward differs -> higher consistency wins.
    passes = [
        _p(1, 2.0, 1000, 1.0, 100, 90, 200),    # consistency 0.90
        _p(2, 2.0, 1000, 1.0, 100, 50, 200),    # consistency 0.50
    ]
    res = score_passes(passes, min_trades=100)
    assert res["scored"][0]["pass_no"] == 1
    assert res["scored"][0]["robustness_score"] > res["scored"][1]["robustness_score"]


def test_profitability_gate_drops_breakeven_and_losers():
    # The real-data failure mode: tiny-DD break-even/losing passes must NOT score
    # high just because low drawdown + coincidental consistency rank well.
    passes = [
        _p(1, 2.0, 4000, 1.0, 99, 98, 300),     # profitable -> kept
        _p(2, 0.97, -19.0, 0.2, 40, 40, 200),   # PF<1, losing -> dropped
        _p(3, 1.0, 0.0, 0.1, 40, 40, 200),      # break-even -> dropped
        _p(4, 0.96, -22.0, 0.02, 0.02, 40, 150),  # degenerate back≈0 -> dropped
    ]
    res = score_passes(passes, min_trades=100)
    assert res["kept"] == 1
    assert {s["pass_no"] for s in res["scored"]} == {1}
    # Turning the gate off keeps them (opt-in only).
    res2 = score_passes(passes, min_trades=100, require_profitable=False)
    assert res2["kept"] == 4


def test_weights_reconfigurable():
    passes = [
        _p(1, 10.0, 100, 5.0, 100, 10, 200),    # huge PF, terrible everything else
        _p(2, 1.5, 9000, 0.1, 100, 99, 200),    # tiny PF, great everything else
    ]
    # All weight on profit factor -> pass 1 wins despite being a bad set.
    pf_only = score_passes(passes, weights=Weights(profit_factor=1, drawdown=0,
                                                   profit=0, consistency=0),
                           min_trades=100)
    assert pf_only["scored"][0]["pass_no"] == 1
    # Default balanced weights -> the robust set wins.
    balanced = score_passes(passes, min_trades=100)
    assert balanced["scored"][0]["pass_no"] == 2
