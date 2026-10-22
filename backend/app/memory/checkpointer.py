"""Checkpointer wiring (LangGraph state persistence per thread_id).

SQLite (aiosqlite) is the keyless default; Postgres activates via DATABASE_URL.
Both satisfy: sessions survive restarts, runs are resumable, interrupt() state
is durable (HITL).

Postgres uses the SYNC PostgresSaver wrapped in an async adapter: psycopg's
native async mode requires WindowsSelectorEventLoopPolicy, which conflicts
with uvicorn's Proactor loop (needed for MCP stdio subprocesses on Windows),
and the sync saver ships without async methods in this langgraph-checkpoint
version — so the adapter delegates each call to the sync saver via
asyncio.to_thread. All state stays durable in Postgres; single threaded
sync access is safe to serialize.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.core.settings import get_settings
from app.models.session import _ensure_sqlite_dir

logger = logging.getLogger(__name__)


class _SyncToAsyncCheckpointer(BaseCheckpointSaver):
    """Async facade over the sync PostgresSaver (thread-delegated).

    Subclasses BaseCheckpointSaver so LangGraph's compile-time isinstance
    validation accepts it; async methods delegate to the sync saver via
    asyncio.to_thread (psycopg-async is incompatible with the Proactor loop).
    """

    def __init__(self, sync_saver) -> None:  # noqa: ANN001 - saver type
        super().__init__()
        self._sync = sync_saver
        self.serde = sync_saver.serde

    def get_tuple(self, config):  # noqa: ANN001, ANN201
        return self._sync.get_tuple(config)

    def put(self, config, checkpoint, metadata, new_versions):  # noqa: ANN001, ANN201
        return self._sync.put(config, checkpoint, metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=""):  # noqa: ANN001, ANN201
        return self._sync.put_writes(config, writes, task_id, task_path)

    def list(self, config=None, *, filter=None, before=None, limit=None):  # noqa: A002, ANN001, ANN201
        return self._sync.list(config, filter=filter, before=before, limit=limit)

    def delete_thread(self, thread_id: str) -> None:
        self._sync.delete_thread(thread_id)

    async def aget_tuple(self, config):  # noqa: ANN001, ANN201
        return await asyncio.to_thread(self._sync.get_tuple, config)

    async def aput(self, config, checkpoint, metadata, new_versions):  # noqa: ANN001, ANN201
        return await asyncio.to_thread(
            self._sync.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):  # noqa: ANN001, ANN201
        return await asyncio.to_thread(
            self._sync.put_writes, config, writes, task_id, task_path)

    async def alist(self, config=None, *, filter=None, before=None, limit=None):  # noqa: A002, ANN001, ANN201
        def _collect():  # noqa: ANN202
            return list(self._sync.list(config, filter=filter, before=before,
                                        limit=limit))
        return await asyncio.to_thread(_collect)

    async def adelete_thread(self, thread_id: str) -> None:
        return await asyncio.to_thread(self._sync.delete_thread, thread_id)


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[object]:
    """Yield an initialized checkpointer for the configured backend."""
    settings = get_settings()
    if settings.database_url.startswith("postgresql"):
        # PostgresSaver speaks psycopg conninfo (postgresql://...), not
        # SQLAlchemy dialect URLs (+asyncpg) — normalize.
        conninfo = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        saver_cm = PostgresSaver.from_conn_string(conninfo)
        sync_saver = saver_cm.__enter__()
        try:
            await asyncio.to_thread(sync_saver.setup)
            logger.info("postgres checkpointer ready (sync saver, async adapter)")
            yield _SyncToAsyncCheckpointer(sync_saver)
        finally:
            try:
                saver_cm.__exit__(None, None, None)
            except BaseException:  # noqa: BLE001 - best-effort teardown
                logger.warning("postgres checkpointer close failed", exc_info=True)
    else:
        path = settings.checkpoint_db_path
        _ensure_sqlite_dir(f"sqlite+aiosqlite:///{path}")
        async with aiosqlite.connect(path) as conn:
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            logger.info("sqlite checkpointer ready at %s", path)
            yield saver
