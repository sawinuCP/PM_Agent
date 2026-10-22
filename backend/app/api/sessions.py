"""Session CRUD and message history."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_api_token
from app.core.settings import get_settings
from app.memory.session_stats import get_session_context_stats
from app.models import db
from app.models.api import SessionCreate, SessionOut
from app.models.memory import SessionContextStats
from app.models.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


def _to_out(record: db.SessionRecord) -> SessionOut:
    return SessionOut(
        id=record.id, title=record.title, thread_id=record.thread_id,
        status=record.status, message_count=record.message_count,
        total_tokens=record.total_tokens, total_cost_usd=record.total_cost_usd,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
    )


@router.post("/sessions", response_model=SessionOut, status_code=201)
async def create_session(
        body: SessionCreate, _auth: None = Depends(require_api_token)) -> SessionOut:
    settings = get_settings()
    record = await db.create_session_record(title=body.title,
                                            user_id=settings.demo_user_id)
    logger.info("session created: %s", record.id)
    return _to_out(record)


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions() -> list[SessionOut]:
    records = await db.list_session_records()
    return [_to_out(r) for r in records if r.message_count > 0]


@router.get("/sessions/{session_id}", response_model=SessionOut)
async def get_session(session_id: str) -> SessionOut:
    record = await db.get_session_record(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _to_out(record)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
        session_id: str, _auth: None = Depends(require_api_token)) -> None:
    """Delete session; derived memories remain traceable via source_run_id."""
    deleted = await db.delete_session_record(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/sessions/{session_id}/runs")
async def list_session_runs(
    session_id: str,
    db_sess: AsyncSession = Depends(get_db),
) -> dict:
    """Runs for a session (newest first) — powers the UI run-history/replay."""
    record = await db.get_session_record(session_id, session=db_sess)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    runs = await db.list_runs_for_session(session_id, session=db_sess)
    return {
        "runs": [
            {"id": r.id, "status": r.status, "goal": r.goal_text,
             "cost_usd": r.cost_usd,
             "started_at": r.started_at.isoformat(),
             "finished_at": r.finished_at.isoformat() if r.finished_at else None}
            for r in runs
        ]
    }


@router.get("/sessions/{session_id}/context-stats", response_model=SessionContextStats)
async def session_context_stats(session_id: str) -> SessionContextStats:
    """Current session context fill vs GLM input budget and summarization threshold."""
    stats = await get_session_context_stats(session_id)
    if stats is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return stats


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: str,
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db_sess: AsyncSession = Depends(get_db),
) -> dict:
    record = await db.get_session_record(session_id, session=db_sess)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = await db.list_messages(
        session_id, limit=limit, offset=offset, session=db_sess,
    )
    return {
        "messages": [
            {"id": m.id, "role": m.role, "content": m.content,
             "run_id": m.run_id, "token_count": m.token_count,
             "created_at": m.created_at.isoformat()}
            for m in messages
        ],
        "count": len(messages),
    }
