"""SQLite journal — every decision, verdict, order, and daily P&L snapshot."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("GA_JOURNAL_DB", Path(__file__).resolve().parent.parent / "results" / "autonomous_journal.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL,
    approved INTEGER NOT NULL,
    verdict_reasons TEXT,
    notional REAL,
    order_id TEXT,
    order_status TEXT,
    error TEXT,
    report_md TEXT
);
CREATE TABLE IF NOT EXISTS daily_pnl (
    day TEXT PRIMARY KEY,
    equity REAL,
    realized_pct REAL
);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def log_run(
    ticker: str,
    trade_date: str,
    action: str,
    confidence: float | None,
    approved: bool,
    verdict_reasons: list[str],
    notional: float,
    order_id: str | None = None,
    order_status: str | None = None,
    error: str | None = None,
    report_md: str | None = None,
) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO runs (ts, ticker, trade_date, action, confidence, approved, verdict_reasons, notional, order_id, order_status, error, report_md) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                ticker,
                trade_date,
                action,
                confidence,
                int(approved),
                json.dumps(verdict_reasons),
                notional,
                order_id,
                order_status,
                error,
                report_md,
            ),
        )


def log_daily_pnl(day: str, equity: float, realized_pct: float) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO daily_pnl (day, equity, realized_pct) VALUES (?,?,?) "
            "ON CONFLICT(day) DO UPDATE SET equity=excluded.equity, realized_pct=excluded.realized_pct",
            (day, equity, realized_pct),
        )


def daily_summary(days: int = 7) -> list[dict]:
    with _conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT day, equity, realized_pct FROM daily_pnl ORDER BY day DESC LIMIT ?", (days,)
        ).fetchall()
        return [dict(r) for r in rows]


def recent_runs(limit: int = 20) -> list[dict]:
    with _conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def today() -> str:
    return date.today().isoformat()
