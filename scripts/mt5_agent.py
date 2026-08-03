"""MT5 companion agent — runs on the machine with the MT5 terminal (your VPS).

The MetaTrader5 package only talks to a terminal on the SAME machine, so deploy
this on the VPS. It reads account + open positions and pushes snapshots to MT5
Optimizer Studio. It has **no trade capability** and no credentials leave the box.

Two modes:

  ATTACH (recommended on a VPS) — the terminal is already running and logged in,
  so just attach to it. No login or password needed at all:
      pip install MetaTrader5 httpx
      set STUDIO_URL=https://your-studio-host        # must be reachable from the VPS
      set MT5_AGENT_TOKEN=<same token the server has>
      python -m scripts.mt5_agent --interval 60

  LOGIN — connect explicitly with the account's INVESTOR (read-only) password
  (use when the terminal isn't already logged into the account you want):
      set MT5_LOGIN=12345678
      set MT5_PASSWORD=<INVESTOR (read-only) password>
      set MT5_SERVER=PUPrime-Demo
      set MT5_TERMINAL_PATH=C:/Program Files/MetaTrader 5/terminal64.exe   # optional

STUDIO_URL must be reachable from the VPS — localhost only works if the studio
runs on the VPS too; otherwise host it somewhere the VPS can hit over HTTPS.
"""
from __future__ import annotations

import argparse
import os
import time

import httpx

try:
    import MetaTrader5 as mt5  # Windows-only; talks to a local terminal
except Exception:  # pragma: no cover - only importable where MT5 is installed
    mt5 = None


def read_snapshot(*, login: str | None = None, password: str | None = None,
                  server: str | None = None, terminal_path: str | None = None) -> dict:
    if mt5 is None:
        raise RuntimeError("MetaTrader5 package not installed (Windows + MT5 terminal required)")
    # ATTACH mode when no login is given: connect to the already-running terminal.
    init_kwargs: dict = {}
    if terminal_path:
        init_kwargs["path"] = terminal_path
    if login:
        init_kwargs.update(login=int(login), password=password, server=server)
    if not mt5.initialize(**init_kwargs):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        ai = mt5.account_info()
        if ai is None:
            raise RuntimeError(f"account_info failed: {mt5.last_error()}")
        positions = mt5.positions_get() or []
        # Drawdown from the peak balance we can see this session (simple proxy).
        dd_pct = None
        if ai.balance:
            dd_pct = max(0.0, (ai.balance - ai.equity) / ai.balance * 100.0)
        return {
            "account_login": str(ai.login),
            "name": f"{ai.name} ({ai.server})",
            "equity": ai.equity,
            "balance": ai.balance,
            "dd_pct": dd_pct,
            "open_positions": [
                {"symbol": p.symbol, "type": int(p.type), "volume": p.volume,
                 "profit": p.profit, "price_open": p.price_open}
                for p in positions
            ],
        }
    finally:
        mt5.shutdown()


def push(studio_url: str, token: str, snapshot: dict) -> dict:
    r = httpx.post(f"{studio_url}/api/agent/snapshot", json=snapshot,
                   headers={"x-agent-token": token}, timeout=20)
    r.raise_for_status()
    return r.json()


def run_once() -> dict:
    studio = os.getenv("STUDIO_URL", "http://localhost:8137")
    token = os.environ["MT5_AGENT_TOKEN"]
    snap = read_snapshot(
        login=os.getenv("MT5_LOGIN"),          # unset -> ATTACH to running terminal
        password=os.getenv("MT5_PASSWORD"),
        server=os.getenv("MT5_SERVER"),
        terminal_path=os.getenv("MT5_TERMINAL_PATH"),
    )
    result = push(studio, token, snap)
    print(f"pushed: equity={snap['equity']} balance={snap['balance']} "
          f"open={len(snap['open_positions'])} -> {result}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=0,
                    help="seconds between pushes (0 = run once)")
    args = ap.parse_args()
    if args.interval <= 0:
        run_once()
        return 0
    while True:
        try:
            run_once()
        except Exception as e:  # keep the agent alive across transient errors
            print(f"error: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
