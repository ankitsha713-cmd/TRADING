"""Dependency-free domain types shared across the assistant.

Kept free of SQLAlchemy/FastAPI imports so pure business logic (market
inference, watchlist rotation) stays unit-testable without the optional
``assistant`` dependency group installed.
"""

from enum import Enum


class Market(str, Enum):
    US = "us"
    INDIA = "india"
    CRYPTO = "crypto"


class Tier(str, Enum):
    DAILY = "daily"      # full run every scheduled market day
    WEEKLY = "weekly"    # demoted: runs only on the first market day of the week
    PAUSED = "paused"    # manually excluded from all scheduled runs


# Ratings produced by tradingagents.graph.signal_processing.process_signal.
ACTIONABLE_RATINGS = frozenset({"Buy", "Overweight", "Underweight", "Sell"})
HOLD_RATING = "Hold"


def infer_market(symbol: str) -> Market:
    """Classify a Yahoo Finance symbol into a scheduling market group."""
    upper = symbol.upper()
    if upper.endswith((".NS", ".BO")):
        return Market.INDIA
    if upper.endswith("-USD"):
        return Market.CRYPTO
    return Market.US


def infer_asset_type(symbol: str) -> str:
    """Asset type as expected by TradingAgentsGraph.propagate()."""
    return "crypto" if infer_market(symbol) is Market.CRYPTO else "stock"
