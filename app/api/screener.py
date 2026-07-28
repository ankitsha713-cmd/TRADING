"""Screener endpoints: recent results and a manual trigger."""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ScreenerResultItem
from app.models.base import get_session
from app.repositories.screener import ScreenerRepository
from app.services.screener import run_screener

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/screener", tags=["screener"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_runs: set[asyncio.Task] = set()


@router.get("", response_model=list[ScreenerResultItem])
async def recent_results(session: SessionDep) -> list[ScreenerResultItem]:
    results = await ScreenerRepository(session).list_recent(limit=40)
    return [ScreenerResultItem.model_validate(r) for r in results]


@router.post("/run", status_code=202)
async def run_now() -> dict:
    """Fire a screener pass now (takes a minute or two; no LLM cost)."""
    task = asyncio.create_task(run_screener())
    _runs.add(task)
    task.add_done_callback(_runs.discard)
    logger.info("Manual screener run triggered")
    return {"status": "started"}
