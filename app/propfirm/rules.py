"""Prop-firm rule evaluation.

Judges whether an optimization pass could survive a prop-firm account. From the
MT5 optimization grid we can check what actually breaks accounts:

* **Max total drawdown** — the headline killer. ``equity_dd_pct`` (MT5's
  "Equity DD %") maps straight onto a firm's overall-loss limit (commonly 8-10%).
* **Profit target** — only when an account size is provided (profit % = profit /
  account_size).
* **Min trades / min profit factor** — basic quality gates.

Not checkable from the optimization grid (need each pass's full backtest report,
a later enhancement): DAILY drawdown, trading-day counts, and consistency rules.
So a "safe" verdict here means "passes the checks we can verify" — it is not a
guarantee of daily-DD or consistency compliance.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PropFirmProfile:
    name: str = "Custom"
    max_total_dd_pct: float = 10.0
    min_trades: int = 0
    min_profit_factor: float | None = None
    profit_target_pct: float | None = None   # requires account_size to check
    account_size: float | None = None


def evaluate(p: dict, profile: PropFirmProfile) -> dict:
    """Return ``{"safe": bool, "reasons": [str]}`` for one pass."""
    reasons: list[str] = []

    dd = p.get("equity_dd_pct")
    if dd is not None and dd > profile.max_total_dd_pct:
        reasons.append(f"max DD {dd:.2f}% exceeds {profile.max_total_dd_pct:g}% limit")

    trades = p.get("trades") or 0
    if profile.min_trades and trades < profile.min_trades:
        reasons.append(f"{trades} trades below {profile.min_trades} minimum")

    pf = p.get("profit_factor")
    if profile.min_profit_factor is not None and (pf is None or pf < profile.min_profit_factor):
        shown = "n/a" if pf is None else f"{pf:g}"
        reasons.append(f"profit factor {shown} below {profile.min_profit_factor:g}")

    if profile.profit_target_pct is not None and profile.account_size:
        pct = (p.get("profit") or 0.0) / profile.account_size * 100.0
        if pct < profile.profit_target_pct:
            reasons.append(f"profit {pct:.1f}% below {profile.profit_target_pct:g}% target")

    return {"safe": not reasons, "reasons": reasons}


# Starting templates — verify against your specific firm's current rules.
PRESETS: dict[str, PropFirmProfile] = {
    "standard_10": PropFirmProfile("Standard (10% max DD)", max_total_dd_pct=10.0),
    "conservative_8": PropFirmProfile("Conservative (8% max DD)",
                                      max_total_dd_pct=8.0, min_trades=50),
    "challenge_8target": PropFirmProfile("Challenge (10% DD / 8% target)",
                                         max_total_dd_pct=10.0, profit_target_pct=8.0),
}
