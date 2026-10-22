"""Unit tests for the sandbox guard: imports, builtins, timeout, determinism."""

from __future__ import annotations

from mcp_servers.sandbox.runtime import execute


def test_markdown_fence_and_truncated_tail_are_repaired() -> None:
    outcome = execute("```python\nresult = 3\n```")
    assert outcome.status == "ok"
    assert "3" in (outcome.result or "")
    truncated = execute("result = {'n': 1}\nbroken = {\n")
    assert truncated.status == "ok"
    assert "n" in (truncated.result or "")


def test_statistics_computation_ok() -> None:
    outcome = execute("import statistics\nresult = statistics.mean([1, 2, 3])")
    assert outcome.status == "ok"
    assert "2" in (outcome.result or "")


def test_forbidden_import_blocked() -> None:
    outcome = execute("import socket")
    assert outcome.status == "error"
    assert "not allowed" in (outcome.error or "")


def test_importlib_bypass_blocked() -> None:
    outcome = execute("import importlib\nresult = importlib.import_module('socket')")
    assert outcome.status == "error"


def test_dunder_escape_blocked() -> None:
    outcome = execute("result = __import__('os').system('echo pwned')")
    assert outcome.status == "error"


def test_subclasses_introspection_escape_blocked() -> None:
    outcome = execute("result = ().__class__.__base__.__subclasses__()")
    assert outcome.status == "error"
    assert "dunder" in (outcome.error or "")


def test_timeout_returns_status() -> None:
    outcome = execute("while True:\n    pass", timeout_s=1)
    assert outcome.status == "timeout"


def test_stdout_captured() -> None:
    outcome = execute("print('hello'); result = 7")
    assert outcome.status == "ok" and "hello" in outcome.stdout


def test_deterministic_replay() -> None:
    code = "import statistics\nvelocities = [30, 42, 38, 45, 36]\nresult = round(sum(velocities[-3:]) / 3, 1)"
    a, b = execute(code), execute(code)
    assert a.result == b.result == "39.7"
