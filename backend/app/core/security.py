"""Small, optional API boundary for non-local deployments.

The assessment demo remains keyless by default. Setting ``API_TOKEN`` enables
constant-time token checks on mutation and approval routes without changing
the agent or orchestration layers.
"""

from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.core.settings import get_settings

_chat_hits: dict[str, deque[float]] = defaultdict(deque)


def reset_chat_rate_limit() -> None:
    _chat_hits.clear()


def enforce_chat_rate_limit(session_id: str) -> None:
    """Reject burst /chat traffic that would burn paid inference."""
    limit = get_settings().chat_rate_limit_per_minute
    if limit <= 0:
        return
    now = time.monotonic()
    window = _chat_hits[session_id]
    cutoff = now - 60.0
    while window and window[0] <= cutoff:
        window.popleft()
    if len(window) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many chat requests. Try again shortly.",
        )
    window.append(now)


async def require_api_token(
    supplied: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    expected = get_settings().api_token
    if not expected:
        return
    if supplied is None or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid API credentials are required.",
            headers={"WWW-Authenticate": "API-Key"},
        )
