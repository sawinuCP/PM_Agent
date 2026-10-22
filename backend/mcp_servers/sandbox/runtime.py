"""Sandbox runtime guard: restricted namespace, audit-hook import guard, caps.

Honest security posture: the subprocess gives process isolation
(crash containment, killable timeouts, resource caps) — NOT OS-level security
against genuinely malicious code. The audit hook is a guardrail against
accidental misuse (including `importlib` bypasses, which a plain import
blocklist misses); real isolation requires a microVM (E2B/gVisor), the
documented upgrade path behind the same MCP contract.
"""

from __future__ import annotations

import ast
import builtins
import contextlib
import io
import sys
import time
from dataclasses import dataclass
from multiprocessing import get_context
from queue import Empty
from typing import Any

FORBIDDEN_IMPORTS = {
    "socket",
    "subprocess",
    "ctypes",
    "multiprocessing",
    "shutil",
    "urllib",
    "requests",
    "http",
    "ftplib",
    "telnetlib",
    "smtplib",
    "asyncio",
    "threading",
    "signal",
    "importlib",
    "pdb",
    "webbrowser",
}
ALLOWED_TOP_LEVEL = {
    "json",
    "statistics",
    "math",
    "datetime",
    "collections",
    "itertools",
    "functools",
    "heapq",
    "re",
    "string",
    "textwrap",
    "decimal",
    "fractions",
    "random",
    "csv",
    "io",
}

_SAFE_BUILTINS = {  # noqa: SIM905 - grouped string is easier to audit
    name: getattr(builtins, name)
    for name in [
        "abs",
        "all",
        "any",
        "bin",
        "bool",
        "bytes",
        "callable",
        "chr",
        "complex",
        "dict",
        "divmod",
        "enumerate",
        "filter",
        "float",
        "format",
        "frozenset",
        "hash",
        "hex",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "map",
        "max",
        "min",
        "next",
        "oct",
        "ord",
        "pow",
        "print",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "slice",
        "sorted",
        "staticmethod",
        "str",
        "sum",
        "tuple",
        "zip",
        "ValueError",
        "TypeError",
        "ZeroDivisionError",
        "KeyError",
        "IndexError",
        "AttributeError",
        "NameError",
        "RuntimeError",
        "Exception",
    ]
}


@dataclass
class SandboxResult:
    stdout: str
    result: str | None
    ms: int
    status: str  # ok | error | timeout
    error: str | None = None


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
    """__import__ replacement: audit-checked, allowlist-enforced."""
    top = str(name).split(".")[0]
    if top in FORBIDDEN_IMPORTS:
        raise ImportError(f"import of '{top}' is not allowed in the sandbox")
    if top not in ALLOWED_TOP_LEVEL:
        raise ImportError(f"module '{top}' is not available in the sandbox")
    return __import__(name, globals, locals, fromlist, level)


_SAFE_BUILTINS["__import__"] = _guarded_import

# Attribute access denied even when the module itself was importable.
_DENIED_ATTR = {"system", "popen", "fork", "exec", "execve", "spawn"}


def _audit(event: str, args) -> None:  # type: ignore[no-untyped-def]
    """PEP 578 audit hook — blocks forbidden imports/attrs before execution."""
    if event == "import":
        module = str(args[0]).split(".")[0]
        if module in FORBIDDEN_IMPORTS:
            raise ImportError(f"import of '{module}' is not allowed in the sandbox")
    if event == "object.__getattr__":
        attr = args[1] if len(args) > 1 else ""
        if attr in _DENIED_ATTR:
            raise AttributeError(f"attribute '{attr}' is not allowed in the sandbox")


def _prepare_code(code: str) -> str:
    """Strip markdown fences and drop a truncated tail so ast.parse can succeed.

    This is the cheap static gate (no LLM retry). A 2k-token cut-off script
    often fails on the last line; keeping the last valid prefix is enough.
    """
    import re

    text = (code or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:python)?\s*", "", text, count=1, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    try:
        ast.parse(text, mode="exec")
        return text
    except SyntaxError:
        lines = text.splitlines()
        for end in range(len(lines), 0, -1):
            chunk = "\n".join(lines[:end])
            try:
                ast.parse(chunk, mode="exec")
            except SyntaxError:
                continue
            if "result" in chunk:
                return chunk
        raise


def _validate_code(code: str) -> None:
    """Reject dunder introspection used by common Python sandbox escapes."""
    tree = ast.parse(code, mode="exec")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError("dunder attribute access is not allowed in the sandbox")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError("dunder names are not allowed in the sandbox")



def _execute_child(code: str, output: Any) -> None:
    """Child-process entrypoint. Only serialized result data crosses back."""
    started = time.perf_counter()
    stdout = io.StringIO()
    try:
        code = _prepare_code(code)
        _validate_code(code)
        with contextlib.redirect_stdout(stdout):
            sys.addaudithook(_audit)
            namespace: dict = {"__builtins__": _SAFE_BUILTINS, "__name__": "sandbox"}
            exec(compile(code, "<sandbox>", "exec"), namespace)  # noqa: S102 - isolated worker
            value = namespace.get("result")
        outcome = SandboxResult(
            stdout=stdout.getvalue()[:50_000],
            result=repr(value)[:2_000] if value is not None else None,
            ms=int((time.perf_counter() - started) * 1000),
            status="ok",
        )
    except Exception as exc:  # noqa: BLE001 - errors are DATA in the sandbox
        outcome = SandboxResult(
            stdout=stdout.getvalue()[:5_000],
            result=None,
            ms=int((time.perf_counter() - started) * 1000),
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )
    output.put(outcome)


def execute(code: str, timeout_s: float = 15.0) -> SandboxResult:
    """Execute code in a disposable child process with a killable deadline.

    This gives reliable crash/CPU-spin containment, including on Windows. It
    remains process isolation rather than a security boundary against a
    determined attacker; production should use a container or microVM.
    """
    started = time.perf_counter()
    context = get_context("spawn")
    output = context.Queue(maxsize=1)
    worker = context.Process(target=_execute_child, args=(code, output), daemon=True)
    worker.start()
    worker.join(timeout=max(1.0, timeout_s))
    if worker.is_alive():
        worker.terminate()
        worker.join(timeout=1)
        if worker.is_alive():
            worker.kill()
            worker.join()
        worker.close()
        return SandboxResult(
            stdout="",
            result=None,
            ms=int((time.perf_counter() - started) * 1000),
            status="timeout",
            error=f"execution exceeded {timeout_s}s limit",
        )
    try:
        outcome = output.get_nowait()
    except Empty:
        outcome = None
    worker.close()
    output.close()
    if not isinstance(outcome, SandboxResult):
        return SandboxResult(
            stdout="",
            result=None,
            ms=int((time.perf_counter() - started) * 1000),
            status="error",
            error="sandbox worker exited unexpectedly",
        )
    return outcome
