"""Runner mode 1: daemon — runs the full loop on a schedule while you sleep.

Usage:
    python -m autonomous.daemon

Env:
    GA_TICKERS            comma list (default "AAPL,MSFT,NVDA")
    GA_INTERVAL_MIN       minutes between cycles (default 60)
    GA_MARKET_HOURS_ONLY  "1" skips cycles outside 9:30-16:00 ET (default 1)
"""
from __future__ import annotations

import os
import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from rich.console import Console

from autonomous.engine import AutonomousEngine
from autonomous import journal

console = Console()

ET = ZoneInfo("America/New_York")


def market_open() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    return dtime(9, 30) <= now.time() <= dtime(16, 0)


def main() -> None:
    tickers = [t.strip().upper() for t in os.getenv("GA_TICKERS", "AAPL,MSFT,NVDA").split(",") if t.strip()]
    interval_min = int(os.getenv("GA_INTERVAL_MIN", "60"))
    hours_only = os.getenv("GA_MARKET_HOURS_ONLY", "1") == "1"

    console.print(f"[bold green]Autonomous daemon started[/bold green] tickers={tickers} interval={interval_min}min market_hours_only={hours_only}")

    while True:
        if hours_only and not market_open():
            console.print(f"[dim]{datetime.now(ET):%H:%M} ET — market closed, sleeping[/dim]")
        else:
            trade_date = journal.today()
            for t in tickers:
                engine = AutonomousEngine()
                engine.run_ticker(t, trade_date)
            # snapshot equity for P&L reporting
            try:
                acct = engine.broker.get_account()
                journal.log_daily_pnl(trade_date, float(acct.get("equity", 0)), 0.0)
            except Exception as e:
                console.print(f"[red]P&L snapshot failed:[/red] {e}")

        time.sleep(interval_min * 60)


if __name__ == "__main__":
    main()
