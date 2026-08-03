# MT5 Optimizer Studio — Build Spec

**Status:** Draft for review · **Date:** 2026-08-02 · **Owner:** dazhawn

A universal web app that ingests MetaTrader 5 set files and optimization results (back + forward),
ranks the most **robust** parameter sets, auto-suggests **narrowed parameter ranges** to cut future
optimization time, and tracks promoted sets as a **live portfolio** (MT5 investor password + Myfxbook).

**Non-negotiables**
1. **Universal** — works for *any* EA, not just the Dark Algos.
2. **Web app** — usable from anywhere.
3. **Robustness over raw profit** — the product exists to kill curve-fit sets.
4. **Read-only everywhere** — never holds trade rights; investor passwords only.

---

## 1. System architecture

```
                          ┌────────────────────────── CLOUD ──────────────────────────┐
  Browser  ──HTTPS──▶  Web Dashboard (React/Vite)                                        │
                          │                                                              │
                          ▼                                                              │
                     FastAPI backend                                                     │
                     ├─ dialect_parser     (set files → normalized params)               │
                     ├─ optresults_ingest  (MT5 XML / .opt → passes + metrics)           │
                     ├─ scoring_engine      (robustness ranking, EA-agnostic)            │
                     ├─ range_optimizer     (sensitivity → refined set file)             │
                     ├─ portfolio           (Myfxbook poller + agent ingest endpoint)    │
                     └─ Postgres            (EAs, sets, runs, passes, accounts, snaps)   │
                          ▲                                                              │
                          │  authenticated push (account snapshots)                      │
                          └──────────────────────────────────────────────────────────────┘
                                        ▲
                                        │  investor (read-only) password, never leaves PC
             ┌──────────── USER'S WINDOWS PC ────────────┐
             │  Local Companion Agent (Python)           │
             │   MetaTrader5 bridge → MT5 terminal       │
             └───────────────────────────────────────────┘
```

Two independent input paths (never conflate them):
- **Set files** = the *search space* (inputs + ranges). Custom per-EA dialects.
- **Optimization results** = the *outcomes* (one row per pass + metrics). EA-agnostic schema from MT5.

---

## 2. The dialect engine (universality lives here)

Set files come in incompatible formats. The parser is **detect → route → normalize**.

### 2.1 Detection
1. **Encoding sniff** — BOM / null-byte pattern → UTF-16LE (Dark Moon) vs UTF-8/ANSI.
2. **Layout sniff** —
   - Vanilla MT5: presence of `Name,F=`, `Name,1=`, `Name,2=`, `Name,3=` sibling lines.
   - Inline dialect (Dark Moon): values containing `||` with a trailing `Y|N` flag.
   - Fallback: plain `key=value` with no optimization metadata.

### 2.2 Handlers (pluggable)
```python
class SetDialect(Protocol):
    name: str
    def detect(self, raw: bytes) -> float: ...        # 0..1 confidence
    def parse(self, raw: bytes) -> list[Param]: ...
    def render(self, params: list[Param]) -> bytes: ... # write back in SAME dialect
```
- `VanillaMt5Dialect` — the standard `,F/,1/,2/,3` multi-line layout.
- `InlinePipeDialect` — `key=current||start||step||stop||{Y|N}`; treats `Separo*` / all-caps
  label lines as `kind="divider"|"label"`, plain strings (e.g. URLs) as `kind="string"`.
- Registry picks the highest-confidence handler; unknown → parse as read-only labels + warn.

### 2.3 Normalized model (everything downstream speaks only this)
```python
@dataclass
class Param:
    name: str
    kind: Literal["number","bool","enum","string","label","divider"]
    current: float | bool | str | None
    start: float | None
    step: float | None
    stop: float | None
    optimize: bool          # Y flag
    raw_line: str           # preserved for lossless round-trip
```
Round-trip guarantee: `render(parse(x))` reproduces `x` byte-for-byte for untouched params, so
refined files stay loadable by the original EA.

**Acceptance:** point it at the whole Google Drive "Dark Trading Algos" folder → every EA's inputs
come out structured, `optimize` flags correct, and re-rendered files diff-clean.

---

## 3. Optimization results ingestion

### 3.1 Sources
- **Primary — MT5 XML export.** Strategy Tester → Optimization Results tab → right-click → Export to
  XML. Ingest back-tab and forward-tab. Columns → `Pass`. (Documented, stable across builds.)
- **Later — `.opt` binary cache** in `<TerminalData>\Tester\cache\`. Reverse-engineered layout;
  behind a feature flag because it can break across MT5 builds. ("Both / decide later.")

### 3.2 Pass record
```
pass(id, run_id, pass_no, segment[back|forward],
     profit, profit_factor, recovery_factor, sharpe, expected_payoff,
     equity_dd_pct, trades, custom_criterion, inputs_json)
```
`inputs_json` = the exact input vector for that pass, so we can locate it in parameter space.

---

## 4. Scoring engine (EA-agnostic robustness)

Input: all passes for a run (with forward enabled). Output: ranked sets + a 0–100 **Robustness Score**.

**Hard filters (drop before scoring):**
- `trades < MinimumTrades` (read from the set file; default 100).
- `equity_dd_pct > user_max_dd`.
- Forward segment missing (if forward-required mode is on).

**Component scores (each normalized 0–1 across the run):**
| Component | Why |
|---|---|
| Back↔Forward consistency | forward metric ÷ back metric, clipped — punishes IS-only winners |
| Plateau strength | mean performance of the pass's *neighbors* in parameter space (grid-adjacent inputs); a lone spike scores low |
| Profit Factor | classic edge measure |
| Recovery Factor | profit vs pain |
| Sharpe | risk-adjusted |
| Max equity DD% (inverted) | survivability |

`Robustness = Σ weight_i · component_i` (weights user-tunable, sensible defaults). Consistency and
plateau are weighted highest — they're what a human staring at the grid cannot compute.

**Plateau detection:** treat each optimized input as an axis; for a candidate pass, gather passes
within ±1 grid step on each axis; score = candidate metric × (neighbor mean ÷ candidate). Reward
regions, penalize peaks.

**Acceptance:** on a real Dark Moon optimization XML, top-ranked sets are visibly in stable regions
with healthy forward numbers, not the single highest-profit spike.

---

## 5. Range optimizer (cut future test time)

Loop the user runs between MT5 optimization passes:
1. **Sensitivity analysis** — for each optimized input, measure how much it moves the Robustness Score
   (variance / gradient across passes). Rank inputs by impact.
2. **Prune** — inputs with negligible impact get pinned to their best value and flagged `optimize=N`.
3. **Narrow** — surviving inputs get ranges tightened around the winning plateau (e.g. ±2 steps),
   step size optionally refined.
4. **Emit** — `range_optimizer` calls `dialect.render()` to write a new `.set` **in the original
   dialect/encoding**, ready to drop straight back into MT5.

This is where the "cut testing time" promise is paid: fewer optimized inputs + tighter ranges =
combinatorial collapse of the MT5 search space.

**Acceptance:** given a coarse run, produce a refined set file whose search space is dramatically
smaller while still covering the robust region.

---

## 6. Portfolio tracking

Two sources, unified into `account_snapshot(account_id, ts, equity, balance, dd_pct, open_positions_json)`.

### 6.1 Myfxbook (cloud-side, no local footprint)
Poller: login → session → fetch gain/DD/open trades on a schedule. Stores snapshots. Best for
accounts you can't attach a terminal to.

### 6.2 MT5 investor password (via local agent — the web-app constraint)
The `MetaTrader5` Python bridge only talks to a **local** terminal, so the cloud cannot reach MT5
directly. Therefore:
- **Local Companion Agent** (small Python service on the user's Windows PC): `mt5.initialize()` with
  **investor (read-only) password**, reads `account_info` / `positions_get` / `history_deals_get`,
  and **pushes** snapshots to the cloud.
- **Agent protocol:** agent holds a per-user API token; POSTs signed snapshots to
  `/api/agent/snapshot`. Credentials never leave the PC. Agent has zero trade capability by design.
- **Fallback:** users who won't run the agent get Myfxbook-only live tracking.

Promoted sets (chosen via the scoring engine) get linked to a live account so the dashboard shows
"is this set actually holding up live vs. its backtest?"

---

## 7. Data model (Postgres, multi-EA from day one)

```
ea(id, name, source_path, dialect)
set_file(id, ea_id, name, dialect, encoding, uploaded_at, raw_blob)
param(id, set_file_id, name, kind, current, start, step, stop, optimize, raw_line)
run(id, ea_id, set_file_id, symbol, timeframe, date_from, date_to, forward_from, imported_at)
pass(id, run_id, pass_no, segment, profit, profit_factor, recovery_factor, sharpe,
     expected_payoff, equity_dd_pct, trades, custom_criterion, inputs_json, robustness_score)
account(id, kind[mt5|myfxbook], label, ext_ref, linked_pass_id)
account_snapshot(id, account_id, ts, equity, balance, dd_pct, open_positions_json)
user(id, email, api_token_hash)
```

---

## 8. API surface (FastAPI)

```
POST /api/setfiles                 upload/parse a set file → normalized params
GET  /api/eas                      list EAs + counts
POST /api/runs/import              ingest MT5 optimization XML (back+forward)
GET  /api/runs/{id}/leaderboard    ranked passes by robustness (filters/weights as query)
POST /api/runs/{id}/refine         → refined set file (download, original dialect)
GET  /api/accounts                 tracked accounts + latest snapshot
POST /api/agent/snapshot           (agent-only, token-auth) push MT5 snapshot
GET  /api/accounts/{id}/equity     equity/DD time series
```

---

## 9. Security

- Investor (read-only) passwords only; app never requests or stores master passwords; no trade rights anywhere.
- MT5 creds never leave the user's PC — only snapshots are pushed.
- Secrets encrypted at rest; agent auth via per-user token; TLS everywhere.
- Myfxbook session handled server-side, credentials encrypted.

---

## 10. Tech stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy, Postgres, pandas/numpy (scoring).
- **Agent:** Python, `MetaTrader5`, httpx. Packaged as a small Windows executable.
- **Frontend:** React + Vite; tables + plateau heatmap + equity charts.
- Matches the user's existing FastAPI work (Smart Investor).

---

## 11. Milestones & acceptance

| Phase | Deliverable | Done when |
|---|---|---|
| 1 | Dialect engine + normalized schema | Whole Drive folder parses; round-trip diff-clean |
| 2 | Results ingestion (XML) | Query passes by any metric with min-trades filter |
| 3 | Scoring engine | Ranked robust sets; top picks sit on plateaus w/ healthy forward |
| 4 | Range optimizer | Emits smaller refined set file in original dialect |
| 5 | Portfolio (Myfxbook + agent) | Live equity/DD for a linked promoted set |
| 6 | Polish | Dashboard UX, DD-breach alerts, export |

**Open questions for review**
- Preferred cloud host (affects Postgres + agent endpoint setup)?
- Default robustness weights, or expose sliders from day one?
- `.opt` binary reading — Phase 2 stretch or defer to post-v1?
