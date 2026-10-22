"""Memory runtime integration tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.memory.runtime import (
    PostRunContext,
    episodic_hints_for_goal,
    mark_plan_hint_usage,
    persist_post_run,
    semantic_notes_for_agent,
)
from app.models.schemas import Plan, PlanStep


def _sample_plan() -> Plan:
    return Plan(
        thread_id="t1",
        goal="Predict sprint velocity",
        steps=[PlanStep(id="s1", agent="jira-analyst", task="Fetch sprint history")],
        synthesis="Summarize forecast",
    )


class TestMarkPlanHintUsage:
    def test_no_hints_leaves_flag_false(self) -> None:
        plan = mark_plan_hint_usage(_sample_plan(), None)
        assert plan.uses_episodic_hint is False

    def test_no_hints_clears_llm_flag(self) -> None:
        plan = _sample_plan().model_copy(update={"uses_episodic_hint": True})
        marked = mark_plan_hint_usage(plan, None)
        assert marked.uses_episodic_hint is False

    def test_hints_set_platform_flag(self) -> None:
        hints = [{"score": 0.9, "goal": "prior", "plan_outline": [], "outcome": "success"}]
        plan = mark_plan_hint_usage(_sample_plan(), hints)
        assert plan.uses_episodic_hint is True


class TestSemanticNotesForAgent:
    def test_degrades_on_retrieval_error(self, monkeypatch) -> None:
        async def fail(*_args, **_kwargs):
            raise RuntimeError("store down")

        monkeypatch.setattr("app.memory.semantic.relevant_facts_for", fail)
        notes = asyncio.run(semantic_notes_for_agent("jira-analyst", "velocity"))
        assert notes == []


class TestPersistPostRun:
    @pytest.mark.asyncio
    async def test_success_records_l2_and_l4(self) -> None:
        ctx = PostRunContext(
            run_id="r1",
            thread_id="t1",
            session_id="s1",
            goal="Predict velocity",
            plan=_sample_plan(),
            answer="Forecast is 42 points.",
            outcome="success",
            cost_usd=0.01,
            duration_s=3.5,
        )
        with patch("app.memory.runtime._persist_l2_conversation", new_callable=AsyncMock) as l2, \
             patch("app.memory.runtime._persist_l3_episodic", new_callable=AsyncMock) as l3, \
             patch("app.memory.runtime._persist_l4_semantic", new_callable=AsyncMock) as l4:
            await persist_post_run(ctx)
            l2.assert_awaited_once()
            l3.assert_not_awaited()
            l4.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_commit_episodic_records_l3(self) -> None:
        from app.memory.runtime import commit_episodic_record

        ctx = PostRunContext(
            run_id="r1",
            thread_id="t1",
            session_id="s1",
            goal="Predict velocity",
            plan=_sample_plan(),
            answer="Forecast is 42 points.",
            outcome="success",
            cost_usd=0.01,
            duration_s=3.5,
        )
        with patch("app.memory.runtime._persist_l3_episodic", new_callable=AsyncMock) as l3:
            assert await commit_episodic_record(ctx) is True
            l3.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_skips_l3_l4(self) -> None:
        ctx = PostRunContext(
            run_id="r1",
            thread_id="t1",
            session_id="s1",
            goal="Predict velocity",
            plan=_sample_plan(),
            answer="",
            outcome="failure",
            cost_usd=0.0,
            duration_s=1.0,
        )
        with patch("app.memory.runtime._persist_l2_conversation", new_callable=AsyncMock) as l2, \
             patch("app.memory.runtime._persist_l3_episodic", new_callable=AsyncMock) as l3, \
             patch("app.memory.runtime._persist_l4_semantic", new_callable=AsyncMock) as l4:
            await persist_post_run(ctx)
            l2.assert_awaited_once()
            l3.assert_not_awaited()
            l4.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_episodic_round_trip_scoped_to_thread(self) -> None:
        from app.memory import episodic

        plan = {"steps": [{"agent": "jira-analyst", "task": "fetch sprints", "tools": []}]}
        goal = "Predict the velocity for our next sprint based on previous sprints"
        await episodic.record_run(
            thread_id="thread-a",
            goal_text=goal,
            plan=plan,
            outcome="success",
            cost_usd=0.0,
            duration_s=2.0,
        )
        assert await episodic_hints_for_goal(goal, thread_id="thread-b") == []
        hints = await episodic_hints_for_goal(goal, thread_id="thread-a")
        assert hints
        assert hints[0]["plan_outline"][0]["agent"] == "jira-analyst"
