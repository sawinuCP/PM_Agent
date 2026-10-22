"""Platform composition root — wires singleton services for the FastAPI lifespan.

Wires registry, MCP manager, LLM provider, factory, event bus, executor,
and checkpointer; started/stopped by the FastAPI lifespan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.agents.factory import AgentFactory
from app.agents.registry import AgentRegistry, load_registry
from app.ai.provider import get_provider, resolve_llm_provider
from app.core.settings import env_file_path, get_settings
from app.mcp_integration.manager import MCPManager
from app.mcp_integration.transports import load_server_configs
from app.memory.checkpointer import open_checkpointer
from app.models.db import dispose_engine
from app.observability.bus import EventBus
from app.orchestration.workflow import Executor

logger = logging.getLogger(__name__)


@dataclass
class Platform:
    registry: AgentRegistry
    manager: MCPManager
    bus: EventBus
    factory: AgentFactory
    executor: Executor
    checkpointer: Any = None

    async def shutdown(self) -> None:
        """Tear down checkpointer + MCP connections (same-task closes: anyio
        cancel scopes forbid cross-task exits, so no wait_for here)."""
        if self._checkpointer_cm is not None:
            try:
                await self._checkpointer_cm.__aexit__(None, None, None)
            except BaseException:  # noqa: BLE001 - best-effort teardown (see below)
                logger.warning("checkpointer close failed", exc_info=True)
            self._checkpointer_cm = None
            self.checkpointer = None
        await self.manager.shutdown()
        try:
            await self.bus.flush_all_traces()
        except BaseException:  # noqa: BLE001
            logger.warning("trace buffer flush failed at shutdown", exc_info=True)
        try:
            await dispose_engine()
        except BaseException:  # noqa: BLE001 - teardown is best-effort; note:
            # CancelledError is a BaseException (not Exception) on py3.11 and
            # aiosqlite pool-close races the loop on Windows — logged, never fatal.
            logger.warning("engine dispose incomplete at shutdown", exc_info=True)
        logger.info("platform shut down")


async def build_platform() -> Platform:
    """Boot-order composition: registry → MCP → provider → factory → executor."""
    settings = get_settings()
    key = settings.openai_api_key or ""
    logger.info(
        "settings loaded: env_file=%s exists=%s llm=%s model=%s key_len=%s",
        env_file_path(), env_file_path().is_file(),
        settings.llm_provider, settings.openai_model,
        len(key) if key else 0,
    )
    registry = load_registry(settings.agents_config_dir)
    manager = MCPManager(load_server_configs(settings.mcp_servers_config))
    await manager.start()
    bus = EventBus()
    provider = get_provider()
    logger.info(
        "inference ready: provider=%s model=%s live=%s",
        resolve_llm_provider(), provider.usage_model(),
        resolve_llm_provider() == "openai")
    factory = AgentFactory(registry, manager, provider)
    executor = Executor(factory, bus)
    platform = Platform(registry=registry, manager=manager, bus=bus,
                        factory=factory, executor=executor)
    checkpointer_cm = open_checkpointer()
    saver = await checkpointer_cm.__aenter__()
    platform.checkpointer = saver
    platform._checkpointer_cm = checkpointer_cm
    platform.executor._checkpointer = saver
    return platform


_platform: Platform | None = None


def get_platform() -> Platform:
    if _platform is None:
        raise RuntimeError("platform not started")
    return _platform


def set_platform(platform: Platform | None) -> None:
    global _platform
    _platform = platform
