"""Pure scoring rules for the anomaly screener — no I/O, unit-testable.

The screener hunts for "hidden gems": companies whose fundamentals and
momentum are strong while market attention is still low — plus legal insider
buying (SEC Form 4 disclosures), one of the best-documented bullish signals.

Score components (0–100+):
- Fundamentals (up to 50): revenue growth, earnings growth, profit margins
- Momentum (up to 25): 3-month return, 52-week change
- Insider activity (up to 10): net open-market buying by officers/directors
- Attention adjustment (−10 to +15): bonus when demonstrably under-followed,
  penalty when already crowded; unknown attention is neutral — never punish
  a candidate for missing data.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CandidateMetrics:
    symbol: str
    market: str
    revenue_growth: float | None = None    # fraction, e.g. 0.45 = +45% YoY
    earnings_growth: float | None = None   # fraction
    profit_margins: float | None = None    # fraction
    return_3m: float | None = None         # fraction over ~90 days
    week52_change: float | None = None     # fraction over 52 weeks
    market_cap: float | None = None        # USD-ish (as reported)
    watchers: int | None = None            # StockTwits watchlist count (None = unknown)
    insider_net_shares: float | None = None  # net shares bought (buys - sells), 6 months
    analyst_upside_pct: float | None = None  # (mean target - price) / price * 100
    dilution_yoy_pct: float | None = None    # shares-outstanding growth, 1 year
    cash_runway_quarters: float | None = None  # cash / quarterly burn (None = not burning/unknown)
    insider_cluster: bool = False            # >=2 distinct insiders bought recently (EDGAR)


# Attention thresholds (StockTwits watchers). Below LOW is "under-followed";
# above HIGH the crowd has already arrived.
ATTENTION_LOW = 10_000
ATTENTION_HIGH = 150_000

# Size tilt: the mission is under-the-radar names. Small caps get a bonus;
# mega-caps are penalized — everyone already knows them, whatever their growth.
SMALL_CAP_USD = 10_000_000_000     # < $10B
MEGA_CAP_USD = 200_000_000_000     # > $200B

# A candidate must have at least one growth figure to be scoreable at all.
MIN_SCORE_TO_ADD = 55.0


def _scaled(value: float | None, cap: float, points: float) -> float:
    """Linear score: ``value`` (fraction) earns up to ``points`` at ``cap``."""
    if value is None or value <= 0:
        return 0.0
    return min(value / cap, 1.0) * points


def anomaly_score(m: CandidateMetrics) -> float | None:
    """Composite score, or None when fundamentals are too incomplete to judge."""
    if m.revenue_growth is None and m.earnings_growth is None:
        return None

    score = 0.0
    # Fundamentals — 50 pts
    score += _scaled(m.revenue_growth, cap=0.50, points=25)
    score += _scaled(m.earnings_growth, cap=0.50, points=15)
    score += _scaled(m.profit_margins, cap=0.25, points=10)
    # Momentum — 25 pts
    score += _scaled(m.return_3m, cap=0.25, points=15)
    score += _scaled(m.week52_change, cap=1.00, points=10)
    # Insider net buying — 10 pts (any meaningful net buying scores; the
    # signal is direction, not magnitude, since share counts vary wildly)
    if m.insider_net_shares is not None and m.insider_net_shares > 0:
        score += 10
    # Attention adjustment
    if m.watchers is not None:
        if m.watchers < ATTENTION_LOW:
            score += 15  # strong fundamentals nobody is talking about — the target
        elif m.watchers > ATTENTION_HIGH:
            score -= 10  # the crowd is already here
    # Size tilt
    if m.market_cap is not None:
        if m.market_cap < SMALL_CAP_USD:
            score += 10
        elif m.market_cap > MEGA_CAP_USD:
            score -= 10
    # Analyst consensus — up to 10 pts at >=30% upside to the mean target;
    # a mean target BELOW the price is the street saying "overextended".
    if m.analyst_upside_pct is not None:
        if m.analyst_upside_pct > 0:
            score += min(m.analyst_upside_pct / 30.0, 1.0) * 10
        else:
            score -= 5
    # Dilution guard (EDGAR share counts) — printing shares erodes whatever
    # the growth numbers promise; heavy printing is disqualifying territory.
    if m.dilution_yoy_pct is not None:
        if m.dilution_yoy_pct > 25:
            score -= 20
        elif m.dilution_yoy_pct > 10:
            score -= 10
    # Cash runway — a burner with under ~4 quarters of cash almost always
    # dilutes soon; the raise is the catalyst that kills the trade.
    if m.cash_runway_quarters is not None and m.cash_runway_quarters < 4:
        score -= 15
    # Insider cluster (EDGAR Form 4): several distinct insiders buying
    # together is a far stronger read than one routine purchase.
    if m.insider_cluster:
        score += 8
    return round(score, 1)


def describe(m: CandidateMetrics) -> str:
    """One-line human summary used in Telegram alerts and the dashboard."""
    parts: list[str] = []
    if m.revenue_growth is not None:
        parts.append(f"revenue {m.revenue_growth * 100:+.0f}%")
    if m.earnings_growth is not None:
        parts.append(f"earnings {m.earnings_growth * 100:+.0f}%")
    if m.return_3m is not None:
        parts.append(f"3M {m.return_3m * 100:+.0f}%")
    if m.insider_cluster:
        parts.append("insider cluster buy")
    elif m.insider_net_shares is not None and m.insider_net_shares > 0:
        parts.append("insiders buying")
    if m.analyst_upside_pct is not None and abs(m.analyst_upside_pct) >= 5:
        parts.append(f"analysts see {m.analyst_upside_pct:+.0f}%")
    if m.dilution_yoy_pct is not None and m.dilution_yoy_pct > 10:
        parts.append(f"⚠ dilution +{m.dilution_yoy_pct:.0f}%/yr")
    if m.cash_runway_quarters is not None and m.cash_runway_quarters < 4:
        parts.append(f"⚠ {m.cash_runway_quarters:.0f}q cash left")
    if m.watchers is not None:
        if m.watchers < ATTENTION_LOW:
            parts.append(f"only {m.watchers:,} watchers")
        else:
            parts.append(f"{m.watchers:,} watchers")
    return ", ".join(parts) if parts else "insufficient data"
