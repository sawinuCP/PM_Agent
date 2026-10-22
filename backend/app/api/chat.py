"""POST /chat — starts a run (business logic in services/chat_service)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.errors import AppError
from app.core.security import enforce_chat_rate_limit, require_api_token
from app.models.api import ChatAccepted, ChatRequest
from app.services import chat_service
from app.services.platform import get_platform

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/chat", response_model=ChatAccepted, status_code=202)
async def chat(body: ChatRequest, _auth: None = Depends(require_api_token)) -> ChatAccepted:
    platform = get_platform()
    enforce_chat_rate_limit(body.session_id)
    try:
        outcome = await chat_service.handle_chat(
            body.session_id, body.message, executor=platform.executor,
            bus=platform.bus, platform=platform)
    except AppError as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message) from exc
    return ChatAccepted(run_id=outcome.run_id, thread_id=outcome.thread_id,
                        status=outcome.status)
