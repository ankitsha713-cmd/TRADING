"""Repository for screener results."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import ScreenerResult


class ScreenerRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, result: ScreenerResult) -> ScreenerResult:
        self._session.add(result)
        await self._session.flush()
        return result

    async def list_recent(self, limit: int = 50) -> list[ScreenerResult]:
        stmt = (
            select(ScreenerResult)
            .order_by(ScreenerResult.run_date.desc(), ScreenerResult.score.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())
