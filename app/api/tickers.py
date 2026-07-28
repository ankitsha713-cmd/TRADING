"""Per-ticker data for the dashboard: price history and the latest analysis report."""

import asyncio
import logging
import math
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    PriceHistory,
    ReportResponse,
    ReportVersionItem,
    TickerContextResponse,
)
from app.models.base import get_session
from app.repositories.signals import SignalRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tickers", tags=["tickers"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _fetch_prices_sync(symbol: str, days: int) -> tuple[list[str], list[float]]:
    from datetime import date, timedelta

    import yfinance as yf

    from tradingagents.dataflows.symbol_utils import normalize_symbol

    # start= keeps ranges in calendar days ("1M" means one month); a period
    # string like "30d" would mean 30 *trading* days (~6 weeks).
    start = (date.today() - timedelta(days=days)).isoformat()
    history = yf.Ticker(normalize_symbol(symbol)).history(start=start)
    if history.empty:
        return [], []
    dates = [d.strftime("%Y-%m-%d") for d in history.index]
    close = [round(float(c), 4) for c in history["Close"]]
    # Drop NaN rows (halts, listing gaps) so the chart doesn't break.
    pairs = [(d, c) for d, c in zip(dates, close, strict=True) if not math.isnan(c)]
    return [p[0] for p in pairs], [p[1] for p in pairs]


@router.get("/{symbol}/prices", response_model=PriceHistory)
async def price_history(
    symbol: str,
    days: Annotated[int, Query(ge=7, le=730)] = 180,
) -> PriceHistory:
    dates, close = await asyncio.to_thread(_fetch_prices_sync, symbol, days)
    if not dates:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No price data for {symbol.upper()}",
        )
    return PriceHistory(symbol=symbol.upper(), dates=dates, close=close)


@router.get("/{symbol}/context", response_model=TickerContextResponse)
async def ticker_context(symbol: str) -> TickerContextResponse:
    """Forward-looking context: next earnings date + analyst consensus."""
    from app.services.earnings import fetch_earnings_context_sync

    context = await asyncio.to_thread(fetch_earnings_context_sync, symbol)
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No earnings/analyst context available for {symbol.upper()}",
        )
    return TickerContextResponse(
        symbol=context.symbol,
        next_earnings_date=(
            context.next_earnings_date.isoformat() if context.next_earnings_date else None
        ),
        days_to_earnings=context.days_to_earnings,
        eps_estimate_avg=context.eps_estimate_avg,
        revenue_estimate_avg=context.revenue_estimate_avg,
        target_mean=context.target_mean,
        target_median=context.target_median,
        target_high=context.target_high,
        target_low=context.target_low,
        current_price=context.current_price,
        analyst_upside_pct=context.analyst_upside_pct,
    )


@router.get("/{symbol}/reports", response_model=list[ReportVersionItem])
async def report_versions(symbol: str, session: SessionDep) -> list[ReportVersionItem]:
    """Every stored analysis of this ticker, newest first (each run is kept)."""
    records = await SignalRepository(session).list_success(symbol)
    return [ReportVersionItem.model_validate(r) for r in records]


@router.get("/{symbol}/report", response_model=ReportResponse)
async def report(
    symbol: str, session: SessionDep, signal_id: int | None = None
) -> ReportResponse:
    """One analysis report: the latest by default, or a specific past run
    via ``signal_id`` (from ``/tickers/{symbol}/reports``)."""
    repo = SignalRepository(session)
    if signal_id is not None:
        record = await repo.get(signal_id)
        if record is not None and (
            record.symbol != symbol.upper() or record.status != "success"
        ):
            record = None
    else:
        record = await repo.latest_success(symbol)
    if record is None or not record.report_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No analysis on record for {symbol.upper()} yet",
        )
    report_file = Path(record.report_path)
    if report_file.is_dir():
        report_file = report_file / "complete_report.md"
    if not report_file.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report file missing on disk (was it moved or deleted?)",
        )
    markdown = await asyncio.to_thread(report_file.read_text, "utf-8")
    return ReportResponse(
        signal_id=record.id,
        symbol=record.symbol,
        trade_date=record.trade_date,
        rating=record.rating,
        created_at=record.created_at,
        markdown=markdown,
    )
