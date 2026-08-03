"""Prop-firm evaluation tests (pure rules + scored-endpoint integration)."""
from __future__ import annotations

import warnings

import pytest
from fastapi.testclient import TestClient

from app.propfirm.rules import PropFirmProfile, evaluate

warnings.filterwarnings("ignore")


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_dd_over_limit_fails():
    r = evaluate({"equity_dd_pct": 12.0, "trades": 200, "profit_factor": 2},
                 PropFirmProfile(max_total_dd_pct=10))
    assert r["safe"] is False
    assert any("DD" in reason for reason in r["reasons"])


def test_within_dd_limit_is_safe():
    r = evaluate({"equity_dd_pct": 6.0, "trades": 200, "profit_factor": 2},
                 PropFirmProfile(max_total_dd_pct=10))
    assert r["safe"] is True and r["reasons"] == []


def test_profit_target_requires_account_size():
    prof = PropFirmProfile(max_total_dd_pct=10, profit_target_pct=8, account_size=100000)
    assert evaluate({"equity_dd_pct": 5, "profit": 5000}, prof)["safe"] is False  # 5% < 8%
    assert evaluate({"equity_dd_pct": 5, "profit": 9000}, prof)["safe"] is True   # 9% ok


def test_min_trades_and_profit_factor():
    prof = PropFirmProfile(max_total_dd_pct=10, min_trades=100, min_profit_factor=1.2)
    r = evaluate({"equity_dd_pct": 5, "trades": 50, "profit_factor": 1.0}, prof)
    assert r["safe"] is False and len(r["reasons"]) == 2


def test_score_endpoint_annotates_and_filters_propfirm(client):
    from app.db import SessionLocal
    from app import models
    db = SessionLocal()
    ea = models.EA(name="PF"); db.add(ea); db.flush()
    run = models.Run(ea_id=ea.id); db.add(run); db.flush()

    def mk(no, dd, pf, profit, trades, back, fwd):
        return models.Pass(run_id=run.id, pass_no=no, equity_dd_pct=dd,
                           profit_factor=pf, profit=profit, trades=trades,
                           back_result=back, forward_result=fwd, inputs_json={})

    db.add_all([mk(1, 5, 2.0, 1000, 200, 50, 45),    # safe
                mk(2, 15, 3.0, 2000, 200, 60, 55),   # DD 15% -> unsafe
                mk(3, 8, 1.5, 500, 150, 40, 38)])    # safe
    db.commit()
    rid = run.id
    db.close()

    r = client.post(f"/api/runs/{rid}/score",
                    json={"min_trades": 100, "top": 50, "propfirm": {"max_total_dd_pct": 10}})
    d = r.json()
    assert d["prop_safe"] == 2
    byno = {s["pass_no"]: s for s in d["top"]}
    assert byno[2]["prop_safe"] is False
    assert any("DD" in reason for reason in byno[2]["prop_reasons"])
    assert byno[1]["prop_safe"] is True

    r2 = client.post(f"/api/runs/{rid}/score",
                     json={"min_trades": 100, "top": 50,
                           "propfirm": {"max_total_dd_pct": 10, "only": True}})
    nos = {s["pass_no"] for s in r2.json()["top"]}
    assert 2 not in nos and 1 in nos and 3 in nos
