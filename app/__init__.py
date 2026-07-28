"""Personal finance assistant service layered on top of the TradingAgents engine.

Phase 1: scheduled pre-market watchlist runs per market (US / India / crypto),
watchlist rotation to control LLM spend, and Telegram + email alerts on rating
changes. The ``tradingagents`` package stays untouched; this layer only calls
its public API (``TradingAgentsGraph.propagate`` / ``save_reports``).
"""
