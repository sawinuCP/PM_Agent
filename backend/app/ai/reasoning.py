"""Reasoning-effort mapping and usage extraction (GLM + Muse Spark via CommandCode).

GLM-5.3-family models use ``reasoning_effort`` plus ``thinking.enabled`` in
``extra_body``. Muse Spark models always reason and accept ``minimal`` | ``low``
| ``medium`` | ``high`` | ``xhigh`` — disabling reasoning returns HTTP 400.
"""

from __future__ import annotations

from typing import Any, Literal

from app.ai.cost import Usage
from app.core.settings import Settings

ReasoningEffort = Literal["minimal", "low", "medium", "high", "xhigh", "max"]

_VALID_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh", "max"})

_TIER_EFFORT_ATTR = {
    "cheap": "llm_reasoning_effort_cheap",
    "standard": "llm_reasoning_effort_standard",
    "strong": "llm_reasoning_effort_strong",
    "planner": "llm_reasoning_effort_planner",
}


def model_requires_reasoning(model: str) -> bool:
    """Muse Spark always reasons; ``reasoning_effort`` cannot be omitted."""
    lowered = model.lower()
    return "muse-spark" in lowered or "muse_spark" in lowered


def model_uses_glm_thinking(model: str) -> bool:
    """GLM gateways expect ``thinking.type=enabled`` alongside effort."""
    return "glm" in model.lower()


def effort_for_tier(tier: str, settings: Settings, *, model: str = "") -> ReasoningEffort | None:
    """Return configured reasoning depth for an orchestration tier."""
    if not settings.llm_reasoning_enabled:
        if model_requires_reasoning(model):
            return "medium"
        return None
    attr = _TIER_EFFORT_ATTR.get(tier, "llm_reasoning_effort_standard")
    value = getattr(settings, attr, settings.llm_reasoning_effort_standard)
    if value not in _VALID_EFFORTS:
        fallback = settings.llm_reasoning_effort_standard
        if fallback in _VALID_EFFORTS:
            return fallback  # type: ignore[return-value]
        return "medium"
    return value  # type: ignore[return-value]


def enrich_completion_payload(payload: dict[str, Any], effort: ReasoningEffort | None) -> None:
    """Legacy no-op — reasoning controls live in ``completion_extra_body``."""
    _ = payload, effort


def completion_extra_body(effort: ReasoningEffort | None, *, model: str = "") -> dict[str, Any]:
    """OpenAI SDK ``extra_body`` for provider-specific reasoning controls."""
    resolved = effort
    if not resolved and model_requires_reasoning(model):
        resolved = "medium"
    if not resolved:
        return {}
    body: dict[str, Any] = {"reasoning_effort": resolved}
    if model_uses_glm_thinking(model):
        body["thinking"] = {"type": "enabled"}
    return body


def usage_from_api_response(response: Any, *, fallback_completion: str = "") -> Usage:
    """Parse token usage from a provider response, including reasoning tokens."""
    usage_obj = getattr(response, "usage", None)
    if usage_obj is None:
        return Usage()

    prompt = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage_obj, "completion_tokens", 0) or 0)
    reasoning = _reasoning_tokens_from_usage(usage_obj)

    if prompt == 0 and completion == 0 and not fallback_completion:
        return Usage()

    visible = completion
    if reasoning > 0 and reasoning <= completion:
        visible = completion - reasoning

    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        reasoning_tokens=reasoning,
        visible_completion_tokens=visible if reasoning > 0 else 0,
    )


def _reasoning_tokens_from_usage(usage_obj: Any) -> int:
    """Best-effort reasoning token count across OpenAI-compatible providers."""
    direct = getattr(usage_obj, "reasoning_tokens", None)
    if direct is not None:
        return int(direct or 0)

    details = getattr(usage_obj, "completion_tokens_details", None)
    if details is not None:
        nested = getattr(details, "reasoning_tokens", None)
        if nested is not None:
            return int(nested or 0)
        if isinstance(details, dict):
            return int(details.get("reasoning_tokens") or 0)

    if isinstance(usage_obj, dict):
        if usage_obj.get("reasoning_tokens") is not None:
            return int(usage_obj["reasoning_tokens"] or 0)
        nested = usage_obj.get("completion_tokens_details") or {}
        if isinstance(nested, dict) and nested.get("reasoning_tokens") is not None:
            return int(nested["reasoning_tokens"] or 0)
    return 0
