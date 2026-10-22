"""Unit tests: router heuristics and fast-path behavior."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.orchestration.routing.router import classify


def test_greeting_hits_fast_path_without_llm() -> None:
    from app.ai.provider import MockLLM

    decision = asyncio.run(classify("Hi, what can you do?", MockLLM()))
    assert decision.path == "fast"
    assert decision.intent == "simple"


def test_temporal_sensitivity_detected() -> None:
    from app.ai.provider import MockLLM

    decision = asyncio.run(classify("Predict velocity for the next sprint", MockLLM()))
    assert decision.path == "plan"
    assert decision.temporal_sensitivity is True


def test_this_sprint_is_not_mistaken_for_hi() -> None:
    from app.ai.provider import MockLLM

    decision = asyncio.run(classify(
        "What are the open Jira tickets in this sprint?", MockLLM()))
    assert decision.path == "plan"
    assert decision.intent == "complex"
    assert decision.temporal_sensitivity is True


def test_help_plus_work_is_planned_not_canned() -> None:
    from app.ai.provider import MockLLM

    decision = asyncio.run(classify(
        "help me list overdue issues in Nova", MockLLM()))
    assert decision.path == "plan"
    assert decision.intent == "complex"
    assert decision.direct_answer is None


def test_greeting_plus_work_is_planned() -> None:
    from app.ai.provider import MockLLM

    decision = asyncio.run(classify(
        "hi, what are the open tickets in this sprint?", MockLLM()))
    assert decision.path == "plan"
    assert decision.intent == "complex"


def test_fast_path_answer_never_leaks_capability_prompt(registry) -> None:
    from app.ai.provider import MockLLM
    from app.orchestration.workflow.pipeline import fast_path_answer

    platform = SimpleNamespace(
        factory=SimpleNamespace(provider=MockLLM()),
        registry=registry,
    )
    answer, generated = asyncio.run(fast_path_answer("hi", platform))
    assert generated is False
    assert answer.startswith("Hello!")
    assert "Step completed" not in answer
    assert "You can delegate work" not in answer
    assert "specialist agents" in answer.lower()


def test_data_query_uses_router_llm() -> None:
    from app.ai.cost import Usage
    from app.models.schemas import RouterOutput

    class Tracking:
        called = False

        async def generate_structured(self, *a, **k):  # noqa: ANN002, ANN003
            Tracking.called = True
            return RouterOutput(intent="complex", direct_answer=None), Usage()

    provider = Tracking()
    decision = asyncio.run(classify("What Jira projects exist?", provider))
    assert decision.path == "plan"
    assert decision.llm_used is True
    assert Tracking.called is True


def test_router_uses_llm_for_ambiguous_intents() -> None:
    from app.ai.cost import Usage
    from app.models.schemas import RouterOutput

    class Tracking:
        called = False

        async def generate_structured(self, *a, **k):  # noqa: ANN002, ANN003
            Tracking.called = True
            return RouterOutput(intent="complex", direct_answer=None), Usage()

    provider = Tracking()
    decision = asyncio.run(classify(
        "Analyze our infrastructure capacity for Q4", provider))
    assert decision.path == "plan"
    assert Tracking.called is True
    assert decision.llm_used is True


def test_router_falls_back_to_complex_on_llm_failure() -> None:
    class Exploding:
        async def generate_structured(self, *a, **k):  # noqa: ANN002, ANN003
            raise RuntimeError("provider down")

    decision = asyncio.run(classify("Analyze our infrastructure capacity for Q4", Exploding()))
    assert decision.path == "plan"
    assert decision.reason == "router error → plan path"


def test_mock_cost_is_illustrative() -> None:
    from app.ai.cost import pricing_for

    assert pricing_for("mock").cost(1000, 1000) > 0
