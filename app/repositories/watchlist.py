"""Repository for watchlist rows. All DB access for the watchlist lives here."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import Market, Tier, infer_asset_type, infer_market
from app.models.entities import WatchlistTicker


class WatchlistRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_all(self) -> list[WatchlistTicker]:
        result = await self._session.execute(
            select(WatchlistTicker).order_by(WatchlistTicker.market, WatchlistTicker.symbol)
        )
        return list(result.scalars())

    async def get_by_symbol(self, symbol: str) -> WatchlistTicker | None:
        result = await self._session.execute(
            select(WatchlistTicker).where(WatchlistTicker.symbol == symbol.upper())
        )
        return result.scalar_one_or_none()

    async def get_due_for_run(
        self,
        market: Market | None,
        include_weekly: bool,
        limit: int | None = None,
    ) -> list[WatchlistTicker]:
        """Tickers to analyze in a scheduled run, stalest first.

        Daily-tier tickers always qualify; weekly-tier only when
        ``include_weekly`` (first market day of the week). Paused never runs.
        Stalest-first ordering (never-run tickers first) makes budgeted slots
        rotate fairly through the watchlist instead of re-analyzing the same
        few symbols.
        """
        tiers = [Tier.DAILY.value]
        if include_weekly:
            tiers.append(Tier.WEEKLY.value)
        stmt = select(WatchlistTicker).where(WatchlistTicker.tier.in_(tiers))
        if market is not None:
            stmt = stmt.where(WatchlistTicker.market == market.value)
        stmt = stmt.order_by(
            WatchlistTicker.last_run_at.asc().nullsfirst(), WatchlistTicker.symbol
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def list_by_tier(self, market: Market | None, tier: Tier) -> list[WatchlistTicker]:
        stmt = select(WatchlistTicker).where(WatchlistTicker.tier == tier.value)
        if market is not None:
            stmt = stmt.where(WatchlistTicker.market == market.value)
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def add(
        self, symbol: str, added_by: str = "manual", category: str | None = None
    ) -> WatchlistTicker:
        symbol = symbol.upper().strip()
        if category is None:
            # Screener finds are satellites (rotating tournament); anything a
            # human adds deliberately is treated as core conviction.
            category = "satellite" if added_by == "screener" else "core"
        ticker = WatchlistTicker(
            symbol=symbol,
            market=infer_market(symbol).value,
            asset_type=infer_asset_type(symbol),
            added_by=added_by,
            category=category,
        )
        self._session.add(ticker)
        await self._session.flush()
        return ticker

    async def count_satellites(self) -> int:
        result = await self._session.execute(
            select(WatchlistTicker.id).where(WatchlistTicker.category == "satellite")
        )
        return len(result.scalars().all())

    async def remove(self, ticker: WatchlistTicker) -> None:
        await self._session.delete(ticker)

    async def count(self) -> int:
        result = await self._session.execute(select(WatchlistTicker.id))
        return len(result.scalars().all())
