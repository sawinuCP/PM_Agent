"""LLM trace preview helpers."""

from __future__ import annotations

from app.observability.llm_trace import preview_completion, preview_messages, preview_simple, truncate_trace_text


def test_truncate_trace_text() -> None:
    text = "x" * 100
    assert truncate_trace_text(text, max_chars=200) == text
    clipped = truncate_trace_text("a" * 100, max_chars=40)
    assert "truncated" in clipped


def test_preview_simple_and_completion() -> None:
    inp = preview_simple("sys", "user question")
    assert "[system]" in inp
    assert "[user]" in inp
    out = preview_completion("answer", tool_calls=[{"name": "jira_get_projects", "args": {}}])
    assert "answer" in out
    assert "tool_calls" in out


def test_preview_messages() -> None:
    text = preview_messages([
        {"role": "system", "content": "You are helpful"},
        {"role": "user", "content": "Hello"},
    ])
    assert "[system]" in text
    assert "Hello" in text
