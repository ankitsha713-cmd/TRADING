"""Personal finance assistant service.

Run from the repo root:

    uvicorn app.main:app

Startup: creates the SQLite schema, seeds the starter watchlist and default
schedule slots on first run, and builds the slot scheduler. The dashboard
lives at http://127.0.0.1:8000 (API docs at /docs).
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse

# .env must be loaded before anything reads provider keys or settings.
load_dotenv()

from app.api import portfolio, runs, schedule, screener, tickers, watchlist  # noqa: E402
from app.api.schemas import HealthResponse  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.core.scheduler import build_scheduler, sync_slot_jobs  # noqa: E402
from app.models.base import init_db, session_factory  # noqa: E402
from app.services.seed import (  # noqa: E402
    seed_paper_account_if_missing,
    seed_schedule_if_empty,
    seed_watchlist_if_empty,
)

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"

_PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}


def _warn_about_missing_config() -> None:
    settings = get_settings()
    key_env = _PROVIDER_KEY_ENV.get(settings.assistant_llm_provider)
    if key_env and not os.environ.get(key_env):
        logger.warning(
            "%s is not set — scheduled runs will fail until you add it to .env",
            key_env,
        )
    if not settings.telegram_enabled:
        logger.warning("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) — no chat alerts")
    if not settings.email_enabled:
        logger.warning("Email not configured (SMTP_USERNAME / SMTP_PASSWORD / EMAIL_TO) — no digests")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    _warn_about_missing_config()

    await init_db()
    async with session_factory()() as session, session.begin():
        await seed_watchlist_if_empty(session)
        await seed_schedule_if_empty(session)
        await seed_paper_account_if_missing(session, get_settings().paper_starting_cash)

    scheduler = build_scheduler()
    scheduler.start()
    await sync_slot_jobs(scheduler)
    app.state.scheduler = scheduler
    logger.info("Assistant started; dashboard at http://127.0.0.1:8000")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Assistant stopped")


app = FastAPI(
    title="TradingAgents Assistant",
    description=(
        "Personal finance assistant on top of the TradingAgents multi-agent engine: "
        "scheduled analysis slots, watchlist rotation, Telegram/email alerts, dashboard. "
        "Research output — not financial advice."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(watchlist.router)
app.include_router(runs.router)
app.include_router(schedule.router)
app.include_router(tickers.router)
app.include_router(portfolio.router)
app.include_router(screener.router)


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    settings = get_settings()
    scheduler = app.state.scheduler
    jobs = [
        {
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        }
        for job in scheduler.get_jobs()
    ]
    import app.services.pipeline as _pipeline
    from app.models.base import session_factory as _sf
    from app.repositories.signals import SignalRepository as _SigRepo
    from app.services.pipeline import _utc_midnight, _utc_week_start

    async with _sf()() as session:
        signals = _SigRepo(session)
        used_today = await signals.count_since(_utc_midnight())
        used_week = await signals.count_since(_utc_week_start())
    return HealthResponse(
        status="ok",
        scheduler_running=scheduler.running,
        jobs=jobs,
        telegram_configured=settings.telegram_enabled,
        email_configured=settings.email_enabled,
        llm_provider=settings.assistant_llm_provider,
        deep_model=settings.assistant_deep_model,
        quick_model=settings.assistant_quick_model,
        runs_today=used_today,
        daily_run_budget=settings.assistant_daily_run_budget,
        runs_this_week=used_week,
        weekly_run_budget=settings.assistant_weekly_run_budget,
        analyzing=_pipeline.current_analysis,
    )
