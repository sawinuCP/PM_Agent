"""Unit tests for platform-local tools and manager integration."""

from __future__ import annotations

import pytest

from app.mcp_integration.local_tools import run_python_sync
from app.mcp_integration.manager import MCPManager
from app.models.plan import AgentTemplate


def test_run_python_sync_executes_simple_code() -> None:
    payload = run_python_sync("result = 2 + 2", timeout_s=5)
    assert payload["status"] == "ok"
    assert "4" in (payload["result"] or "")


def test_run_python_sync_rejects_oversized_code() -> None:
    payload = run_python_sync("x = 1\n" * 40_000, timeout_s=5, max_code_chars=100)
    assert payload["status"] == "error"
    assert "exceeds" in (payload.get("error") or "")


@pytest.mark.asyncio
async def test_manager_registers_local_run_python() -> None:
    manager = MCPManager([])
    await manager.start()
    try:
        catalog = manager.tool_catalog()
        assert "code-sandbox" in catalog
        assert any(entry["name"] == "run_python" for entry in catalog["code-sandbox"])
        health = await manager.health()
        assert health["code-sandbox"]["transport"] == "local"
        assert health["code-sandbox"]["status"] == "up"
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_manager_calls_local_run_python() -> None:
    from app.models import db

    await db.init_schema()
    manager = MCPManager([])
    await manager.start()
    template = AgentTemplate(
        name="data-scientist",
        version=1,
        description="test",
        capabilities=[],
        system_prompt="test",
        allowed_mcp_servers=["code-sandbox"],
        allowed_tools=["run_python"],
    )
    try:
        outcome = await manager.call_tool_for_agent(
            tool_name="run_python",
            args={"code": "result = {'ok': True}"},
            agent_template=template,
            step_id="s1",
            run_id="",
        )
        assert outcome["status"] == "ok"
        assert "ok" in outcome["digest"].lower() or "True" in outcome["digest"]
    finally:
        await manager.shutdown()
