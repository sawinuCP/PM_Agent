"""System endpoints: health (liveness/readiness split), agents catalog, MCP health."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from app.ai.provider import get_provider, resolve_llm_provider
from app.memory.embeddings import active_provider as embeddings_provider
from app.models import db
from app.models.api import HealthOut, InferenceOut, ReadinessOut, TelemetryOut
from app.services.platform import get_platform
from app.services.telemetry import RangeName, collect_telemetry

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health/live", response_model=HealthOut)
async def liveness() -> HealthOut:
    """Process is up (no dependency checks — k8s liveness semantics)."""
    return HealthOut(status="ok")


@router.get("/health/ready", response_model=ReadinessOut)
async def readiness() -> ReadinessOut:
    """DB + MCP servers reachable (k8s readiness semantics)."""
    platform = get_platform()
    db_status = "up"
    try:
        await db.get_session_record("ping-probe")
    except Exception:  # noqa: BLE001 - readiness must report, not raise
        db_status = "down"
    servers = {
        name: state["status"]
        for name, state in (await platform.manager.health()).items()
    }
    status = "ok" if db_status == "up" and all(
        v == "up" for v in servers.values()) else "degraded"
    provider_name = resolve_llm_provider()
    return ReadinessOut(
        status=status, database=db_status, mcp_servers=servers,
        inference=InferenceOut(
            provider=provider_name,
            model=get_provider().usage_model(),
            live=provider_name == "openai",
            embeddings=embeddings_provider(),
        ),
    )


@router.get("/mcp/health")
async def mcp_health() -> dict:
    """Per-server MCP health roll-up incl. circuit-breaker states."""
    platform = get_platform()
    return {"servers": await platform.manager.health()}


@router.get("/telemetry", response_model=TelemetryOut)
async def telemetry(
    range: str = Query("week", pattern="^(day|week|month)$"),
) -> TelemetryOut:
    """Project-level traces, tokens, cost, and latency for day / week / month."""
    chosen: RangeName = range if range in {"day", "week", "month"} else "week"
    return TelemetryOut.model_validate(await collect_telemetry(chosen))


@router.get("/agents")
async def agents_catalog() -> dict:
    """Registry catalog: agents + capabilities + tools (Planner's menu)."""
    platform = get_platform()
    return {
        "agents": [entry.model_dump() for entry in platform.registry.capability_catalog()]
    }
