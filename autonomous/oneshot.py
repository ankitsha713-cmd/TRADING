"""Runner mode 2: one-shot CLI — run once now, or print reports.

Usage:
    python -m autonomous.oneshot run NVDA 2026-09-23     # decide+execute one ticker
    python -m autonomous.oneshot run-all                 # all configured tickers
    python -m autonomous.oneshot report                  # recent runs + P&L table
"""
from __future__ import annotations

import os
import sys

from rich.console import Console

from autonomous.engine import AutonomousEngine
from autonomous import journal
from autonomous.reporting import print_pnl_table

console = Console()


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "run":
        if len(sys.argv) < 3:
            print("usage: python -m autonomous.oneshot run TICKER [YYYY-MM-DD]")
            sys.exit(1)
        ticker = sys.argv[2].upper()
        trade_date = sys.argv[3] if len(sys.argv) > 3 else journal.today()
        engine = AutonomousEngine()
        engine.run_ticker(ticker, trade_date)

    elif cmd == "run-all":
        trade_date = journal.today()
        tickers = [t.strip().upper() for t in os.getenv("GA_TICKERS", "AAPL,MSFT,NVDA").split(",") if t.strip()]
        engine = AutonomousEngine()
        for t in tickers:
            engine.run_ticker(t, trade_date)

    elif cmd == "report":
        runs = journal.recent_runs()
        console.rule("Recent Runs")
        for r in runs:
            status = "✅" if r["approved"] else "🚫"
            err = f" — {r['error']}" if r.get("error") else ""
            console.print(f"{status} {r['ts'][:16]}  {r['ticker']:<6} {r['action']:<5} conf={r['confidence']} notional=${r['notional'] or 0:,.0f} order={r['order_status']}{err}")
        print_pnl_table(journal.daily_summary())

    else:
        print(f"unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
