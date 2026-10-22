"""Async SQLAlchemy engine/session and ORM tables.

SQLite+aiosqlite is the keyless default; Postgres (with pgvector for memory)
activates via ``DATABASE_URL``. SQLite uses ``create_all``; Postgres runs
Alembic revisions (``backend/alembic``) then reconciles pgvector dimensions.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import declarative_base

from app.core.clock import utcnow
from app.core.settings import get_settings
from app.db.migrate import run_alembic_upgrade
from app.db.sql.postgres_admin import (
    DROP_EPISODIC_RUNS,
    DROP_SEMANTIC_MEMORIES,
    GOAL_EMBEDDING_COLUMN_TYPE,
    REGCLASS_EPISODIC_RUNS,
)
from app.db.sql.postgres_ddl import (
    postgres_memory_table_ddl,
)
from app.db.sql.sqlite_migrations import (
    SQLITE_ADD_CONVERSATION_SUMMARY,
    SQLITE_ADD_MEMORY_WINDOW_TOKENS,
    SQLITE_PRAGMA_TABLE_INFO_SESSIONS,
)
from app.models.session import (  # noqa: F401 — re-exported for db module callers
    dispose_engine,
    get_engine,
    ping_database,
    reset_engine_cache,
    using_session,
)

logger = logging.getLogger(__name__)

Base = declarative_base()


def _uuid() -> str:
    return str(uuid4())


class SessionRecord(Base):
    __tablename__ = "sessions"
    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(64), nullable=False, default="demo")
    title = Column(String(255), nullable=False, default="New session")
    thread_id = Column(String(36), nullable=False, default=_uuid)
    status = Column(String(20), nullable=False, default="active")
    message_count = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    total_cost_usd = Column(Float, nullable=False, default=0.0)
    conversation_summary = Column(Text, nullable=True)
    memory_window_tokens = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class Message(Base):
    __tablename__ = "messages"
    id = Column(String(36), primary_key=True, default=_uuid)
    session_id = Column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id = Column(String(36), nullable=True)
    role = Column(String(20), nullable=False)
    content = Column(JSON, nullable=False)
    token_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Run(Base):
    __tablename__ = "runs"
    id = Column(String(36), primary_key=True, default=_uuid)
    session_id = Column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    thread_id = Column(String(36), nullable=False, index=True)
    goal_text = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, default="pending")
    plan = Column(JSON, nullable=True)
    result_summary = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    token_usage = Column(JSON, nullable=True)
    cost_usd = Column(Float, nullable=False, default=0.0)
    started_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class Trace(Base):
    __tablename__ = "traces"
    id = Column(String(36), primary_key=True, default=_uuid)
    run_id = Column(String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    event_type = Column(String(40), nullable=False)
    agent_name = Column(String(100), nullable=True)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class ToolResult(Base):
    __tablename__ = "tool_results"
    id = Column(String(36), primary_key=True, default=_uuid)
    run_id = Column(String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    step_id = Column(String(20), nullable=False)
    tool_name = Column(String(100), nullable=False)
    args_digest = Column(JSON, nullable=False)
    payload = Column(JSON, nullable=True)
    digest = Column(Text, nullable=False)
    byte_size = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="ok")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)



async def init_schema() -> None:
    """Create app tables (SQLite metadata or Postgres raw DDL)."""
    engine = get_engine()
    if str(engine.url).startswith("sqlite"):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await _migrate_session_memory_columns(conn)
        logger.info("sqlite schema ensured at %s", engine.url)
        return
    settings = get_settings()
    if settings.db_auto_migrate:
        await asyncio.to_thread(run_alembic_upgrade)
    dim = settings.embeddings_dim
    async with engine.begin() as conn:
        await _reconcile_pgvector_dim(conn, dim, text)
        exists = (await conn.execute(text(REGCLASS_EPISODIC_RUNS))).scalar()
        if exists is None:
            for statement in postgres_memory_table_ddl(dim):
                await conn.execute(text(statement))
    logger.info("postgres schema at alembic head (memory dim=%d)", dim)


async def _migrate_session_memory_columns(conn: Any) -> None:
    """Add L2 memory columns to existing SQLite sessions tables."""
    rows = (await conn.execute(text(SQLITE_PRAGMA_TABLE_INFO_SESSIONS))).fetchall()
    cols = {row[1] for row in rows}
    if "conversation_summary" not in cols:
        await conn.execute(text(SQLITE_ADD_CONVERSATION_SUMMARY))
    if "memory_window_tokens" not in cols:
        await conn.execute(text(SQLITE_ADD_MEMORY_WINDOW_TOKENS))


async def _reconcile_pgvector_dim(conn: Any, dim: int, text_fn: Any) -> None:
    """Recreate memory tables when leftover columns are the old 384-d space.

    Voyage writes 1024-d vectors. CREATE TABLE IF NOT EXISTS keeps a stale
    384-d column from an earlier boot, which then fails every episodic write.
    """
    exists = (await conn.execute(text_fn(REGCLASS_EPISODIC_RUNS))).scalar()
    if exists is None:
        return
    fmt = (await conn.execute(text_fn(GOAL_EMBEDDING_COLUMN_TYPE))).scalar()
    if fmt is None or f"({dim})" in str(fmt):
        return
    logger.warning(
        "pgvector column is %s but EMBEDDINGS_DIM=%s; recreating memory tables",
        fmt, dim)
    await conn.execute(text_fn(DROP_SEMANTIC_MEMORIES))
    await conn.execute(text_fn(DROP_EPISODIC_RUNS))



TraceWriteRow = tuple[str, int, str, str | None, dict[str, Any]]


async def save_traces_batch(
    rows: list[TraceWriteRow],
    *,
    session: AsyncSession | None = None,
) -> None:
    """Persist many trace rows in one DB session/commit."""
    if not rows:
        return

    async def _run(sess: AsyncSession) -> None:
        for run_id, seq, event_type, agent_name, payload in rows:
            sess.add(Trace(
                run_id=run_id,
                seq=seq,
                event_type=event_type,
                agent_name=agent_name,
                payload=payload,
            ))
        await sess.commit()

    await using_session(session, _run)


async def save_trace(run_id: str, seq: int, event_type: str, agent_name: str | None,
                     payload: dict[str, Any], *, session: AsyncSession | None = None) -> None:
    await save_traces_batch(
        [(run_id, seq, event_type, agent_name, payload)],
        session=session,
    )


async def list_traces(run_id: str, *, session: AsyncSession | None = None) -> list[Trace]:
    async def _run(sess: AsyncSession) -> list[Trace]:
        result = await sess.execute(
            Trace.__table__.select().where(Trace.run_id == run_id).order_by(Trace.seq)
        )
        return [Trace(**row._mapping) for row in result]  # type: ignore[arg-type]

    return await using_session(session, _run)


async def create_session_record(
    title: str,
    user_id: str,
    *,
    session: AsyncSession | None = None,
) -> SessionRecord:
    async def _run(sess: AsyncSession) -> SessionRecord:
        record = SessionRecord(title=title, user_id=user_id)
        sess.add(record)
        await sess.commit()
        await sess.refresh(record)
        return record

    return await using_session(session, _run)


async def list_session_records(*, session: AsyncSession | None = None) -> list[SessionRecord]:
    async def _run(sess: AsyncSession) -> list[SessionRecord]:
        result = await sess.execute(
            select(SessionRecord).order_by(SessionRecord.updated_at.desc())
        )
        return list(result.scalars().all())

    return await using_session(session, _run)


async def get_session_record(
    session_id: str,
    *,
    session: AsyncSession | None = None,
) -> SessionRecord | None:
    async def _run(sess: AsyncSession) -> SessionRecord | None:
        return await sess.get(SessionRecord, session_id)

    return await using_session(session, _run)


async def update_session_record(
    session_id: str,
    *,
    session: AsyncSession | None = None,
    **fields: Any,
) -> None:
    async def _run(sess: AsyncSession) -> None:
        record = await sess.get(SessionRecord, session_id)
        if record is None:
            return
        for key, value in fields.items():
            setattr(record, key, value)
        record.updated_at = utcnow()
        await sess.commit()

    await using_session(session, _run)


async def delete_session_record(session_id: str, *, session: AsyncSession | None = None) -> bool:
    async def _run(sess: AsyncSession) -> bool:
        record = await sess.get(SessionRecord, session_id)
        if record is None:
            return False
        await sess.delete(record)
        await sess.commit()
        return True

    return await using_session(session, _run)


async def add_message(
    session_id: str,
    role: str,
    content: dict[str, Any],
    *,
    run_id: str | None = None,
    token_count: int = 0,
    session: AsyncSession | None = None,
) -> Message:
    async def _run(sess: AsyncSession) -> Message:
        msg = Message(session_id=session_id, role=role, content=content,
                      run_id=run_id, token_count=token_count)
        sess.add(msg)
        record = await sess.get(SessionRecord, session_id)
        if record is not None:
            record.message_count += 1
            record.total_tokens += token_count
            record.updated_at = utcnow()
        await sess.commit()
        await sess.refresh(msg)
        return msg

    return await using_session(session, _run)


async def list_messages(
    session_id: str,
    limit: int = 100,
    offset: int = 0,
    *,
    session: AsyncSession | None = None,
) -> list[Message]:
    async def _run(sess: AsyncSession) -> list[Message]:
        result = await sess.execute(
            select(Message).where(Message.session_id == session_id)
            .order_by(Message.created_at).offset(offset).limit(limit)
        )
        return list(result.scalars().all())

    return await using_session(session, _run)


async def create_run_record(
    session_id: str,
    thread_id: str,
    goal_text: str,
    run_id: str | None = None,
    *,
    session: AsyncSession | None = None,
) -> Run:
    async def _run(sess: AsyncSession) -> Run:
        run = Run(id=run_id or _uuid(), session_id=session_id, thread_id=thread_id,
                  goal_text=goal_text, status="running")
        sess.add(run)
        await sess.commit()
        await sess.refresh(run)
        return run

    return await using_session(session, _run)


async def add_session_cost(
    session_id: str,
    cost_usd: float,
    *,
    session: AsyncSession | None = None,
) -> None:
    """Add a completed run's model cost to the session total."""
    if not cost_usd:
        return

    async def _run(sess: AsyncSession) -> None:
        record = await sess.get(SessionRecord, session_id)
        if record is None:
            return
        record.total_cost_usd = float(record.total_cost_usd or 0.0) + float(cost_usd)
        record.updated_at = utcnow()
        await sess.commit()

    await using_session(session, _run)


async def update_run_record(
    run_id: str,
    *,
    session: AsyncSession | None = None,
    **fields: Any,
) -> None:
    async def _run(sess: AsyncSession) -> None:
        run = await sess.get(Run, run_id)
        if run is None:
            return
        for key, value in fields.items():
            setattr(run, key, value)
        await sess.commit()

    await using_session(session, _run)


async def list_runs_for_session(
    session_id: str,
    limit: int = 50,
    *,
    session: AsyncSession | None = None,
) -> list[Run]:
    async def _run(sess: AsyncSession) -> list[Run]:
        result = await sess.execute(
            select(Run).where(Run.session_id == session_id)
            .order_by(Run.started_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    return await using_session(session, _run)


async def list_runs_since(
    since: datetime,
    limit: int = 2000,
    *,
    session: AsyncSession | None = None,
) -> list[Run]:
    """Runs started at or after ``since`` (oldest first) for telemetry roll-ups."""
    async def _run(sess: AsyncSession) -> list[Run]:
        result = await sess.execute(
            select(Run).where(Run.started_at >= since)
            .order_by(Run.started_at.asc()).limit(limit)
        )
        return list(result.scalars().all())

    return await using_session(session, _run)


async def get_run_record(run_id: str, *, session: AsyncSession | None = None) -> Run | None:
    async def _run(sess: AsyncSession) -> Run | None:
        return await sess.get(Run, run_id)

    return await using_session(session, _run)


async def save_tool_result(
    run_id: str,
    step_id: str,
    tool_name: str,
    args_digest: dict[str, Any],
    payload: dict[str, Any],
    digest: str,
    byte_size: int,
    status: str,
    *,
    session: AsyncSession | None = None,
) -> ToolResult:
    async def _run(sess: AsyncSession) -> ToolResult:
        row = ToolResult(run_id=run_id, step_id=step_id, tool_name=tool_name,
                         args_digest=args_digest, payload=payload, digest=digest,
                         byte_size=byte_size, status=status)
        sess.add(row)
        await sess.commit()
        await sess.refresh(row)
        return row

    return await using_session(session, _run)


async def get_tool_result(
    tool_result_id: str,
    *,
    session: AsyncSession | None = None,
) -> ToolResult | None:
    async def _run(sess: AsyncSession) -> ToolResult | None:
        return await sess.get(ToolResult, tool_result_id)

    return await using_session(session, _run)