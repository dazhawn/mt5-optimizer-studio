"""Portfolio tests: Myfxbook normalization, agent ingest, equity series."""
from __future__ import annotations

import warnings

import pytest
from fastapi.testclient import TestClient

from app.portfolio import myfxbook

warnings.filterwarnings("ignore")


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_normalize_account():
    acc = {"id": 12345, "name": "ArchAngel Live", "equity": "10250.5",
           "balance": "10000", "drawdown": "3.2", "gain": "2.5",
           "currency": "USD", "demo": False}
    n = myfxbook.normalize_account(acc)
    assert n["ext_ref"] == "12345"
    assert n["label"] == "ArchAngel Live"
    assert n["equity"] == 10250.5
    assert n["dd_pct"] == 3.2
    assert n["demo"] is False


def test_normalize_account_handles_missing_fields():
    n = myfxbook.normalize_account({"id": 7})
    assert n["ext_ref"] == "7"
    assert n["label"] == "account 7"
    assert n["equity"] is None and n["dd_pct"] is None


def test_agent_snapshot_requires_token(client, monkeypatch):
    monkeypatch.setenv("MT5_AGENT_TOKEN", "secret123")
    r = client.post("/api/agent/snapshot",
                    json={"account_login": "111", "equity": 100},
                    headers={"x-agent-token": "WRONG"})
    assert r.status_code == 401


def test_agent_snapshot_flow_and_equity(client, monkeypatch):
    monkeypatch.setenv("MT5_AGENT_TOKEN", "secret123")
    hdr = {"x-agent-token": "secret123"}
    # two snapshots for the same account login -> one account, two points
    r1 = client.post("/api/agent/snapshot", headers=hdr, json={
        "account_login": "5551234", "name": "Demo", "equity": 10000,
        "balance": 10000, "dd_pct": 0.0, "open_positions": []})
    assert r1.status_code == 200
    acct_id = r1.json()["account_id"]
    client.post("/api/agent/snapshot", headers=hdr, json={
        "account_login": "5551234", "equity": 10250, "balance": 10000,
        "dd_pct": 1.5, "open_positions": [{"symbol": "XAGUSD"}]})

    accounts = client.get("/api/accounts").json()
    mine = [a for a in accounts if a["id"] == acct_id][0]
    assert mine["kind"] == "mt5" and mine["ext_ref"] == "5551234"
    assert mine["latest"]["equity"] == 10250      # most recent wins

    series = client.get(f"/api/accounts/{acct_id}/equity").json()
    assert [p["equity"] for p in series] == [10000, 10250]


def test_link_and_unlink_pass_to_account(client, monkeypatch):
    monkeypatch.setenv("MT5_AGENT_TOKEN", "secret123")
    r = client.post("/api/agent/snapshot", headers={"x-agent-token": "secret123"},
                    json={"account_login": "LINKTEST", "equity": 1000})
    acc_id = r.json()["account_id"]

    # seed a run + pass directly in the (isolated) test DB
    from app.db import SessionLocal
    from app import models
    db = SessionLocal()
    ea = models.EA(name="T"); db.add(ea); db.flush()
    run = models.Run(ea_id=ea.id); db.add(run); db.flush()
    p = models.Pass(run_id=run.id, pass_no=42, profit_factor=6.55,
                    equity_dd_pct=9.0, robustness_score=88.0, back_result=50,
                    forward_result=45, trades=200, inputs_json={})
    db.add(p); db.commit()
    pid = p.id
    db.close()

    r2 = client.post(f"/api/accounts/{acc_id}/link", json={"pass_id": pid})
    assert r2.status_code == 200 and r2.json()["linked_pass_id"] == pid

    acc = [a for a in client.get("/api/accounts").json() if a["id"] == acc_id][0]
    assert acc["linked"]["pass_no"] == 42
    assert acc["linked"]["profit_factor"] == 6.55

    # link to a non-existent pass -> 404
    assert client.post(f"/api/accounts/{acc_id}/link",
                       json={"pass_id": 999999}).status_code == 404

    # unlink
    r3 = client.post(f"/api/accounts/{acc_id}/link", json={"pass_id": None})
    assert r3.json()["linked_pass_id"] is None
    acc2 = [a for a in client.get("/api/accounts").json() if a["id"] == acc_id][0]
    assert acc2["linked"] is None


def test_parse_daily_flattens_and_computes_equity():
    daily = [
        [{"date": "01/01/2026", "balance": "10000", "floatingPL": "25"}],
        [{"date": "01/03/2026", "balance": "10200", "floatingPL": "0"}],
    ]
    pts = myfxbook.parse_daily(daily)
    assert [p["balance"] for p in pts] == [10000.0, 10200.0]
    assert pts[0]["equity"] == 10025.0                       # balance + floatingPL
    assert (pts[0]["ts"].year, pts[0]["ts"].month, pts[0]["ts"].day) == (2026, 1, 1)


def test_myfxbook_sync_backfills_history_and_is_idempotent(client, monkeypatch):
    monkeypatch.setattr(myfxbook, "login", lambda e, p, client=None: "sess")
    monkeypatch.setattr(myfxbook, "logout", lambda s, client=None: None)
    monkeypatch.setattr(myfxbook, "get_accounts", lambda s, client=None: [
        {"id": 900, "name": "Public Copy", "equity": 5100, "balance": 5000,
         "drawdown": 4.0, "gain": 2.0}])
    monkeypatch.setattr(myfxbook, "get_data_daily", lambda s, i, st, en, client=None: [
        [{"date": "01/01/2026", "balance": 5000, "floatingPL": 50}],
        [{"date": "01/02/2026", "balance": 5100, "floatingPL": 0}]])

    r1 = client.post("/api/portfolio/myfxbook/sync",
                     json={"email": "x@y.com", "password": "pw", "days": 365})
    assert r1.status_code == 200
    assert r1.json()["accounts"][0]["history_points"] == 2   # both daily points

    accts = client.get("/api/accounts").json()
    acc = [a for a in accts if a["kind"] == "myfxbook" and a["ext_ref"] == "900"][0]
    assert acc["latest"]["dd_pct"] == 4.0                    # live current snapshot wins

    series = client.get(f"/api/accounts/{acc['id']}/equity").json()
    assert len(series) == 3                                   # 2 history + 1 current

    # Re-sync: history already stored -> not duplicated (only a new current point).
    r2 = client.post("/api/portfolio/myfxbook/sync",
                     json={"email": "x@y.com", "password": "pw", "days": 365})
    assert r2.json()["accounts"][0]["history_points"] == 0
    assert len(client.get(f"/api/accounts/{acc['id']}/equity").json()) == 4
