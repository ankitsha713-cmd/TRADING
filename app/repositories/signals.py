"""Repository for signal/run history rows."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import SignalRecord


class SignalRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, record: SignalRecord) -> SignalRecord:
        self._session.add(record)
        await self._session.flush()
        return record

    async def list_recent(self, symbol: str | None = None, limit: int = 50) -> list[SignalRecord]:
        stmt = select(SignalRecord).order_by(SignalRecord.created_at.desc()).limit(limit)
        if symbol:
            stmt = stmt.where(SignalRecord.symbol == symbol.upper())
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def count_since(self, since: datetime) -> int:
        """Number of runs (any status) recorded at or after ``since`` — LLM quota accounting."""
        result = await self._session.execute(
            select(func.count()).select_from(SignalRecord).where(SignalRecord.created_at >= since)
        )
        return int(result.scalar_one())

    async def latest_success(self, symbol: str) -> SignalRecord | None:
        result = await self._session.execute(
            select(SignalRecord)
            .where(SignalRecord.symbol == symbol.upper(), SignalRecord.status == "success")
            .order_by(SignalRecord.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_success(self, symbol: str, limit: int = 20) -> list[SignalRecord]:
        """All successful analyses for a ticker, newest first — the report versions."""
        result = await self._session.execute(
            select(SignalRecord)
            .where(SignalRecord.symbol == symbol.upper(), SignalRecord.status == "success")
            .order_by(SignalRecord.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def get(self, signal_id: int) -> SignalRecord | None:
        return await self._session.get(SignalRecord, signal_id)
