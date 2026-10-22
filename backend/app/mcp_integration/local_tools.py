"""Platform-local tools (not MCP servers) — same catalog/call contract as remote MCP.

`run_python` delegates to ``mcp_servers.sandbox.runtime.execute`` in a worker
thread so the asyncio event loop stays responsive.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp_servers.sandbox.runtime import execute

logger = logging.getLogger(__name__)

LOCAL_SERVER_NAME = "code-sandbox"

RUN_PYTHON_DESCRIPTION = (
    "Execute Python in an isolated interpreter: no network, no access to the "
    "platform process or DB, restricted imports, hard time caps. Provide the "
    "complete analysis code; assign the final answer to a variable named "
    "`result`. Returns stdout, result, execution time and status."
)

RUN_PYTHON_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": {"type": "string", "description": "Complete Python analysis script."},
        "timeout_s": {
            "type": "integer",
            "description": "Per-call timeout in seconds (capped by platform policy).",
            "default": 15,
        },
    },
    "required": ["code"],
}

MAX_CODE_CHARS = 32_768


def local_tool_catalog() -> dict[str, dict[str, Any]]:
    """Static catalog entries keyed by tool name."""
    return {
        "run_python": {
            "name": "run_python",
            "description": RUN_PYTHON_DESCRIPTION,
            "input_schema": RUN_PYTHON_INPUT_SCHEMA,
            "server": LOCAL_SERVER_NAME,
            "sensitive": False,
        },
    }


def run_python_sync(
    code: str,
    timeout_s: int = 15,
    *,
    max_runtime_s: int = 25,
    max_code_chars: int = MAX_CODE_CHARS,
) -> dict[str, Any]:
    if len(code) > max_code_chars:
        return {
            "stdout": "",
            "result": None,
            "ms": 0,
            "status": "error",
            "error": f"code exceeds the {max_code_chars}-character limit",
        }
    timeout = max(1, min(int(timeout_s), max_runtime_s))
    logger.info("run_python: %d chars, timeout %ds", len(code), timeout)
    outcome = execute(code, timeout_s=float(timeout))
    payload: dict[str, Any] = {
        "stdout": outcome.stdout,
        "result": outcome.result,
        "ms": outcome.ms,
        "status": outcome.status,
    }
    if outcome.error:
        payload["error"] = outcome.error
    return payload


async def invoke_run_python(
    code: str,
    timeout_s: int = 15,
    *,
    max_runtime_s: int = 25,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        run_python_sync,
        code,
        timeout_s,
        max_runtime_s=max_runtime_s,
    )
