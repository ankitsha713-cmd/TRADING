"""Repository for schedule slots."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import ScheduleSlot


class ScheduleRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_all(self) -> list[ScheduleSlot]:
        result = await self._session.execute(select(ScheduleSlot).order_by(ScheduleSlot.id))
        return list(result.scalars())

    async def get(self, slot_id: int) -> ScheduleSlot | None:
        return await self._session.get(ScheduleSlot, slot_id)

    async def add(self, slot: ScheduleSlot) -> ScheduleSlot:
        self._session.add(slot)
        await self._session.flush()
        return slot

    async def count(self) -> int:
        result = await self._session.execute(select(ScheduleSlot.id))
        return len(result.scalars().all())
