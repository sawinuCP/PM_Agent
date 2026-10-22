"""Unit tests: registry fail-fast validation + digest pipeline + episodic scoring."""

from __future__ import annotations

import asyncio

import pytest
from app.mcp_integration.digest import (
    args_digest,
    build_digest,
    decode_step_packet,
    loop_record,
    normalize_payload,
    structured_preview,
)
from app.models.schemas import AgentTemplate


class TestRegistry:
    def test_loads_shipped_templates(self, registry) -> None:
        assert "conversational" in registry.names()
        assert "jira-analyst" in registry.names()
        catalog = registry.capability_catalog()
        assert all(e.tools is not None for e in catalog)

    def test_unknown_agent_raises(self, registry) -> None:
        from app.core.errors import RegistryError

        with pytest.raises(RegistryError, match="unknown agent"):
            registry.get("nope")

    def test_duplicate_names_rejected(self) -> None:
        from app.agents.registry import AgentRegistry

        template = AgentTemplate(name="dup", description="d", system_prompt="p")
        with pytest.raises(Exception, match="duplicate"):
            AgentRegistry([template, template.model_copy()])

    def test_missing_conversational_rejected(self) -> None:
        from app.agents.registry import AgentRegistry

        template = AgentTemplate(name="other", description="d", system_prompt="p")
        with pytest.raises(Exception, match="conversational"):
            AgentRegistry([template])


class TestDigest:
    def test_list_digest_shows_count_and_sample(self) -> None:
        text = build_digest([{"a": i} for i in range(10)])
        assert "list[10]" in text

    def test_dict_digest_caps_long_values(self) -> None:
        text = build_digest({"big": "x" * 500, "n": 3})
        assert "n: 3" in text and len(text) < 700

    def test_args_digest_truncates(self) -> None:
        out = args_digest({"jql": "y" * 300})
        assert out["jql"].endswith("...")

    def test_empty_args(self) -> None:
        assert args_digest(None) == {}

    def test_normalize_wraps_list_and_json_text(self) -> None:
        assert normalize_payload([1, 2]) == {"items": [1, 2], "count": 2}
        assert normalize_payload('{"ok": true}') == {"ok": True}

    def test_structured_preview_keeps_issue_samples(self) -> None:
        preview = structured_preview({
            "issues": [
                {"id": "PLAT-9", "summary": "Stabilize checkout retries",
                 "type": "Bug", "status": "In Progress", "story_points": 3,
                 "assignee": "bob"},
            ],
            "count": 1,
        })
        assert preview["issues_count"] == 1
        assert preview["issues"][0]["id"] == "PLAT-9"

    def test_loop_record_is_json(self) -> None:
        import json

        text = loop_record({"type": "tool_result", "tool": "jira_search", "status": "ok"})
        parsed = json.loads(text)
        assert parsed["type"] == "tool_result"
        assert parsed["tool"] == "jira_search"

    def test_decode_step_packet_round_trip(self) -> None:
        import json

        packet = {"type": "step_result", "step": "s1", "summary": "done", "hops": []}
        assert decode_step_packet(json.dumps(packet), "s1")["summary"] == "done"
        assert decode_step_packet("plain digest", "s2")["summary"] == "plain digest"


class TestEpisodicScoring:
    def test_weights_sum_to_one(self) -> None:
        from app.core.settings import get_settings

        s = get_settings()
        assert abs(s.episodic_weight_similarity + s.episodic_weight_recency +
                   s.episodic_weight_success - 1.0) < 1e-9

    def test_record_and_retrieve_hint(self) -> None:
        from app.memory import episodic

        plan = {"steps": [{"agent": "jira-analyst", "task": "fetch", "tools": ["t"]}]}
        import asyncio

        asyncio.get_event_loop_policy()
        record = episodic.record_run(
            thread_id="t1",
            goal_text="Predict the velocity for our next sprint based on previous sprints",
            plan=plan, outcome="success", cost_usd=0.0, duration_s=1.0)
        hint_coro = episodic.hints_for_goal(
            "Predict the velocity for our next sprint from prior sprints")

        async def _run():  # noqa: ANN202
            await record
            return await hint_coro

        hints = asyncio.run(_run())
        assert hints and hints[0]["plan_outline"][0]["agent"] == "jira-analyst"

    def test_ttl_filters_stale_records(self) -> None:
        from datetime import UTC, datetime, timedelta

        from app.core.settings import get_settings
        from app.memory import episodic

        old = datetime.now(UTC) - timedelta(days=30)
        episodic._STORES.setdefault(get_settings().database_url, []).append({
            "thread_id": "t", "goal_text": "velocity forecast", "embedding": [1.0],
            "plan": {}, "agents": [], "tools": [], "outcome": "success",
            "cost_usd": 0.0, "duration_s": 0.0, "created_at": old.isoformat(),
        })
        hints = asyncio.run(episodic.hints_for_goal("velocity forecast"))
        assert hints == []  # 30d-old record filtered by the 14d TTL

    def test_postgres_retrieval_failure_degrades_to_no_hints(
            self, monkeypatch) -> None:
        from app.core.settings import get_settings
        from app.memory import episodic

        monkeypatch.setenv(
            "DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused")
        get_settings.cache_clear()

        def fail_search(*_args):
            raise RuntimeError("incompatible vector schema")

        monkeypatch.setattr(episodic, "_pg_search_episodic_sync", fail_search)
        assert asyncio.run(episodic.hints_for_goal("velocity forecast")) == []
