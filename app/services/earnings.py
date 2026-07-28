"""Earnings-calendar and analyst-consensus context (yfinance — free, verified).

Two jobs:
- Never let a review date sail blind past an earnings report: the scheduler
  clamps a ticker's next review to the day before its next earnings date.
- Give the UI and the screener cheap forward-looking context: next earnings
  date, EPS/revenue consensus, and analyst price targets.

All data comes from endpoints empirically verified working in this
environment (2026-07-07): ``Ticker.calendar`` and
``Ticker.get_analyst_price_targets()``.
"""

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple["EarningsContext | None", float]] = {}
_TTL_SECONDS = 6 * 3600


@dataclass(frozen=True)
class EarningsContext:
    symbol: str
    next_earnings_date: date | None
    eps_estimate_avg: float | None
    revenue_estimate_avg: float | None
    target_mean: float | None
    target_median: float | None
    target_high: float | None
    target_low: float | None
    current_price: float | None

    @property
    def analyst_upside_pct(self) -> float | None:
        if not self.target_mean or not self.current_price:
            return None
        return (self.target_mean - self.current_price) / self.current_price * 100

    @property
    def days_to_earnings(self) -> int | None:
        if self.next_earnings_date is None:
            return None
        return (self.next_earnings_date - date.today()).days


def _first_future_date(value) -> date | None:
    """yfinance calendar 'Earnings Date' is a list of date candidates."""
    dates = value if isinstance(value, (list, tuple)) else [value]
    today = date.today()
    future = sorted(
        d for d in dates
        if isinstance(d, (date, datetime))
        and (d.date() if isinstance(d, datetime) else d) >= today
    )
    if not future:
        return None
    first = future[0]
    return first.date() if isinstance(first, datetime) else first


def fetch_earnings_context_sync(symbol: str) -> EarningsContext | None:
    """Best-effort earnings + analyst context; cached ~6h; never raises."""
    cached = _CACHE.get(symbol)
    if cached and time.monotonic() - cached[1] < _TTL_SECONDS:
        return cached[0]

    import yfinance as yf

    from tradingagents.dataflows.symbol_utils import normalize_symbol

    context: EarningsContext | None = None
    try:
        ticker = yf.Ticker(normalize_symbol(symbol))
        calendar: dict = {}
        targets: dict = {}
        try:
            calendar = ticker.calendar or {}
        except Exception:
            logger.debug("calendar fetch failed for %s", symbol)
        try:
            targets = ticker.get_analyst_price_targets() or {}
        except Exception:
            logger.debug("analyst targets fetch failed for %s", symbol)

        if calendar or targets:
            context = EarningsContext(
                symbol=symbol.upper(),
                next_earnings_date=_first_future_date(calendar.get("Earnings Date")),
                eps_estimate_avg=calendar.get("Earnings Average"),
                revenue_estimate_avg=calendar.get("Revenue Average"),
                target_mean=targets.get("mean"),
                target_median=targets.get("median"),
                target_high=targets.get("high"),
                target_low=targets.get("low"),
                current_price=targets.get("current"),
            )
    except Exception:
        logger.warning("Earnings context fetch failed for %s", symbol)

    _CACHE[symbol] = (context, time.monotonic())
    return context


def clamp_review_to_earnings(
    review_at: datetime, now: datetime, next_earnings: date | None
) -> datetime:
    """Pull a review date forward so it lands the day BEFORE earnings.

    A thesis written before an earnings report is stale the moment the report
    drops; reviewing one day ahead of it means the position is re-underwritten
    with the report on the calendar. Reviews already scheduled before the
    earnings date are left alone. All datetimes are naive-UTC.
    """
    if next_earnings is None:
        return review_at
    pre_earnings = datetime(
        next_earnings.year, next_earnings.month, next_earnings.day
    )  # midnight UTC the day before-ish; minus one day below
    from datetime import timedelta

    pre_earnings -= timedelta(days=1)
    if now < pre_earnings < review_at:
        return pre_earnings
    return review_at
