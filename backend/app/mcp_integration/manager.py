"""MCP manager — connection lifecycle, tool binding, and mediated tool calls.

Responsibilities: TTL-respecting tool-catalog discovery, permission-filtered
toolset binding, resilience-wrapped tool calls, digests, and full-payload
persistence. Agents and the planner consume typed tool objects and digests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import Tool
from mcp.types import CallToolResult, TextContent

from app.core.errors import MCPError, MCPTimeoutError, MCPToolPermissionError
from app.core.settings import get_settings
from app.mcp_integration import local_tools, transports
from app.mcp_integration.digest import (
    args_digest,
    build_digest,
    normalize_payload,
    structured_preview,
)
from app.mcp_integration.resilience import CircuitBreaker, RetryPolicy, call_with_resilience
from app.models import db
from app.models.plan import AgentTemplate
from app.observability.events import EventType, make_event

logger = logging.getLogger(__name__)


@dataclass
class ToolCatalogEntry:
    name: str
    description: str
    input_schema: dict[str, Any]
    server: str
    sensitive: bool = False


@dataclass
class ServerState:
    config: transports.MCPServerConfig
    client: Any | None = None  # mcp.Client (entered async context manager)
    tools: dict[str, ToolCatalogEntry] = field(default_factory=dict)
    catalog_fetched_at: float = 0.0
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)


class MCPManager:
    """Owns every server connection; mediates bind + call for all agents."""

    def __init__(self, configs: list[transports.MCPServerConfig]) -> None:
        settings = get_settings()
        self._states: dict[str, ServerState] = {
            cfg.name: ServerState(
                config=cfg,
                retry_policy=RetryPolicy(max_retries=cfg.max_retries),
                breaker=CircuitBreaker(
                    failure_threshold=settings.mcp_circuit_failure_threshold,
                    recovery_seconds=settings.mcp_circuit_recovery_s,
                ),
            )
            for cfg in configs
        }
        self._local_tools: dict[str, ToolCatalogEntry] = {
            name: ToolCatalogEntry(**entry)
            for name, entry in local_tools.local_tool_catalog().items()
        }


    async def start(self) -> None:
        """Connect all servers and fetch initial tool catalogs (fail-fast)."""
        for name, state in self._states.items():
            try:
                state.client = await transports.open_client(state.config)
                await self._refresh_catalog(name, force=True)
            except Exception as exc:  # noqa: BLE001 - converted to typed error
                logger.exception("MCP server '%s' failed to start", name)
                await self.shutdown()
                raise MCPError(f"server '{name}' unreachable at startup: {exc}",
                               safe_message="A tool service could not be reached.") from exc
        logger.info("MCP manager started: %s", ", ".join(self._states))

    async def shutdown(self) -> None:
        # Clients are entered sequentially in start(), so unwind their nested
        # AnyIO context managers in LIFO order. Forward-order exits corrupt the
        # cancel-scope stack (especially visible with stdio on Windows).
        for name, state in reversed(self._states.items()):
            if state.client is not None:
                try:
                    await transports.close_client(state.client)
                except Exception:  # noqa: BLE001 - best-effort teardown
                    logger.warning("error closing MCP server '%s'", name)
                state.client = None
        logger.info("MCP manager shut down")

    async def _refresh_catalog(self, name: str, force: bool = False) -> None:
        state = self._states[name]
        if not force and state.tools:
            elapsed = time.monotonic() - state.catalog_fetched_at
            if elapsed < state.config.tool_cache_ttl_s:
                return
        client = state.client
        if client is None:
            return
        listed = await _list_all_tools(client)
        tools: dict[str, ToolCatalogEntry] = {}
        for tool in listed:
            tools[tool.name] = ToolCatalogEntry(
                name=tool.name,
                description=tool.description or "",
                input_schema=tool.input_schema or {"type": "object", "properties": {}},
                server=name,
                sensitive=tool.name in state.config.sensitive_tools,
            )
        state.tools = tools
        state.catalog_fetched_at = time.monotonic()
        logger.info("catalog refreshed for '%s': %d tools (%s)",
                    name, len(tools), ", ".join(sorted(tools)[:16]))

    def tool_catalog(self) -> dict[str, list[dict[str, Any]]]:
        """Live catalogs for the Planner (name/description/schema per server)."""
        catalogs = {
            name: [
                {"name": t.name, "description": t.description,
                 "input_schema": t.input_schema, "sensitive": t.sensitive}
                for t in state.tools.values()
            ]
            for name, state in self._states.items()
        }
        if self._local_tools:
            catalogs[local_tools.LOCAL_SERVER_NAME] = [
                {"name": t.name, "description": t.description,
                 "input_schema": t.input_schema, "sensitive": t.sensitive}
                for t in self._local_tools.values()
            ]
        return catalogs

    async def health(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for name, state in self._states.items():
            entry: dict[str, Any] = {
                "transport": state.config.transport,
                "breaker": state.breaker.snapshot(),
            }
            if state.config.transport == "streamable_http" and state.config.url:
                entry["url"] = state.config.url
            if state.client is None:
                entry["status"] = "down"
            elif state.tools:
                # Catalog cache (TTL-respecting) is the liveness signal: the
                # stateless spec has no session to probe, and a ping issued
                # from this task would violate anyio cancel-scope ownership
                # (the client was entered in the startup task). A TTL expiry
                # triggers a fresh tools/list on the next bind/call instead.
                entry["status"] = "up"
            else:
                entry["status"] = "unknown"
            out[name] = entry
        if self._local_tools:
            out[local_tools.LOCAL_SERVER_NAME] = {
                "transport": "local",
                "status": "up",
                "breaker": {"state": "closed", "consecutive_failures": 0,
                            "seconds_until_probe": 0.0},
            }
        return out

    def unavailable_servers(self) -> list[str]:
        """Server names that are not ready to serve tool calls."""
        down: list[str] = []
        for name, state in self._states.items():
            if state.client is None or not state.tools:
                down.append(name)
        return down



    async def bind_toolset(self, template: AgentTemplate, step_id: str = "unknown",
                           run_id: str = "", emit=None,
                           requested_tools: list[str] | None = None) -> list[Any]:
        """Permission-filtered LangChain tools for one spawned agent.

        Binding = (requested_tools or allowed_tools) ∩ discovered catalog
        tools, filtered by the template's allowlist. run_id/emit are captured per-run.
        """
        allowed_servers = set(template.allowed_mcp_servers)
        if requested_tools is not None:
            requested = set(requested_tools) & set(template.allowed_tools)
        else:
            requested = set(template.allowed_tools)
        discovered: dict[str, ToolCatalogEntry] = {}
        for state in self._states.values():
            if allowed_servers and state.config.name not in allowed_servers:
                continue
            discovered.update(state.tools)
        if not allowed_servers or local_tools.LOCAL_SERVER_NAME in allowed_servers:
            discovered.update(self._local_tools)
        missing = requested - set(discovered)
        if missing:
            logger.warning("agent '%s': tools missing from catalogs: %s",
                           template.name, sorted(missing))

        bound: list[Any] = []
        manager = self

        for tool_name in template.allowed_tools:
            if tool_name not in requested:
                continue
            entry = discovered.get(tool_name)
            if entry is None:
                continue

            def _make_caller(mgr: MCPManager, name: str, tmpl=template, step=step_id,
                             rid=run_id, on_event=emit):
                async def _call(**kwargs: Any) -> str:
                    outcome = await mgr.call_tool_for_agent(
                        tool_name=name, args=kwargs, agent_template=tmpl,
                        step_id=step, run_id=rid, emit=on_event,
                    )
                    return outcome["digest"]
                return _call

            bound.append(Tool.from_function(
                func=None,
                name=tool_name,
                description=f"[{entry.server}] {entry.description}",
                coroutine=_make_caller(manager, tool_name),
            ))
        logger.info("bound %d/%d tools to agent '%s' (step %s)",
                    len(bound), len(template.allowed_tools), template.name, step_id)
        return bound


    async def call_tool_for_agent(
        self, *, tool_name: str, args: dict[str, Any],
        agent_template: AgentTemplate, step_id: str,
        run_id: str = "", emit=None, mode: str = "",
    ) -> dict[str, Any]:
        """RBAC check → resilience-wrapped call → digest → async persistence.

        Returns digest + structured preview. Full payload persists asynchronously.
        """
        if tool_name not in agent_template.allowed_tools:
            raise MCPToolPermissionError(
                f"agent '{agent_template.name}' is not allowed tool '{tool_name}'")
        if tool_name in self._local_tools:
            return await self._call_local_tool_for_agent(
                tool_name=tool_name, args=args, agent_template=agent_template,
                step_id=step_id, run_id=run_id, emit=emit, mode=mode,
            )
        state = self._resolve_server(tool_name)
        if state is None or state.client is None:
            raise MCPError(f"tool '{tool_name}' not found in any live server catalog")
        await self._refresh_catalog(state.config.name)
        if tool_name not in state.tools:
            raise MCPError(f"tool '{tool_name}' disappeared from server '{state.config.name}'")

        args_summary = args_digest(args)
        if emit is not None:
            await emit(_tool_started_event(
                run_id, step_id, tool_name, args_summary, mode=mode))

        started = time.perf_counter()
        try:
            result: CallToolResult = await call_with_resilience(
                lambda: state.client.call_tool(tool_name, args),
                policy=state.retry_policy,
                breaker=state.breaker,
                timeout_s=state.config.timeout_s,
            )
        except MCPError as exc:
            return await self._persist_failure(
                run_id=run_id, step_id=step_id, tool_name=tool_name, args=args,
                exc=exc, started=started, emit=emit, mode=mode)
        ms = int((time.perf_counter() - started) * 1000)
        if getattr(result, "is_error", False):
            payload: Any = {"error": _result_text(result)}
            status = "error"
        else:
            payload = normalize_payload(_result_payload(result))
            status = _payload_status(payload)
        digest = build_digest(payload)
        preview = structured_preview(payload)
        size = len(json.dumps(payload, default=str))
        row = await db.save_tool_result(
            run_id=run_id, step_id=step_id, tool_name=tool_name,
            args_digest=args_summary, payload=payload if size <= 1_000_000 else None,
            digest=digest, byte_size=size, status=status)
        if emit is not None:
            await emit(_tool_finished_event(run_id, step_id, tool_name, status, ms,
                                            digest, row.id, mode=mode))
        logger.info("tool '%s' %s in %dms (%d bytes) step=%s",
                    tool_name, status, ms, size, step_id)
        return {
            "digest": digest, "preview": preview, "result_id": row.id,
            "status": status, "ms": ms,
        }

    async def _call_local_tool_for_agent(
        self, *, tool_name: str, args: dict[str, Any],
        agent_template: AgentTemplate, step_id: str,
        run_id: str = "", emit=None, mode: str = "",
    ) -> dict[str, Any]:
        settings = get_settings()
        args_summary = args_digest(args)
        if emit is not None:
            await emit(_tool_started_event(
                run_id, step_id, tool_name, args_summary, mode=mode))

        started = time.perf_counter()
        try:
            if tool_name == "run_python":
                code = str(args.get("code", ""))
                timeout_s = int(args.get("timeout_s", 15))
                payload = await asyncio.wait_for(
                    local_tools.invoke_run_python(
                        code,
                        timeout_s,
                        max_runtime_s=settings.sandbox_max_runtime_s,
                    ),
                    timeout=settings.sandbox_call_timeout_s,
                )
            else:
                raise MCPError(f"unknown local tool '{tool_name}'")
        except asyncio.TimeoutError:
            exc = MCPTimeoutError(
                f"local tool '{tool_name}' exceeded {settings.sandbox_call_timeout_s}s",
                safe_message="The code sandbox timed out.",
            )
            return await self._persist_failure(
                run_id=run_id, step_id=step_id, tool_name=tool_name, args=args,
                exc=exc, started=started, emit=emit, mode=mode)
        except MCPError as exc:
            return await self._persist_failure(
                run_id=run_id, step_id=step_id, tool_name=tool_name, args=args,
                exc=exc, started=started, emit=emit, mode=mode)
        except Exception as exc:  # noqa: BLE001 - sandbox failures are tool data
            wrapped = MCPError(
                f"local tool '{tool_name}' failed: {exc}",
                safe_message="The code sandbox failed to run.",
            )
            return await self._persist_failure(
                run_id=run_id, step_id=step_id, tool_name=tool_name, args=args,
                exc=wrapped, started=started, emit=emit, mode=mode)

        ms = int((time.perf_counter() - started) * 1000)
        status = _payload_status(payload)
        digest = build_digest(payload)
        preview = structured_preview(payload)
        size = len(json.dumps(payload, default=str))
        row = await db.save_tool_result(
            run_id=run_id, step_id=step_id, tool_name=tool_name,
            args_digest=args_summary, payload=payload if size <= 1_000_000 else None,
            digest=digest, byte_size=size, status=status)
        if emit is not None:
            await emit(_tool_finished_event(run_id, step_id, tool_name, status, ms,
                                            digest, row.id, mode=mode))
        logger.info("local tool '%s' %s in %dms (%d bytes) step=%s",
                    tool_name, status, ms, size, step_id)
        return {
            "digest": digest, "preview": preview, "result_id": row.id,
            "status": status, "ms": ms,
        }

    async def _persist_failure(self, *, run_id: str, step_id: str, tool_name: str,
                               args: dict[str, Any], exc: MCPError,
                               started: float, emit=None, mode: str = "") -> dict[str, Any]:
        ms = int((time.perf_counter() - started) * 1000)
        status = "timeout" if isinstance(exc, MCPTimeoutError) else "error"
        digest = f"tool '{tool_name}' failed: {exc.safe_message}"
        row = await db.save_tool_result(
            run_id=run_id, step_id=step_id, tool_name=tool_name,
            args_digest=args_digest(args), payload={"error": exc.detail},
            digest=digest, byte_size=len(exc.detail), status=status)
        if emit is not None:
            await emit(_tool_finished_event(run_id, step_id, tool_name,
                                            status, ms, digest, row.id, mode=mode))
        logger.warning("tool '%s' failed (%s) in %dms: %s",
                       tool_name, type(exc).__name__, ms, exc.detail)
        return {
            "digest": digest,
            "preview": {"error": exc.safe_message},
            "result_id": row.id,
            "status": status,
            "ms": ms,
        }

    def is_tool_sensitive(self, tool_name: str) -> bool:
        """True when the server registered the tool as sensitive (HITL gate)."""
        local = self._local_tools.get(tool_name)
        if local is not None:
            return local.sensitive
        state = self._resolve_server(tool_name)
        return bool(state and state.tools.get(tool_name) and
                    state.tools[tool_name].sensitive)

    def _resolve_server(self, tool_name: str) -> ServerState | None:
        if tool_name in self._local_tools:
            return None
        for state in self._states.values():
            if tool_name in state.tools:
                return state
        return None


def _payload_status(payload: Any) -> str:
    """Mark tool calls as error when the service layer returns an error envelope."""
    if isinstance(payload, dict) and payload.get("error"):
        return "error"
    return "ok"


async def _list_all_tools(client: Any) -> list[Any]:
    """Follow MCP tools/list cursors so a long catalog is not truncated."""
    collected: list[Any] = []
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(20):
        try:
            page = await client.list_tools(cursor=cursor) if cursor else await client.list_tools()
        except TypeError:
            page = await client.list_tools()
        batch = getattr(page, "tools", None) or []
        collected.extend(batch)
        nxt = getattr(page, "next_cursor", None) or getattr(page, "nextCursor", None)
        if not nxt or nxt in seen:
            break
        seen.add(nxt)
        cursor = nxt
    return collected


def _tool_started_event(run_id: str, step_id: str, tool: str, args_summary: dict[str, Any],
                        mode: str = ""):
    return make_event(EventType.TOOL_CALL_STARTED, run_id, step_id=step_id,
                      tool=tool, args_digest=args_summary, mode=mode)


def _tool_finished_event(run_id: str, step_id: str, tool: str, status: str, ms: int,
                         digest: str, result_id: str | None, mode: str = ""):
    return make_event(EventType.TOOL_CALL_FINISHED, run_id, step_id=step_id,
                      tool=tool, status=status, ms=ms, digest_preview=digest[:200],
                      result_id=result_id, mode=mode)


def _result_text(result: CallToolResult) -> str:
    parts = []
    for block in result.content:
        if isinstance(block, TextContent):
            parts.append(block.text)
    return "\n".join(parts)


def _result_payload(result: CallToolResult) -> Any:
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    return _result_text(result)
