"""Outbound notifications: Telegram alerts and per-run email digests.

Send-only by design (no Telegram webhook/polling in Phase 1), so the only
security surface is the outbound HTTPS call with your own bot token. Failures
are logged, never raised — a notification hiccup must not kill a run.
"""

import html
import logging
from email.mime.text import MIMEText

import aiosmtplib
import httpx

from app.core.config import AssistantSettings
from app.domain import Market

logger = logging.getLogger(__name__)

RATING_EMOJI = {
    "Buy": "🟢",
    "Overweight": "📈",
    "Hold": "⚪",
    "Underweight": "📉",
    "Sell": "🔴",
}

_SNIPPET_CHARS = 700


class Notifier:
    def __init__(self, settings: AssistantSettings):
        self._settings = settings

    # --- Telegram ---

    async def send_telegram(self, text: str) -> None:
        if not self._settings.telegram_enabled:
            logger.info("Telegram not configured; skipping alert")
            return
        url = f"https://api.telegram.org/bot{self._settings.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self._settings.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
        except httpx.HTTPError:
            logger.exception("Telegram send failed")

    async def alert_rating_change(
        self,
        symbol: str,
        market: Market,
        trade_date: str,
        prev_rating: str | None,
        rating: str,
        decision_text: str | None,
        holding_note: str | None = None,
    ) -> None:
        emoji = RATING_EMOJI.get(rating, "ℹ️")
        was = f" (was {html.escape(prev_rating)})" if prev_rating else " (first analysis)"
        lines = [
            f"{emoji} <b>{html.escape(symbol)}</b> — <b>{html.escape(rating)}</b>{was}",
            f"{market.value.upper()} · {trade_date}",
        ]
        if holding_note:
            lines.append(f"💼 {html.escape(holding_note)}")
        if decision_text:
            snippet = decision_text.strip()
            if len(snippet) > _SNIPPET_CHARS:
                snippet = snippet[:_SNIPPET_CHARS] + "…"
            lines += ["", html.escape(snippet)]
        await self.send_telegram("\n".join(lines))

    async def alert_run_error(self, symbol: str, trade_date: str, error: str) -> None:
        await self.send_telegram(
            f"⚠️ <b>{html.escape(symbol)}</b> analysis failed on {trade_date}\n"
            f"<code>{html.escape(error[:500])}</code>"
        )

    # --- Email digest ---

    async def send_digest(self, label: str, trade_date: str, rows: list[dict]) -> None:
        """One summary email per analysis batch (slot or manual run).

        Each row: {symbol, prev_rating, rating, changed, status, error, report_path}.
        """
        if not self._settings.email_enabled:
            logger.info("Email not configured; skipping digest")
            return
        if not rows:
            return

        body_rows = []
        for r in rows:
            if r["status"] == "error":
                outcome = f"❌ failed: {html.escape((r.get('error') or '')[:200])}"
            else:
                arrow = (
                    f"{html.escape(r['prev_rating'])} → " if r.get("prev_rating") else ""
                )
                mark = " 🔔" if r.get("changed") else ""
                outcome = f"{arrow}<b>{html.escape(r['rating'] or '?')}</b>{mark}"
            body_rows.append(
                f"<tr><td style='padding:4px 12px'><b>{html.escape(r['symbol'])}</b></td>"
                f"<td style='padding:4px 12px'>{outcome}</td>"
                f"<td style='padding:4px 12px;color:#666'>{html.escape(r.get('report_path') or '')}</td></tr>"
            )
        changed_count = sum(1 for r in rows if r.get("changed"))
        html_body = (
            f"<p>TradingAgents digest — <b>{html.escape(label)}</b> — {trade_date}<br>"
            f"{len(rows)} tickers analyzed, {changed_count} rating change(s).</p>"
            f"<table style='border-collapse:collapse;font-family:sans-serif'>"
            f"<tr><th align='left' style='padding:4px 12px'>Ticker</th>"
            f"<th align='left' style='padding:4px 12px'>Rating</th>"
            f"<th align='left' style='padding:4px 12px'>Report</th></tr>"
            + "".join(body_rows)
            + "</table>"
            "<p style='color:#888;font-size:12px'>Research output, not financial advice. "
            "Full reasoning is in the report folders listed above.</p>"
        )

        message = MIMEText(html_body, "html", "utf-8")
        message["From"] = self._settings.email_from or self._settings.smtp_username
        message["To"] = self._settings.email_to
        message["Subject"] = (
            f"[TradingAgents] {label} digest {trade_date} — {changed_count} change(s)"
        )
        try:
            await aiosmtplib.send(
                message,
                hostname=self._settings.smtp_host,
                port=self._settings.smtp_port,
                username=self._settings.smtp_username,
                password=self._settings.smtp_password,
                start_tls=True,
                timeout=30,
            )
        except (aiosmtplib.errors.SMTPException, OSError):
            logger.exception("Email digest send failed")
