"""JSON-schema helpers for MCP tool argument validation."""

from __future__ import annotations

from typing import Any


def required_schema_fields(
    schema: dict[str, Any] | None,
    *,
    fallback_limit: int = 4,
) -> list[str]:
    """Return required property names from an MCP input schema."""
    if not isinstance(schema, dict):
        return []
    required = schema.get("required")
    if isinstance(required, list) and required:
        return [str(name) for name in required]
    props = schema.get("properties")
    if isinstance(props, dict):
        return [str(name) for name in list(props)[:fallback_limit]]
    return []
