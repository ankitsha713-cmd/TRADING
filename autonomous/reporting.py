"""Reporting: Rich CLI report + optional Discord webhook push.

Same content, two channels — you read it in the terminal or get pinged on
your phone while asleep.
"""
from __future__ import annotations

import json
import os

import requests
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()


def render_run_report(
    ticker: str,
    action: str,
    confidence: float,
    approved: bool,
    reasons: list[str],
    notional: float,
    order_status: str | None,
    error: str | None,
) -> str:
    """Builds the markdown report text shared by CLI + webhook."""
    lines = [
        f"## {ticker} — decision: **{action}** (confidence {confidence:.2f})",
        f"- Guardrail verdict: {'✅ APPROVED' if approved else '🚫 VETOED'}",
    ]
    if reasons:
        lines.append("- Reasons: " + "; ".join(reasons))
    if order_status:
        lines.append(f"- Order status: {order_status} (notional ${notional:,.2f})")
    if error:
        lines.append(f"- ⚠️ Error: {error}")
    return "\n".join(lines)


def print_cli_report(md: str) -> None:
    console.print(Panel(md, title="Autonomous Run Report", border_style="green" if "APPROVED" in md else "red"))


def print_pnl_table(days: list[dict]) -> None:
    t = Table(title="Recent Daily P&L", show_header=True)
    t.add_column("Day")
    t.add_column("Equity", justify="right")
    t.add_column("Realized %", justify="right")
    for d in days:
        pct = d.get("realized_pct") or 0.0
        color = "green" if pct >= 0 else "red"
        t.add_row(d["day"], f"${d['equity']:,.2f}", f"[{color}]{pct:+.2%}[/{color}]")
    console.print(t)


def push_discord(md: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": md[:1900]}, timeout=10)
    except requests.RequestException as e:
        console.print(f"[red]Discord push failed:[/red] {e}")
