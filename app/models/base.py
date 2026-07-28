"""Async SQLAlchemy engine, session factory, and FastAPI session dependency."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url)
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


# Columns added after a table already shipped: create_all() cannot ALTER, so
# these are applied additively at startup. SQLite ADD COLUMN is cheap and
# idempotence comes from checking PRAGMA table_info first.
_COLUMN_MIGRATIONS = [
    ("watchlist", "category", "VARCHAR(16) DEFAULT 'satellite'"),
    ("watchlist", "next_review_at", "DATETIME"),
    ("paper_account", "label", "VARCHAR(16) DEFAULT 'strategic'"),
]


def _apply_column_migrations(conn) -> None:
    for table, column, ddl in _COLUMN_MIGRATIONS:
        existing = {
            row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
        }
        if existing and column not in existing:
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


async def init_db() -> None:
    """Create tables on first startup and apply additive column migrations.

    Pragmatism for a local SQLite app: ``create_all`` plus explicit ADD COLUMN
    statements. Revisit Alembic if this ever moves to a shared database.
    """
    from app.models import entities  # noqa: F401 — register mappings

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_apply_column_migrations)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, committed on success."""
    async with session_factory()() as session, session.begin():
        yield session
