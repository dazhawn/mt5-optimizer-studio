"""Optimization-XML ingestion tests (SpreadsheetML streaming parser)."""
from __future__ import annotations

import pathlib
import warnings

import pytest
from fastapi.testclient import TestClient

from app.ingest.optresults import iter_passes, summarize

warnings.filterwarnings("ignore")

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SAMPLE = FIXTURES / "opt_results_sample.xml"


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_background_import_job_flow(client):
    # TestClient runs BackgroundTasks synchronously, so the job is done on return.
    with open(SAMPLE, "rb") as f:
        r = client.post("/api/runs/import",
                        files={"file": ("s.xml", f, "text/xml")},
                        data={"ea_name": "JobTest", "symbol": "X", "timeframe": "M1"})
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    st = client.get(f"/api/runs/import/{job_id}").json()
    assert st["status"] == "done"
    assert st["passes"] == 4
    assert st["run_id"] is not None

    # the imported run is now usable
    runs = client.get("/api/runs").json()
    assert any(run["id"] == st["run_id"] and run["ea"] == "JobTest" for run in runs)


def test_import_status_404(client):
    assert client.get("/api/runs/import/999999").status_code == 404


def test_pass_count_excludes_header():
    passes = list(iter_passes(SAMPLE))
    assert len(passes) == 4
    assert [p["pass_no"] for p in passes] == [0, 1, 2, 3]


def test_metrics_and_inputs_parsed():
    p0 = list(iter_passes(SAMPLE))[0]
    assert p0["forward_result"] == 12.5
    assert p0["back_result"] == 30.2
    assert p0["profit"] == 5000.0
    assert p0["profit_factor"] == 1.8
    assert p0["equity_dd_pct"] == 9.35
    assert p0["trades"] == 200          # int, not float
    assert isinstance(p0["trades"], int)
    assert p0["inputs"] == {"inpAtrPeriod": 4.0, "inpPipStep": 10.0,
                            "inpTakeProfitPips": 135.0}


def test_back_only_pass_and_sparse_index_alignment():
    # Pass 1 omits the Forward cell via ss:Index="3"; forward must be None AND
    # every later column must still map to the right header (inputs intact).
    p1 = list(iter_passes(SAMPLE))[1]
    assert p1["forward_result"] is None
    assert p1["back_result"] == 28.0
    assert p1["trades"] == 150
    assert p1["inputs"]["inpAtrPeriod"] == 8.0      # alignment preserved
    assert p1["inputs"]["inpTakeProfitPips"] == 200.0


def test_summarize_leaderboards():
    s = summarize(SAMPLE, top_n=3)
    assert s["passes"] == 4
    assert s["with_forward"] == 3                    # pass 1 has no forward
    assert s["input_columns"] == ["inpAtrPeriod", "inpPipStep", "inpTakeProfitPips"]
    assert s["pass_no_range"] == [0, 3]
    # highest back_result is pass 3 (50.0); highest PF is pass 3 (8.0)
    assert s["top_by_back_result"][0] == (50.0, 3)
    assert s["top_by_profit_factor"][0] == (8.0, 3)
