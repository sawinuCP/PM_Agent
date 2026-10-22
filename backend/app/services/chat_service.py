"""Chat/run orchestration — thin API over the orchestration pipeline."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.ai.tokens import count_tokens
from app.core.clock import utcnow
from app.core.errors import AppError
from app.core.logging import bind_run_context
from app.models import db
from app.models.session import run_write_scope
from app.observability.bus import EventBus
from app.orchestration.workflow import Executor, find_pending_clarify_run, run_turn

logger = logging.getLogger(__name__)

_BACKGROUND: dict[str, asyncio.Task] = {}


@dataclass
class ChatOutcome:
    run_id: str
    thread_id: str
    status: str
    path: str


async def handle_chat(session_id: str, message: str, *, executor: Executor,
                      bus: EventBus, platform: Any) -> ChatOutcome:
    """Start one run for the message; returns immediately with run identity."""
    session = await db.get_session_record(session_id)
    if session is None:
        raise AppError(f"session '{session_id}' not found",
                       safe_message="Session not found.")

    pending = await find_pending_clarify_run(session_id)
    if pending is not None:
        run_id = pending.id
        thread_id = session.thread_id
        bind_run_context(run_id=run_id, thread_id=thread_id)
        await db.add_message(session_id, "user", {"text": message}, run_id=run_id,
                             token_count=count_tokens(message))
        task = asyncio.create_task(
            _resume_clarify_pipeline(
                run_id=run_id, session_id=session_id, answer=message,
                executor=executor, bus=bus, platform=platform))
        _BACKGROUND[run_id] = task
        logger.info("clarify resume accepted: run=%s session=%s", run_id, session_id)
        return ChatOutcome(run_id=run_id, thread_id=thread_id, status="running",
                           path="clarify-resume")

    run_id = str(uuid4())
    thread_id = session.thread_id
    bind_run_context(run_id=run_id, thread_id=thread_id)

    async with run_write_scope() as db_sess:
        await db.add_message(
            session_id, "user", {"text": message}, run_id=run_id,
            token_count=count_tokens(message), session=db_sess,
        )
        if session.title == "New session":
            await db.update_session_record(
                session_id, session=db_sess, title=message[:80],
            )
        await db.create_run_record(
            session_id, thread_id, message, run_id=run_id, session=db_sess,
        )
    await bus.emit_run_started(run_id, message)

    task = asyncio.create_task(
        _run_pipeline(run_id=run_id, session_id=session_id, thread_id=thread_id,
                      message=message, executor=executor, bus=bus,
                      platform=platform))
    _BACKGROUND[run_id] = task
    logger.info("chat accepted: run=%s session=%s msg_len=%d",
                run_id, session_id, len(message))
    return ChatOutcome(run_id=run_id, thread_id=thread_id, status="running",
                       path="pending-router")


async def _run_pipeline(*, run_id: str, session_id: str, thread_id: str,
                        message: str, executor: Executor, bus: EventBus,
                        platform: Any) -> None:
    try:
        await run_turn(
            run_id=run_id, session_id=session_id, thread_id=thread_id,
            message=message, executor=executor, bus=bus, platform=platform)
    except Exception as exc:  # noqa: BLE001
        logger.exception("pipeline crashed for run=%s", run_id)
        safe = getattr(exc, "safe_message", "The request could not be completed.")
        await bus.emit_run_failed(run_id, safe)
        async with run_write_scope() as sess:
            await db.update_run_record(
                run_id, session=sess, status="failed", error=safe,
                finished_at=utcnow())
    finally:
        _BACKGROUND.pop(run_id, None)


async def _resume_clarify_pipeline(
    *, run_id: str, session_id: str, answer: str,
    executor: Executor, bus: EventBus, platform: Any,
) -> None:
    try:
        active = await executor.resume_with_clarification(
            run_id, answer, platform=platform)
        if active is None:
            await bus.emit_run_failed(run_id, "Could not resume the paused run.")
            async with run_write_scope() as sess:
                await db.update_run_record(
                    run_id, session=sess, status="failed",
                    error="Could not resume the paused run.",
                    finished_at=utcnow())
        elif active.status == "awaiting_clarification":
            return
    except Exception as exc:  # noqa: BLE001
        logger.exception("clarify resume crashed for run=%s", run_id)
        safe = getattr(exc, "safe_message", "The request could not be completed.")
        await bus.emit_run_failed(run_id, safe)
        async with run_write_scope() as sess:
            await db.update_run_record(
                run_id, session=sess, status="failed", error=safe,
                finished_at=utcnow())
    finally:
        _BACKGROUND.pop(run_id, None)
