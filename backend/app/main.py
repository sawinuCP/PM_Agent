"""FastAPI application factory and lifespan hooks.

OpenAPI docs at /docs. Typed AppErrors render safe messages; unexpected
errors log details but return a generic 500 without internal leakage.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api import chat, runs, sessions, system
from app.core.cors import configure_cors
from app.core.errors import AppError, MCPError
from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.models import db
from app.services import chat_service
from app.services.platform import build_platform, set_platform

_MAX_BODY_BYTES = 256_000


class _LimitBodySize(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > _MAX_BODY_BYTES:
            return JSONResponse(status_code=413,
                                content={"error": "Request body too large."})
        return await call_next(request)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(get_settings().log_level, get_settings().log_json)

    await db.init_schema()
    await db.ping_database()
    platform = await build_platform()
    set_platform(platform)
    logger.info("platform started")
    try:
        yield
    finally:
        for task in chat_service._BACKGROUND.values():
            task.cancel()
        await platform.shutdown()
        set_platform(None)
        logger.info("platform stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="Dynamic, configuration-driven multi-agent orchestration "
                    "platform (MCP + LangGraph + FastAPI).",
        lifespan=lifespan,
    )
    app.add_middleware(_LimitBodySize)
    configure_cors(app, settings)

    app.include_router(system.router, prefix=settings.api_prefix, tags=["system"])
    app.include_router(sessions.router, prefix=settings.api_prefix, tags=["sessions"])
    app.include_router(chat.router, prefix=settings.api_prefix, tags=["chat"])
    app.include_router(runs.router, prefix=settings.api_prefix, tags=["runs"])

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        logger.warning("app error on %s: %s", request.url.path, exc.detail)
        status = 502 if isinstance(exc, MCPError) else 400
        return JSONResponse(status_code=status, content={"error": exc.safe_message})

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(status_code=500,
                            content={"error": "An internal error occurred."})
    return app


app = create_app()
