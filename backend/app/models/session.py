"""Database session lifecycle — pooled engine, scoped sessions, FastAPI DI."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.errors import ConfigError
from app.core.settings import get_settings
from app.db.sql.health import PING
from app.memory.pg import close_psycopg_pool

T = TypeVar("T")

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _ensure_sqlite_dir(url: str) -> None:
    prefix = "sqlite+aiosqlite:///"
    if url.startswith(prefix):
        path = url.removeprefix(prefix)
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)


def _create_engine(url: str):  # noqa: ANN201 - engine type varies by driver
    settings = get_settings()
    _ensure_sqlite_dir(url)
    if url.startswith("sqlite"):
        # One connection per checkout — avoids SQLite writer-lock pile-ups.
        return create_async_engine(
            url,
            echo=False,
            poolclass=NullPool,
            connect_args={"timeout": settings.db_sqlite_busy_timeout_s},
        )
    if url.startswith("postgresql"):
        return create_async_engine(
            url,
            echo=False,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_s,
            pool_recycle=settings.db_pool_recycle_s,
        )
    raise ConfigError(f"Unsupported DATABASE_URL scheme: {url.split('://')[0]}")


def get_engine():  # noqa: ANN201
    global _engine
    if _engine is None:
        _engine = _create_engine(get_settings().database_url)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Borrow one pooled session; rollback on error, always return to pool."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def run_write_scope(
    injected: AsyncSession | None = None,
) -> AsyncIterator[AsyncSession]:
    """Reuse an injected session or open one scoped write batch."""
    if injected is not None:
        yield injected
        return
    async with session_scope() as session:
        yield session


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — one AsyncSession per HTTP request."""
    async with session_scope() as session:
        yield session


async def using_session(
    session: AsyncSession | None,
    op: Callable[[AsyncSession], Awaitable[T]],
) -> T:
    """Run ``op`` on a caller-provided session or a short-lived owned session."""
    if session is not None:
        return await op(session)
    async with session_scope() as owned:
        return await op(owned)


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
    close_psycopg_pool()


def reset_engine_cache() -> None:
    """Drop cached engine/session factory (pytest isolation)."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None
    close_psycopg_pool()


async def ping_database() -> None:
    """Warm the pool and verify connectivity (startup / health)."""
    engine = get_engine()
    async with engine.connect() as conn:
        await conn.execute(text(PING))
