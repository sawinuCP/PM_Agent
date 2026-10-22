"""Reasoning-effort mapping and usage parsing (GLM + Muse Spark)."""

from __future__ import annotations

from types import SimpleNamespace

from app.ai.reasoning import (
    completion_extra_body,
    effort_for_tier,
    usage_from_api_response,
)
from app.core.settings import Settings


def test_effort_for_tier_when_disabled() -> None:
    settings = Settings(llm_reasoning_enabled=False)
    assert effort_for_tier("cheap", settings) is None


def test_effort_for_tier_when_disabled_muse_requires_reasoning() -> None:
    settings = Settings(llm_reasoning_enabled=False)
    assert effort_for_tier("standard", settings, model="meta/muse-spark-1.3-contributor") == "medium"


def test_effort_for_tier_maps_orchestration_tiers() -> None:
    settings = Settings(
        llm_reasoning_enabled=True,
        llm_reasoning_effort_cheap="low",
        llm_reasoning_effort_standard="medium",
        llm_reasoning_effort_strong="medium",
        llm_reasoning_effort_planner="medium",
    )
    assert effort_for_tier("cheap", settings) == "low"
    assert effort_for_tier("standard", settings) == "medium"
    assert effort_for_tier("strong", settings) == "medium"
    assert effort_for_tier("planner", settings) == "medium"


def test_completion_extra_body_glm_thinking() -> None:
    extra = completion_extra_body("high", model="z-ai/glm-5.3-flash")
    assert extra["reasoning_effort"] == "high"
    assert extra["thinking"] == {"type": "enabled"}


def test_completion_extra_body_muse_spark_no_thinking_field() -> None:
    extra = completion_extra_body("medium", model="meta/muse-spark-1.3-contributor")
    assert extra == {"reasoning_effort": "medium"}


def test_completion_extra_body_muse_defaults_when_effort_missing() -> None:
    extra = completion_extra_body(None, model="meta/muse-spark-1.3-contributor")
    assert extra == {"reasoning_effort": "medium"}


def test_usage_from_api_response_reads_reasoning_tokens() -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=40,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=25),
        ),
    )
    usage = usage_from_api_response(response)
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 40
    assert usage.reasoning_tokens == 25
    assert usage.visible_completion_tokens == 15
