"""MCP server configuration model + YAML loader, and v2 SDK client factory.

The Manager is the only consumer of this module — agents never see transports.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Literal

import yaml
from mcp import Client, StdioServerParameters
from pydantic import BaseModel, Field, model_validator

from app.core.errors import ConfigError

logger = logging.getLogger(__name__)


class MCPServerConfig(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    transport: Literal["stdio", "streamable_http"]
    url: str | None = None
    command: list[str] | None = None
    timeout_s: float = 30.0
    max_retries: int = 3
    tool_cache_ttl_s: float = 300.0
    mode: str = "mock"  # mock | real (documented switch inside the server)
    sensitive_tools: list[str] = Field(default_factory=list)  # server-registered write tools

    @model_validator(mode="after")
    def _validate_transport(self) -> MCPServerConfig:
        if self.transport == "streamable_http" and not self.url:
            raise ValueError(f"server '{self.name}': streamable_http requires 'url'")
        if self.transport == "stdio" and not self.command:
            raise ValueError(f"server '{self.name}': stdio requires 'command'")
        return self


def load_server_configs(path: str | Path) -> list[MCPServerConfig]:
    """Parse and validate mcp_servers.yaml — fail-fast at boot."""
    file = Path(path)
    if not file.exists():
        raise ConfigError(f"MCP servers config not found: {file}")
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {file}: {exc}") from exc
    entries = raw.get("mcp_servers")
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{file} must define a non-empty 'mcp_servers' list")
    names: list[str] = []
    configs: list[MCPServerConfig] = []
    for entry in entries:
        cfg = MCPServerConfig.model_validate(entry)
        if cfg.name in names:
            raise ConfigError(f"duplicate MCP server name: {cfg.name}")
        names.append(cfg.name)
        configs.append(cfg)
    override = os.environ.get("JIRA_MCP_URL", "").strip()
    if override:
        patched: list[MCPServerConfig] = []
        for cfg in configs:
            if cfg.name == "jira" and cfg.transport == "streamable_http":
                cfg = cfg.model_copy(update={"url": override})
            patched.append(cfg)
        configs = patched
    logger.info("loaded %d MCP server configs from %s", len(configs), file)
    return configs


async def open_client(config: MCPServerConfig) -> Client:
    """Open one long-lived v2 Client for a server (connection management)."""
    if config.transport == "stdio":
        command = list(config.command or [])
        if command and command[0] in {"python", "python3"}:
            command[0] = sys.executable
        params = StdioServerParameters(
            command=command[0],
            args=command[1:],
            env=None,
            cwd=str(Path(__file__).resolve().parents[2]),  # backend/ root
        )
        client = Client(params, read_timeout_seconds=config.timeout_s)
    else:
        client = Client(config.url or "", read_timeout_seconds=config.timeout_s)
    await client.__aenter__()
    return client


async def close_client(client: Client) -> None:
    await client.__aexit__(None, None, None)
