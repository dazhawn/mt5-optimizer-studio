# MT5 Optimizer Studio

Universal MetaTrader 5 set-file analyzer, robustness ranker, and portfolio tracker.
Reads MT5 set files + optimization results (back + forward), ranks the most *robust*
parameter sets, auto-suggests narrowed ranges to cut future optimization time, and
tracks promoted sets as a live portfolio.

Full design: [docs/BUILD_SPEC.md](docs/BUILD_SPEC.md).

## Status

**Phase 1 — dialect engine + schema + API skeleton — complete.**
- Format-detecting, pluggable dialect engine (`app/dialects/`) with **byte-exact round-trip**.
- Validated against a real Dark Moon set file (UTF-16LE, `inline_pipe` dialect).
- SQLAlchemy schema (`app/models.py`) — multi-EA from day one.
- FastAPI upload endpoint (`POST /api/setfiles`) → normalized JSON + optional persist.

## Layout

```
app/
  dialects/        format engine: base model, inline_pipe, vanilla_mt5, plain_kv, registry
  models.py        SQLAlchemy schema (EA, SetFile, Param, Run, Pass, Account, Snapshot)
  db.py            engine/session (SQLite dev, Postgres prod via DATABASE_URL)
  api/setfiles.py  upload/parse endpoint
  main.py          FastAPI app
scripts/
  parse_setfile.py CLI proof: parse a .set and print its normalized structure
tests/
  test_dialects.py round-trip + structure tests
  fixtures/        real sample set files
```

## Quickstart

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pytest -q                                   # tests
.venv/Scripts/python -m scripts.parse_setfile tests/fixtures/dark_moon_v1.set  # proof CLI
.venv/Scripts/python -m uvicorn app.main:app --reload               # API at :8000
```

## Adding a new EA format

Implement the `SetDialect` protocol (`detect` / `parse` / `render`) and append an
instance to `_REGISTRY` in `app/dialects/__init__.py`. The round-trip test is the
bar: `render(parse(raw)) == raw` for any file the dialect claims.

## Roadmap

| Phase | Scope | State |
|---|---|---|
| 1 | Dialect engine + schema + API skeleton | ✅ done |
| 2 | Optimization results ingestion (MT5 XML) | next |
| 3 | Robustness scoring (consistency + plateau) | |
| 4 | Range optimizer (emit narrowed set file) | |
| 5 | Portfolio (Myfxbook poller + local MT5 agent) | |
| 6 | Dashboard polish + alerts | |
