"""Run endpoints: detail, SSE stream, trace replay, tool drill-down, and HITL approval."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.ai.tokens import count_tokens
from app.core.security import require_api_token
from app.models import db
from app.models.api import ApprovalRequest, ClarifyRequest
from app.models.session import get_db
from app.services.platform import get_platform

logger = logging.getLogger(__name__)
router = APIRouter()

_TERMINAL_TYPES = {"run_finished", "run_failed"}
_HEARTBEAT_S = 15.0
_NOT_FOUND = "Run not found"


@router.get("/runs/{run_id}")
async def run_detail(
    run_id: str,
    db_sess: AsyncSession = Depends(get_db),
) -> dict:
    run = await db.get_run_record(run_id, session=db_sess)
    if run is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return {
        "id": run.id, "session_id": run.session_id, "thread_id": run.thread_id,
        "goal": run.goal_text, "status": run.status, "plan": run.plan,
        "result": run.result_summary, "error": run.error,
        "token_usage": run.token_usage, "cost_usd": run.cost_usd,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: str,
    db_sess: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    """Live SSE trace stream with replay-then-live semantics + heartbeats."""
    run = await db.get_run_record(run_id, session=db_sess)
    if run is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    event_bus = get_platform().bus

    async def _stream():  # noqa: ANN202
        queue = event_bus.subscribe(run_id)
        try:
            replayed = await db.list_traces(run_id)
            for row in replayed:
                yield {"data": json.dumps(row.payload, default=str)}
            if any(row.event_type in _TERMINAL_TYPES for row in replayed):
                return
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_S)
                except TimeoutError:
                    yield {"comment": "keep-alive"}
                    continue
                yield {"data": json.dumps(payload, default=str)}
                if payload.get("type") in _TERMINAL_TYPES:
                    return
        finally:
            event_bus.unsubscribe(run_id, queue)

    return EventSourceResponse(_stream())


@router.get("/runs/{run_id}/traces")
async def run_traces(
    run_id: str,
    db_sess: AsyncSession = Depends(get_db),
) -> dict:
    """Full trace list for replay."""
    run = await db.get_run_record(run_id, session=db_sess)
    if run is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    rows = await db.list_traces(run_id, session=db_sess)
    return {"events": [row.payload for row in rows], "count": len(rows)}


@router.get("/runs/{run_id}/tool-results/{tool_result_id}")
async def tool_result_detail(
    run_id: str,
    tool_result_id: str,
    db_sess: AsyncSession = Depends(get_db),
) -> dict:
    """Full persisted tool payload."""
    row = await db.get_tool_result(tool_result_id, session=db_sess)
    if row is None or row.run_id != run_id:
        raise HTTPException(status_code=404, detail="Tool result not found")
    return {
        "id": row.id, "run_id": row.run_id, "step_id": row.step_id,
        "tool": row.tool_name, "args_digest": row.args_digest,
        "payload": row.payload, "digest": row.digest,
        "byte_size": row.byte_size, "status": row.status,
        "created_at": row.created_at.isoformat(),
    }


@router.post("/runs/{run_id}/approve")
async def approve(
    run_id: str,
    body: ApprovalRequest,
    db_sess: AsyncSession = Depends(get_db),
    _auth: None = Depends(require_api_token),
) -> dict:
    """HITL approval for a sensitive tool."""
    platform = get_platform()
    resolved = platform.executor.resolve_approval(run_id, body.approved)
    if not resolved:
        raise HTTPException(status_code=409,
                            detail="Run is not awaiting approval (or unknown)")
    await db.update_run_record(run_id, session=db_sess, status="running")
    return {"run_id": run_id, "approved": body.approved, "resumed": True}


@router.post("/runs/{run_id}/clarify")
async def clarify(
    run_id: str,
    body: ClarifyRequest,
    db_sess: AsyncSession = Depends(get_db),
    _auth: None = Depends(require_api_token),
) -> dict:
    """HITL clarify answer → re-plan and resume the paused run."""
    run = await db.get_run_record(run_id, session=db_sess)
    if run is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    if run.status != "awaiting_clarification":
        raise HTTPException(
            status_code=409,
            detail="Run is not awaiting clarification",
        )
    platform = get_platform()
    await db.add_message(
        run.session_id, "user", {"text": body.answer},
        run_id=run_id, token_count=count_tokens(body.answer),
        session=db_sess,
    )
    active = await platform.executor.resume_with_clarification(
        run_id, body.answer, platform=platform)
    if active is None:
        raise HTTPException(status_code=409, detail="Could not resume run")
    return {
        "run_id": run_id,
        "status": active.status,
        "resumed": active.status == "running",
    }
