"""Watchlist rotation rules — pure logic, no I/O.

Since scheduling moved to the priority queue (model-chosen review dates,
volatility-scaled event triggers, position-first funding), the coverage tier
no longer drives WHEN analysis happens — it is the user's preference plus a
demotion backstop. So rotation only ever demotes (persistent Holds → weekly)
and never flips a tier upward: an actionable rating already gets attention
through its review date and, if bought, position priority. Tickers are never
auto-deleted here; expiry is the screener's job and position-holders are
pinned.
"""

from app.domain import HOLD_RATING, Tier


def next_rotation_state(
    tier: Tier,
    consecutive_holds: int,
    rating: str,
    *,
    demote_after: int,
) -> tuple[Tier, int]:
    """Return the (tier, consecutive_holds) a ticker should have after a run.

    Paused tickers are never rotated automatically — pausing is a manual
    decision and stays manual.
    """
    if tier is Tier.PAUSED:
        return tier, consecutive_holds

    if rating == HOLD_RATING:
        holds = consecutive_holds + 1
        if tier is Tier.DAILY and holds >= demote_after:
            return Tier.WEEKLY, holds
        return tier, holds

    # Any actionable rating (Buy/Overweight/Underweight/Sell): reset the
    # boredom counter; the tier the user chose stays put.
    return tier, 0
