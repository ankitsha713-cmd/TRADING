"""The anomaly screener: cheap quantitative discovery of under-followed strength.

Runs daily (no LLM — pure data APIs, costs nothing against the run budget):

1. Candidates from Yahoo's predefined screens (US small-cap gainers, growth
   tech, undervalued growth, aggressive small caps) plus a custom India query.
2. Each candidate enriched with fundamentals (revenue/earnings growth,
   margins), momentum, legal insider-transaction filings (net buying), and
   StockTwits attention (best-effort).
3. Scored by ``screener_rules.anomaly_score``; the top scorers that clear
   MIN_SCORE_TO_ADD are auto-added to the watchlist (added_by="screener"),
   where the normal analysis slots pick them up stalest-first.

Crypto is intentionally out of scope: "hidden gem" small-cap coins are
overwhelmingly manipulation-driven; the watchlist's majors stay curated.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.domain import Market, infer_market
from app.models.base import session_factory
from app.models.entities import ScreenerResult
from app.repositories.screener import ScreenerRepository
from app.repositories.watchlist import WatchlistRepository
from app.services.notifier import Notifier
from app.services.screener_rules import (
    MIN_SCORE_TO_ADD,
    CandidateMetrics,
    anomaly_score,
    describe,
)

logger = logging.getLogger(__name__)

_US_SCREENS = (
    "small_cap_gainers",
    "growth_technology_stocks",
    "undervalued_growth_stocks",
    "aggressive_small_caps",
)
_PER_SCREEN = 25
# Enrichment slots are reserved per market so US candidates (collected first,
# in bulk) can't crowd Indian ones out of the scoring entirely.
_MAX_ENRICHED_US = 20
_MAX_ENRICHED_INDIA = 10


def _collect_candidates_sync() -> tuple[list[str], list[str]]:
    """(us_symbols, india_symbols) from Yahoo screens, deduped, NSE preferred."""
    import yfinance as yf

    us: list[str] = []
    for screen in _US_SCREENS:
        try:
            for quote in yf.screen(screen, count=_PER_SCREEN).get("quotes", []):
                symbol = quote.get("symbol")
                if symbol:
                    us.append(symbol)
        except Exception as exc:
            logger.warning("Screen %r failed: %s", screen, exc)

    india: list[str] = []
    try:
        from yfinance import EquityQuery

        query = EquityQuery("and", [
            EquityQuery("eq", ["region", "in"]),
            # >50B INR market cap: liquid mid/small caps, not micro-cap traps
            EquityQuery("gt", ["intradaymarketcap", 50_000_000_000]),
        ])
        result = yf.screen(query, count=_PER_SCREEN, sortField="percentchange", sortAsc=False)
        for quote in result.get("quotes", []):
            symbol = quote.get("symbol", "")
            if symbol.endswith(".NS"):  # skip .BO duplicates of the same company
                india.append(symbol)
    except Exception as exc:
        logger.warning("India screen failed: %s", exc)

    def dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        return [s for s in items if not (s in seen or seen.add(s))]

    return dedupe(us), dedupe(india)


def _insider_net_shares_sync(ticker) -> float | None:
    """Net insider shares bought minus sold from recent Form-4 style filings."""
    try:
        frame = ticker.insider_transactions
        if frame is None or frame.empty:
            return None
        text_col = next(
            (c for c in ("Text", "Transaction", "transactionText") if c in frame.columns), None
        )
        shares_col = next((c for c in ("Shares", "shares") if c in frame.columns), None)
        if text_col is None or shares_col is None:
            return None
        net = 0.0
        for _, row in frame.head(40).iterrows():
            text = str(row.get(text_col, "")).lower()
            shares = row.get(shares_col)
            if shares is None:
                continue
            try:
                shares = float(shares)
            except (TypeError, ValueError):
                continue
            if "purchase" in text or "buy" in text:
                net += shares
            elif "sale" in text or "sold" in text:
                net -= shares
        return net
    except Exception:
        return None


def _watchers_sync(symbol: str) -> int | None:
    """StockTwits watchlist count — the attention meter. Best-effort."""
    import requests

    try:
        response = requests.get(
            f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=8,
        )
        if response.status_code != 200:
            return None
        return response.json().get("symbol", {}).get("watchlist_count")
    except Exception:
        return None


def _enrich_sync(symbol: str) -> CandidateMetrics:
    import yfinance as yf

    from tradingagents.dataflows.symbol_utils import normalize_symbol

    ticker = yf.Ticker(normalize_symbol(symbol))
    info: dict = {}
    try:
        info = ticker.info or {}
    except Exception:
        logger.warning("info fetch failed for %s", symbol)

    return_3m = None
    try:
        history = ticker.history(period="3mo")
        if len(history) >= 2:
            first, last = float(history["Close"].iloc[0]), float(history["Close"].iloc[-1])
            return_3m = (last - first) / first
    except Exception:
        pass

    # Analyst upside straight from .info — no extra request needed.
    upside = None
    target_mean = info.get("targetMeanPrice")
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if target_mean and price:
        upside = (target_mean - price) / price * 100

    return CandidateMetrics(
        symbol=symbol.upper(),
        market=infer_market(symbol).value,
        revenue_growth=info.get("revenueGrowth"),
        earnings_growth=info.get("earningsGrowth"),
        profit_margins=info.get("profitMargins"),
        return_3m=return_3m,
        week52_change=info.get("52WeekChange"),
        market_cap=info.get("marketCap"),
        watchers=_watchers_sync(symbol) if infer_market(symbol) is Market.US else None,
        insider_net_shares=_insider_net_shares_sync(ticker),
        analyst_upside_pct=upside,
    )


async def expire_stale_picks() -> list[str]:
    """Drop screener picks that stayed boring past the expiry window.

    Only satellites the screener added, sitting at weekly tier with a Hold (or
    no actionable) rating and — the hard rule — NO open position, ever leave
    this way. The screener can always re-discover them if their numbers turn.
    """
    from app.repositories.portfolio import PortfolioRepository

    settings = get_settings()
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=settings.screener_expiry_days
    )
    removed: list[str] = []
    async with session_factory()() as session, session.begin():
        watchlist_repo = WatchlistRepository(session)
        portfolio_repo = PortfolioRepository(session)
        held = {p.symbol for p in await portfolio_repo.list_positions()}
        for ticker in await watchlist_repo.list_all():
            last_run = ticker.last_run_at
            if last_run is not None and last_run.tzinfo:
                last_run = last_run.replace(tzinfo=None)
            if (
                ticker.added_by == "screener"
                and ticker.category == "satellite"
                and ticker.symbol not in held  # position-pin: owned = untouchable
                and ticker.last_rating in (None, "Hold")
                and last_run is not None
                and last_run < cutoff
            ):
                await watchlist_repo.remove(ticker)
                removed.append(ticker.symbol)
    if removed:
        logger.info("Screener expiry: removed %s", ", ".join(removed))
    return removed


async def _edgar_refine(
    scored: list[tuple[float, CandidateMetrics]], top_n: int
) -> list[tuple[float, CandidateMetrics]]:
    """Enrich the top US candidates with EDGAR dilution + insider data, re-rank."""
    from dataclasses import replace

    from app.services.edgar import fetch_dilution_sync, fetch_insider_activity_sync

    refined: list[tuple[float, CandidateMetrics]] = []
    for rank, (score, metrics) in enumerate(scored):
        if rank >= top_n or metrics.market != Market.US.value:
            refined.append((score, metrics))
            continue
        try:
            dilution = await asyncio.to_thread(fetch_dilution_sync, metrics.symbol)
            insider = await asyncio.to_thread(fetch_insider_activity_sync, metrics.symbol)
        except Exception:
            logger.warning("EDGAR refinement failed for %s", metrics.symbol)
            refined.append((score, metrics))
            continue
        updated = replace(
            metrics,
            dilution_yoy_pct=dilution.shares_yoy_pct if dilution else None,
            cash_runway_quarters=dilution.runway_quarters if dilution else None,
            insider_cluster=bool(insider and insider.cluster_buy),
            insider_net_shares=(
                insider.net_shares if insider and insider.filings_parsed > 0
                else metrics.insider_net_shares
            ),
        )
        new_score = anomaly_score(updated)
        refined.append((new_score if new_score is not None else score, updated))
    refined.sort(key=lambda pair: pair[0], reverse=True)
    return refined


async def run_screener() -> list[dict]:
    """One full screener pass. Returns scored results (dicts for the API/UI)."""
    settings = get_settings()
    run_date = datetime.now(timezone.utc).date().isoformat()

    # Drain before filling: expire stale picks so seats free up.
    await expire_stale_picks()

    us_candidates, india_candidates = await asyncio.to_thread(_collect_candidates_sync)
    async with session_factory()() as session:
        watchlist_repo = WatchlistRepository(session)
        existing = {t.symbol for t in await watchlist_repo.list_all()}
        satellite_count = await watchlist_repo.count_satellites()

    fresh_us = [s for s in us_candidates if s.upper() not in existing][:_MAX_ENRICHED_US]
    fresh_india = [
        s for s in india_candidates if s.upper() not in existing
    ][:_MAX_ENRICHED_INDIA]
    fresh = fresh_us + fresh_india
    logger.info(
        "Screener: %d US + %d India candidates, enriching %d + %d",
        len(us_candidates), len(india_candidates), len(fresh_us), len(fresh_india),
    )

    scored: list[tuple[float, CandidateMetrics]] = []
    for symbol in fresh:
        metrics = await asyncio.to_thread(_enrich_sync, symbol)
        score = anomaly_score(metrics)
        if score is not None:
            scored.append((score, metrics))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    # Second pass: primary-source EDGAR refinement (dilution guard + insider
    # clusters) for the top US candidates only — each costs a few throttled
    # SEC requests, so the long tail isn't worth it. Scores are then re-ranked
    # before any watchlist adds happen.
    scored = await _edgar_refine(scored, top_n=12)

    capacity = max(0, settings.screener_satellite_cap - satellite_count)
    budget = min(settings.screener_max_adds, capacity)
    notifier = Notifier(settings)
    results: list[dict] = []

    async with session_factory()() as session, session.begin():
        screener_repo = ScreenerRepository(session)
        watchlist_repo = WatchlistRepository(session)
        for rank, (score, metrics) in enumerate(scored):
            add = rank < budget and score >= MIN_SCORE_TO_ADD
            if add:
                await watchlist_repo.add(
                    metrics.symbol, added_by="screener", category="satellite"
                )
            await screener_repo.add(ScreenerResult(
                run_date=run_date,
                symbol=metrics.symbol,
                market=metrics.market,
                score=score,
                summary=describe(metrics),
                metrics_json=json.dumps(metrics.__dict__),
                added=add,
            ))
            results.append({
                "symbol": metrics.symbol, "market": metrics.market,
                "score": score, "summary": describe(metrics), "added": add,
            })

    for r in results:
        if r["added"]:
            await notifier.send_telegram(
                f"🔎 <b>Screener pick: {r['symbol']}</b> (score {r['score']:.0f})\n"
                f"{r['summary']}\n"
                f"Added to the watchlist — the next analysis slot will do the deep dive."
            )
    logger.info("Screener finished: %d scored, %d added", len(results),
                sum(1 for r in results if r["added"]))
    return results
