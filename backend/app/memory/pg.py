"""Shared Postgres/pgvector helpers and a process-wide psycopg connection pool."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.core.settings import get_settings

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

_psycopg_pool: ConnectionPool | None = None


def pg_conninfo() -> str:
    """psycopg DSN from SQLAlchemy's asyncpg URL."""
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


def vector_literal(vector: list[float]) -> str:
    """pgvector text format: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{v:.7f}" for v in vector) + "]"


def get_psycopg_pool() -> ConnectionPool:
    """Lazy process-wide pool for sync pgvector queries (memory modules)."""
    global _psycopg_pool
    if _psycopg_pool is None:
        from psycopg_pool import ConnectionPool

        settings = get_settings()
        _psycopg_pool = ConnectionPool(
            pg_conninfo(),
            min_size=settings.db_psycopg_pool_min,
            max_size=settings.db_psycopg_pool_max,
            open=True,
            kwargs={"connect_timeout": 10},
        )
        logger.info(
            "psycopg pool ready min=%s max=%s",
            settings.db_psycopg_pool_min,
            settings.db_psycopg_pool_max,
        )
    return _psycopg_pool


def close_psycopg_pool() -> None:
    global _psycopg_pool
    if _psycopg_pool is not None:
        try:
            _psycopg_pool.close()
        except Exception:  # noqa: BLE001 - teardown is best-effort
            logger.warning("psycopg pool close failed", exc_info=True)
        _psycopg_pool = None
