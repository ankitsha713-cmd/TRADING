"""Watchlist management endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AddTickerRequest, UpdateTickerRequest, WatchlistItem
from app.domain import Tier
from app.models.base import get_session
from app.repositories.watchlist import WatchlistRepository

router = APIRouter(prefix="/watchlist", tags=["watchlist"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=list[WatchlistItem])
async def list_watchlist(session: SessionDep) -> list[WatchlistItem]:
    tickers = await WatchlistRepository(session).list_all()
    return [WatchlistItem.model_validate(t) for t in tickers]


@router.post("", response_model=WatchlistItem, status_code=status.HTTP_201_CREATED)
async def add_ticker(body: AddTickerRequest, session: SessionDep) -> WatchlistItem:
    repo = WatchlistRepository(session)
    if await repo.get_by_symbol(body.symbol) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{body.symbol.upper()} is already on the watchlist",
        )
    ticker = await repo.add(body.symbol)
    return WatchlistItem.model_validate(ticker)


@router.delete("/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_ticker(symbol: str, session: SessionDep) -> None:
    repo = WatchlistRepository(session)
    ticker = await repo.get_by_symbol(symbol)
    if ticker is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"{symbol.upper()} not on watchlist"
        )
    await repo.remove(ticker)


@router.patch("/{symbol}", response_model=WatchlistItem)
async def update_ticker(
    symbol: str, body: UpdateTickerRequest, session: SessionDep
) -> WatchlistItem:
    """Set a ticker's coverage cadence (daily / weekly / paused)."""
    return await _set_tier(symbol, Tier(body.tier), session)


@router.post("/{symbol}/pause", response_model=WatchlistItem)
async def pause_ticker(symbol: str, session: SessionDep) -> WatchlistItem:
    return await _set_tier(symbol, Tier.PAUSED, session)


@router.post("/{symbol}/resume", response_model=WatchlistItem)
async def resume_ticker(symbol: str, session: SessionDep) -> WatchlistItem:
    return await _set_tier(symbol, Tier.DAILY, session)


async def _set_tier(symbol: str, tier: Tier, session: AsyncSession) -> WatchlistItem:
    repo = WatchlistRepository(session)
    ticker = await repo.get_by_symbol(symbol)
    if ticker is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"{symbol.upper()} not on watchlist"
        )
    ticker.tier = tier.value
    if tier is Tier.DAILY:
        ticker.consecutive_holds = 0
    return WatchlistItem.model_validate(ticker)
