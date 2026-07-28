"""Portfolio endpoints: paper book snapshot, real-holdings CRUD, trade history.

All valuations use live market prices (yfinance) and the live USDINR rate for
Indian holdings — the same data the analysis engine sees.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AddHoldingRequest, PortfolioResponse, PositionItem, TradeItem
from app.domain import infer_market
from app.models.base import get_session
from app.models.entities import Position, Trade
from app.repositories.portfolio import PortfolioRepository
from app.services.broker_rules import currency_for_market
from app.services.paper_broker import live_price, usd_rate

logger = logging.getLogger(__name__)

router = APIRouter(tags=["portfolio"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _to_item(position: Position) -> PositionItem:
    price = await live_price(position.symbol)
    rate = await usd_rate(position.currency)
    value = pnl = pct = None
    if price is not None and rate:
        value = position.quantity * price / rate
        pnl = position.quantity * (price - position.avg_price) / rate
        pct = (price - position.avg_price) / position.avg_price * 100
    return PositionItem(
        id=position.id,
        account_type=position.account_type,
        symbol=position.symbol,
        market=position.market,
        currency=position.currency,
        quantity=position.quantity,
        avg_price=position.avg_price,
        stop_loss=position.stop_loss,
        price_target=position.price_target,
        opened_at=position.opened_at,
        note=position.note,
        last_price=price,
        value_usd=value,
        pnl_usd=pnl,
        pnl_pct=pct,
    )


async def _benchmark_return_pct(since: datetime) -> float | None:
    def fetch() -> float | None:
        import yfinance as yf

        history = yf.Ticker("SPY").history(start=since.date().isoformat())
        if len(history) < 2:
            return None
        first, last = float(history["Close"].iloc[0]), float(history["Close"].iloc[-1])
        return (last - first) / first * 100

    try:
        return await asyncio.to_thread(fetch)
    except Exception:
        logger.exception("Benchmark fetch failed")
        return None


@router.get("/portfolio", response_model=PortfolioResponse)
async def portfolio(session: SessionDep) -> PortfolioResponse:
    from app.api.schemas import BookSummary
    from app.core.config import get_settings
    from app.services.paper_broker import BOOK_POSITION_TYPE

    repo = PortfolioRepository(session)
    positions = await repo.list_positions()
    items = [await _to_item(p) for p in positions]
    real = [i for i in items if i.account_type == "real"]

    settings = get_settings()
    books: list[BookSummary] = []
    oldest_created = None
    for label in ("strategic", "tactical"):
        account = await repo.get_account(label)
        if account is None:
            continue
        oldest_created = min(filter(None, [oldest_created, account.created_at]))
        position_type = BOOK_POSITION_TYPE[label]
        book_positions = [i for i in items if i.account_type == position_type]
        equity = None
        if all(i.value_usd is not None for i in book_positions):
            equity = account.cash + sum(i.value_usd for i in book_positions)
        return_pct = (
            (equity - account.starting_cash) / account.starting_cash * 100
            if equity is not None else None
        )
        books.append(BookSummary(
            label=label,
            starting_cash_usd=account.starting_cash,
            cash_usd=account.cash,
            equity_usd=equity,
            return_pct=return_pct,
            positions=book_positions,
            enabled=(label != "tactical") or bool(settings.tactical_rule.strip()),
        ))
    if not books:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No paper books yet")

    strategic = books[0]
    return PortfolioResponse(
        books=books,
        real_positions=real,
        benchmark_return_pct=await _benchmark_return_pct(oldest_created),
        tactical_rule=settings.tactical_rule.strip(),
        cash_usd=strategic.cash_usd,
        starting_cash_usd=strategic.starting_cash_usd,
        equity_usd=strategic.equity_usd,
        return_pct=strategic.return_pct,
        paper_positions=strategic.positions,
    )


@router.get("/portfolio/history")
async def portfolio_history(session: SessionDep) -> dict:
    """Daily equity curves per book, for the scoreboard sparklines."""
    from app.api.schemas import EquityPoint

    repo = PortfolioRepository(session)
    out: dict[str, list] = {}
    for book in ("strategic", "tactical"):
        snapshots = await repo.list_snapshots(book, limit=120)
        out[book] = [
            EquityPoint(date=s.snapshot_date, equity_usd=round(s.equity_usd, 2)).model_dump()
            for s in snapshots
        ]
    return out


@router.post("/holdings", response_model=PositionItem, status_code=status.HTTP_201_CREATED)
async def add_holding(body: AddHoldingRequest, session: SessionDep) -> PositionItem:
    """Log a real position you bought (shares, price, when)."""
    symbol = body.symbol.upper().strip()
    repo = PortfolioRepository(session)
    if await repo.get_position("real", symbol) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A real holding for {symbol} already exists — remove it first to re-enter",
        )
    market = infer_market(symbol)
    opened = (
        datetime.strptime(body.bought_at, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if body.bought_at else datetime.now(timezone.utc)
    )
    position = await repo.add_position(Position(
        account_type="real",
        symbol=symbol,
        market=market.value,
        currency=currency_for_market(market),
        quantity=body.quantity,
        avg_price=body.price,
        stop_loss=body.stop_loss,
        opened_at=opened,
        note=body.note,
    ))
    await repo.add_trade(Trade(
        account_type="real", symbol=symbol, side="buy",
        quantity=body.quantity, price=body.price,
        currency=position.currency, reason="manual entry",
        executed_at=opened,
    ))
    return await _to_item(position)


@router.delete("/holdings/{position_id}", response_model=TradeItem)
async def close_holding(position_id: int, session: SessionDep) -> TradeItem:
    """Remove a real holding (you sold it); logs the exit at the live price."""
    repo = PortfolioRepository(session)
    position = await repo.get_position_by_id(position_id)
    if position is None or position.account_type != "real":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Holding not found")
    price = await live_price(position.symbol) or position.avg_price
    rate = await usd_rate(position.currency) or 1.0
    pnl = position.quantity * (price - position.avg_price) / rate
    trade = await repo.add_trade(Trade(
        account_type="real", symbol=position.symbol, side="sell",
        quantity=position.quantity, price=price, currency=position.currency,
        reason="manual close", realized_pnl_usd=pnl,
    ))
    await repo.remove_position(position)
    return TradeItem.model_validate(trade)


@router.get("/trades", response_model=list[TradeItem])
async def trades(session: SessionDep) -> list[TradeItem]:
    records = await PortfolioRepository(session).list_trades(limit=100)
    return [TradeItem.model_validate(t) for t in records]
