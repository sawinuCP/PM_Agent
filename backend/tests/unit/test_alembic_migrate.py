"""Alembic URL helpers."""

from __future__ import annotations

from app.db.migrate import is_postgres_url, sync_database_url


def test_sync_database_url_asyncpg() -> None:
    assert sync_database_url("postgresql+asyncpg://u:p@localhost/db") == (
        "postgresql+psycopg://u:p@localhost/db"
    )


def test_sync_database_url_plain_postgres() -> None:
    assert sync_database_url("postgresql://u:p@localhost/db") == (
        "postgresql+psycopg://u:p@localhost/db"
    )


def test_sync_database_url_sqlite_unchanged() -> None:
    url = "sqlite+aiosqlite:///./data/test.db"
    assert sync_database_url(url) == url


def test_is_postgres_url() -> None:
    assert is_postgres_url("postgresql+asyncpg://localhost/x")
    assert not is_postgres_url("sqlite+aiosqlite:///./x")
