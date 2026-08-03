# MT5 Optimizer Studio — Quick Start

Analyze MetaTrader 5 optimization results, surface the **robust** parameter sets
(not curve-fit), refine your search to cut future test time, check prop-firm
suitability, and track live accounts — all in one dashboard.

---

## 1. Run it

```bash
cd C:\Users\dazha\Projects\mt5-optimizer-studio
.venv/Scripts/python -m uvicorn app.main:app --port 8137
```

Open **http://localhost:8137**.

> First run creates `mt5studio.db` (SQLite) in the project folder — your imported
> runs and tracked accounts persist there.

---

## 2. The workflow

### a) Import an optimization run
1. In MT5: **Strategy Tester → Optimization Results tab → right-click → Export to XML**
   (enable **Forward** testing first for back↔forward robustness).
2. In the app's **Optimization run** panel: choose the XML, enter the **EA name**
   (symbol/TF optional), click **Import & Analyze**.
3. Already-imported runs appear in the **"select a run"** dropdown.

### b) Rank for robustness
The **leaderboard** shows the top 20 passes by a 0–100 **Robustness Score**.
Adjust the weight sliders to taste, then **Re-score**:

| Weight | What it rewards |
|---|---|
| Profit Factor | raw edge |
| Drawdown | lower max drawdown |
| Profit | total return |
| Consistency | forward result holding up vs back (curve-fit guard) |

**Profitability gate** (on by default) drops losing / break-even passes.
**Min trades** removes statistically meaningless passes (e.g. 8-trade flukes).

### c) Check prop-firm suitability
Turn on **"evaluate for prop firm"**, set **Max total DD %** (your firm's limit,
usually 8–10%). The **Prop** column flags ✓ / ✕ per pass (hover ✕ for the reason);
**"show prop-safe only"** filters to fundable sets.

> Checks max total drawdown, trades, PF. It does **not** check daily DD / trading
> days / consistency rules (those need each pass's full backtest report).

### d) Refine → smaller next optimization
In the **Refine** panel, upload the base `.set` template you optimized and click
**Refine & Download**. The refiner **pins** inputs that don't matter and
**narrows** the ones that do (around this run's top passes), then hands back a
drop-in `.set`. Drop it into MT5 for a dramatically smaller next optimization.

### e) Track live accounts (Portfolio)
- **Connect a Myfxbook account** → enter email/password → **Sync**. Pulls each
  account with backfilled daily equity history. (Credentials are sent to
  Myfxbook and **not stored**.)
- Click an account to see its **equity curve**.
- Each account shows **cur DD** (now), **max DD** (historical), and **RF**
  (recovery factor = gain ÷ max DD).
- **Link a set** dropdown: pin a leaderboard pass to the account running it →
  see `backtest max DD → live max DD` side by side.

---

## 3. Command line (optional)

```bash
# Summarize a results XML without importing
.venv/Scripts/python -m scripts.import_optxml summarize path/to/results.xml

# Import into the DB
.venv/Scripts/python -m scripts.import_optxml persist path/to/results.xml "Archangel X" --symbol XAGUSD --tf M1

# Score a run
.venv/Scripts/python -m scripts.score_run 1 --min-trades 100 --pf 0.4 --dd 0.3 --profit 0.3 --cons 0

# Refine a run into a narrowed set file
.venv/Scripts/python -m scripts.refine_run 1 --base base.set --out refined.set
```

---

## 4. Live MT5 tracking (VPS)

For real-time equity beyond Myfxbook's daily data, run the agent **on the VPS
where MT5 lives** (it attaches to the running terminal — no password needed):

```bash
pip install MetaTrader5 httpx
set STUDIO_URL=https://your-studio-url
set MT5_AGENT_TOKEN=<shared secret>
python -m scripts.mt5_agent --interval 60
```

Read-only by design — it never trades. (Requires the Studio to be reachable from
the VPS; see hosting notes.)

---

## 5. Key metrics cheat-sheet

- **Robustness Score (0–100)** — weighted, per-run-normalized rank; the app's headline.
- **Consistency (fwd/back)** — forward result ÷ back result; ~1.0 = held up out of sample.
- **Max DD** — worst peak-to-valley ever (the prop-firm killer; your risk history).
- **Cur DD** — current floating drawdown (balance → equity).
- **Recovery Factor** — profit ÷ max DD; >3 excellent, 1–3 solid, <0 net loss.

---

## 6. Troubleshooting

- **Myfxbook "Invalid session"** — fixed (session token is URL-decoded). Set
  `MYFXBOOK_DEBUG=1` in the server env for response-level logging if needed.
- **Port already in use** — a previous server is still running; start on another
  port (`--port 8138`) or kill the process holding the port.
- **Tests** — `.venv/Scripts/python -m pytest -q` (40 passing).

---

## 7. Tests

```bash
.venv/Scripts/python -m pytest -q
```
