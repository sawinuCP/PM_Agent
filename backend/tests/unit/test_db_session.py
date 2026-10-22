"""Database session pooling and scoped session tests."""

from __future__ import annotations

import pytest
from sqlalchemy.pool import NullPool

from app.models import db
from app.models.session import get_engine, ping_database, reset_engine_cache, run_write_scope, session_scope


@pytest.mark.asyncio
async def test_ping_database(tmp_path, monkeypatch) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'ping.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    from app.core.settings import get_settings

    get_settings.cache_clear()
    reset_engine_cache()
    await db.init_schema()
    await ping_database()


@pytest.mark.asyncio
async def test_session_scope_rolls_back_on_error(tmp_path, monkeypatch) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'rollback.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    from app.core.settings import get_settings

    get_settings.cache_clear()
    reset_engine_cache()
    await db.init_schema()

    with pytest.raises(RuntimeError, match="boom"):
        async with session_scope() as sess:
            sess.add(db.SessionRecord(title="x", user_id="demo"))
            raise RuntimeError("boom")

    records = await db.list_session_records()
    assert records == []


def test_sqlite_uses_null_pool(tmp_path, monkeypatch) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    from app.core.settings import get_settings

    get_settings.cache_clear()
    reset_engine_cache()
    engine = get_engine()
    assert isinstance(engine.sync_engine.pool, NullPool)


@pytest.mark.asyncio
async def test_run_write_scope_reuses_injected_session(tmp_path, monkeypatch) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'write_scope.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    from app.core.settings import get_settings

    get_settings.cache_clear()
    reset_engine_cache()
    await db.init_schema()

    async with session_scope() as outer:
        async with run_write_scope(outer) as inner:
            assert inner is outer
            await db.create_session_record("scoped", "demo", session=inner)
    records = await db.list_session_records()
    assert len(records) == 1
