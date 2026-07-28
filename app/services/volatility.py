"""Per-ticker volatility and the thresholds scaled from it.

One number drives two safety rails: a ticker's average daily move sets both
its default stop-loss (when an analysis doesn't provide one) and its event
trigger (how big a move since the last analysis counts as "something
happened"). A flat percentage would be deaf on stable mega-caps and jumpy on
small caps; scaling by the ticker's own rhythm makes the thresholds mean the
same thing everywhere.
"""

import logging
import time

logger = logging.getLogger(__name__)

# Default stop when the analysis doesn't emit one: 2.5x daily move, 5-12%.
STOP_VOL_MULTIPLIER = 2.5
STOP_FLOOR_PCT = 5.0
STOP_CEILING_PCT = 12.0

# Event trigger (move since last analysis worth re-analyzing): 3x daily move.
EVENT_VOL_MULTIPLIER = 3.0
EVENT_FLOOR_PCT = 3.0
EVENT_CEILING_PCT = 10.0

_VOL_CACHE: dict[str, tuple[float, float]] = {}  # symbol -> (vol_pct, cached_monotonic)
_VOL_TTL_SECONDS = 24 * 3600


def clamp(value: float, floor: float, ceiling: float) -> float:
    return max(floor, min(ceiling, value))


def daily_volatility_pct_sync(symbol: str) -> float | None:
    """Average absolute daily move (percent) over the last month, cached a day."""
    cached = _VOL_CACHE.get(symbol)
    if cached and time.monotonic() - cached[1] < _VOL_TTL_SECONDS:
        return cached[0]

    import yfinance as yf

    from tradingagents.dataflows.symbol_utils import normalize_symbol

    try:
        history = yf.Ticker(normalize_symbol(symbol)).history(period="1mo")
        closes = history["Close"]
        if len(closes) < 5:
            return None
        moves = closes.pct_change().dropna().abs()
        vol = float(moves.mean()) * 100
        _VOL_CACHE[symbol] = (vol, time.monotonic())
        return vol
    except Exception:
        logger.warning("Volatility fetch failed for %s", symbol)
        return None


def default_stop_pct(vol_pct: float | None) -> float:
    """Stop distance (percent below entry) from a daily-volatility figure."""
    if vol_pct is None:
        return STOP_FLOOR_PCT * 2  # unknown rhythm: be conservative (10%)
    return clamp(vol_pct * STOP_VOL_MULTIPLIER, STOP_FLOOR_PCT, STOP_CEILING_PCT)


def event_threshold_pct(vol_pct: float | None) -> float:
    """Move size (percent since last analysis) that flags a re-analysis."""
    if vol_pct is None:
        return 5.0
    return clamp(vol_pct * EVENT_VOL_MULTIPLIER, EVENT_FLOOR_PCT, EVENT_CEILING_PCT)
