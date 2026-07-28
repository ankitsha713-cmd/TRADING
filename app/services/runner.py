"""Runs one TradingAgents analysis synchronously (called via asyncio.to_thread).

A fresh ``TradingAgentsGraph`` is built per ticker: the graph object tracks
per-run state (current ticker, state log), so sharing one instance across a
watchlist loop risks cross-ticker bleed for the price of a cheap constructor.
"""

import logging
import time
from dataclasses import dataclass

from app.core.config import AssistantSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalysisOutcome:
    symbol: str
    trade_date: str
    rating: str | None
    decision_text: str | None
    report_path: str | None
    duration_seconds: float
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def run_analysis_sync(
    symbol: str,
    trade_date: str,
    asset_type: str,
    settings: AssistantSettings,
) -> AnalysisOutcome:
    """Execute the full multi-agent pipeline for one ticker. Never raises —
    failures come back as an ``AnalysisOutcome`` with ``error`` set so one bad
    ticker cannot abort the rest of the scheduled run.
    """
    start = time.monotonic()
    try:
        # Imported here, not at module top: pulls in langgraph + all providers,
        # which keeps app startup fast and avoids import cost in tests.
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        config = DEFAULT_CONFIG.copy()
        config["llm_provider"] = settings.assistant_llm_provider
        config["deep_think_llm"] = settings.assistant_deep_model
        config["quick_think_llm"] = settings.assistant_quick_model
        # None lets each provider resolve its own endpoint (Anthropic default
        # API, Ollama -> OLLAMA_BASE_URL or localhost:11434/v1, ...); a value
        # here pins a custom endpoint (remote Ollama, vLLM, LM Studio).
        config["backend_url"] = settings.assistant_llm_backend_url or None

        graph = TradingAgentsGraph(debug=False, config=config)
        final_state, rating = graph.propagate(symbol, trade_date, asset_type=asset_type)
        report_path = graph.save_reports(final_state, symbol)

        return AnalysisOutcome(
            symbol=symbol,
            trade_date=trade_date,
            rating=rating,
            decision_text=final_state.get("final_trade_decision"),
            report_path=str(report_path),
            duration_seconds=time.monotonic() - start,
        )
    except Exception as exc:
        logger.exception("Analysis failed for %s on %s", symbol, trade_date)
        return AnalysisOutcome(
            symbol=symbol,
            trade_date=trade_date,
            rating=None,
            decision_text=None,
            report_path=None,
            duration_seconds=time.monotonic() - start,
            error=f"{type(exc).__name__}: {exc}",
        )
