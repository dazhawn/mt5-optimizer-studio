"""Robustness scoring engine.

Turns a run's raw passes into a 0–100 ``robustness_score`` that ranks *robust*
sets above curve-fit ones. Built and validated against real ArchAngel data
(15,144 passes) whose lessons are baked in:

* **Hard filters first.** Passes with too few trades, or no forward result, are
  dropped before scoring — the real data's "best" profit factor (1158) came from
  an 8-trade pass whose forward collapsed to 0.13. Junk must never be scored.
* **Per-run percentile normalization.** ``back_result`` saturates at 99.99 on
  most passes and drawdowns are tiny (<2%); absolute thresholds are meaningless.
  Each component is normalized by its percentile rank *within the filtered run*,
  so scales and saturation don't distort weights.
* **Profit factor via percentile, not magnitude.** A 1158 PF and a 6.0 PF land at
  similar top percentiles, so an outlier can't dominate the weighted sum.

Default weights emphasize profit factor + drawdown + profit (per the user's
preference), with a consistency guardrail so a high-PF / collapsing-forward pass
can't win. Everything is tunable.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, asdict

# Components that feed the weighted sum. Each maps a pass to a "higher = better"
# raw value; all are then percentile-normalized within the filtered run.
COMPONENTS = ("profit_factor", "drawdown", "profit", "consistency")


@dataclass
class Weights:
    profit_factor: float = 0.30
    drawdown: float = 0.25       # inverted: lower equity DD% scores higher
    profit: float = 0.25
    consistency: float = 0.20    # clip(forward/back); guardrail against overfit

    def as_dict(self) -> dict[str, float]:
        return asdict(self)

    def normalized(self) -> dict[str, float]:
        d = self.as_dict()
        total = sum(d.values())
        if total <= 0:
            raise ValueError("weights must sum to a positive number")
        return {k: v / total for k, v in d.items()}


# Below this in-sample criterion, forward/back is meaningless (a near-zero back
# result makes the ratio explode into fake "perfect consistency").
_BACK_FLOOR = 1.0


def _consistency_raw(p: dict) -> float | None:
    """clip(forward/back) in [0, 1]. 1.0 = forward held up (or beat) back."""
    back = p.get("back_result")
    fwd = p.get("forward_result")
    if back is None or fwd is None or back < _BACK_FLOOR:
        return None
    return max(0.0, min(fwd / back, 1.0))


def _raw_value(p: dict, component: str) -> float | None:
    if component == "profit_factor":
        return p.get("profit_factor")
    if component == "drawdown":
        dd = p.get("equity_dd_pct")
        return None if dd is None else -dd    # lower DD -> higher score
    if component == "profit":
        return p.get("profit")
    if component == "consistency":
        return _consistency_raw(p)
    raise KeyError(component)


def _percentile_ranks(values: list[float | None]) -> list[float | None]:
    """Average-rank percentile in (0, 1); Nones pass through as None."""
    present = sorted(v for v in values if v is not None)
    n = len(present)
    if n == 0:
        return [None] * len(values)
    out: list[float | None] = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        less = bisect.bisect_left(present, v)
        equal = bisect.bisect_right(present, v) - less
        out.append((less + 0.5 * equal) / n)
    return out


def _passes_filters(p: dict, min_trades: int, require_profitable: bool) -> bool:
    if (p.get("trades") or 0) < min_trades:
        return False
    if p.get("forward_result") is None or p.get("back_result") is None:
        return False
    if require_profitable:
        pf = p.get("profit_factor")
        profit = p.get("profit")
        if profit is None or profit <= 0 or pf is None or pf <= 1.0:
            return False
    return True


def score_passes(passes: list[dict], *, weights: Weights | None = None,
                 min_trades: int = 100, require_profitable: bool = True) -> dict:
    """Score a run's passes. Pure function — no DB.

    Hard filters (applied before scoring): enough trades, a forward result, and —
    when ``require_profitable`` (default) — a genuinely profitable pass
    (profit > 0 and profit factor > 1). Percentile normalization alone rewards
    inactivity (tiny-drawdown break-even passes), so profitability is a gate.

    Returns ``{"scored": [...], "filtered_out": int, "kept": int}`` where each
    scored item carries ``robustness_score`` (0–100) and its component percentiles,
    sorted best-first. Filtered passes are reported separately with score None.
    """
    weights = weights or Weights()
    w = weights.normalized()

    kept, dropped = [], []
    for p in passes:
        (kept if _passes_filters(p, min_trades, require_profitable) else dropped).append(p)

    ranks = {c: _percentile_ranks([_raw_value(p, c) for p in kept]) for c in COMPONENTS}

    scored = []
    for i, p in enumerate(kept):
        comps = {c: ranks[c][i] for c in COMPONENTS}
        score = sum(w[c] * (comps[c] or 0.0) for c in COMPONENTS)
        scored.append({
            "id": p.get("id"),
            "pass_no": p.get("pass_no"),
            "robustness_score": round(score * 100, 2),
            "components": {c: (None if comps[c] is None else round(comps[c], 3))
                           for c in COMPONENTS},
            "profit_factor": p.get("profit_factor"),
            "profit": p.get("profit"),
            "equity_dd_pct": p.get("equity_dd_pct"),
            "back_result": p.get("back_result"),
            "forward_result": p.get("forward_result"),
            "trades": p.get("trades"),
        })
    scored.sort(key=lambda r: r["robustness_score"], reverse=True)
    return {"scored": scored, "kept": len(kept), "filtered_out": len(dropped),
            "dropped_ids": [p.get("id") for p in dropped]}


def score_run(db, run_id: int, *, weights: Weights | None = None,
              min_trades: int = 100, require_profitable: bool = True,
              top: int = 25, propfirm=None, prop_only: bool = False) -> dict:
    """Score a persisted run: writes ``robustness_score`` back to every Pass.

    ``propfirm`` (a ``PropFirmProfile``) annotates each scored pass with
    ``prop_safe`` / ``prop_reasons``; ``prop_only`` filters the returned top to
    prop-safe passes. Neither affects the persisted robustness scores.
    """
    from app import models

    rows = db.query(models.Pass).filter(models.Pass.run_id == run_id).all()
    passes = [{
        "id": r.id, "pass_no": r.pass_no, "back_result": r.back_result,
        "forward_result": r.forward_result, "profit": r.profit,
        "profit_factor": r.profit_factor, "equity_dd_pct": r.equity_dd_pct,
        "trades": r.trades,
    } for r in rows]

    result = score_passes(passes, weights=weights, min_trades=min_trades,
                          require_profitable=require_profitable)

    updates = [{"id": s["id"], "robustness_score": s["robustness_score"]}
               for s in result["scored"]]
    updates += [{"id": pid, "robustness_score": None} for pid in result["dropped_ids"]]
    db.bulk_update_mappings(models.Pass, updates)
    db.commit()

    scored = result["scored"]
    prop_safe = None
    if propfirm is not None:
        from app.propfirm.rules import evaluate
        prop_safe = 0
        for s in scored:
            ev = evaluate(s, propfirm)
            s["prop_safe"] = ev["safe"]
            s["prop_reasons"] = ev["reasons"]
            prop_safe += ev["safe"]
        pool = [s for s in scored if s["prop_safe"]] if prop_only else scored
    else:
        pool = scored

    return {
        "run_id": run_id, "kept": result["kept"],
        "filtered_out": result["filtered_out"],
        "weights": (weights or Weights()).normalized(),
        "min_trades": min_trades,
        "prop_safe": prop_safe,
        "top": pool[:top],
    }
