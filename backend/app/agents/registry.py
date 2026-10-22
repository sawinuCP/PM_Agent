"""Agent registry — YAML templates, fail-fast validation, capability catalog.

Validation at boot catches unknown server refs, duplicate names, and schema
violations before they can reach a run.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.errors import RegistryError
from app.models.plan import AgentTemplate, CapabilityCatalogEntry

logger = logging.getLogger(__name__)


class AgentRegistry:
    def __init__(self, templates: list[AgentTemplate]) -> None:
        by_name = {t.name: t for t in templates}
        if len(by_name) != len(templates):
            duplicates = [t.name for t in templates if list(by_name).count(t.name) > 1]
            raise RegistryError(f"duplicate agent template names: {sorted(set(duplicates))}")
        self._templates = by_name
        if "conversational" not in self._templates:
            raise RegistryError(
                "registry must contain the 'conversational' agent "
                "(fast-path role prompt + capability catalog)")

    @classmethod
    def load(cls, config_dir: str | Path) -> AgentRegistry:
        directory = Path(config_dir)
        if not directory.exists() or not any(directory.glob("*.yaml")):
            raise RegistryError(f"agent config dir missing or empty: {directory}")
        templates: list[AgentTemplate] = []
        for file in sorted(directory.glob("*.yaml")):
            try:
                raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
                template = AgentTemplate.model_validate(raw)
            except yaml.YAMLError as exc:
                raise RegistryError(f"invalid YAML in {file.name}: {exc}") from exc
            except Exception as exc:  # noqa: BLE001 - pydantic errors surfaced clearly
                raise RegistryError(f"invalid agent template {file.name}: {exc}") from exc
            templates.append(template)
        registry = cls(templates)
        logger.info("agent registry loaded: %s", ", ".join(registry.names()))
        return registry

    def get(self, name: str) -> AgentTemplate:
        template = self._templates.get(name)
        if template is None:
            raise RegistryError(f"unknown agent '{name}' (available: {sorted(self._templates)})")
        return template

    def names(self) -> list[str]:
        return sorted(self._templates)

    def capability_catalog(self) -> list[CapabilityCatalogEntry]:
        """Compact catalog surfaced to the planner."""
        return [
            CapabilityCatalogEntry(
                name=t.name,
                description=t.description,
                capabilities=t.capabilities,
                tools=t.allowed_tools,
                servers=t.allowed_mcp_servers,
                permission_tier=t.permissions.tier.value,
            )
            for t in self._templates.values()
        ]


@lru_cache
def load_registry(config_dir: str) -> AgentRegistry:
    return AgentRegistry.load(config_dir)
