"""Context trace helpers for workflow inspector."""

from __future__ import annotations

from app.observability.context_trace import build_context_sections


def test_build_context_sections_includes_memory_and_bindings() -> None:
    sections = build_context_sections(
        conversation="User: Who owns NOVA-4?\nAssistant: Leo Alvarez.",
        resolved_entities={"issue_id": "NOVA-4", "assignee": "Leo Alvarez"},
        prompt_bindings={"bound_tools": ["jira_get_issue"], "suggested_args": {"issue_id": "NOVA-4"}},
        worker_prompt='{"action":"finish"}',
        note="Worker context",
    )
    keys = {s["key"] for s in sections}
    assert "conversation" in keys
    assert "entities" in keys
    assert "bindings" in keys
    assert "worker_prompt" in keys
    assert "note" in keys


def test_build_context_sections_skips_empty() -> None:
    assert build_context_sections() == []
