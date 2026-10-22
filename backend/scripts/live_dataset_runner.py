"""Run TEST_DATASET.md prompts against a live stack (real LLM + Jira MCP).

Usage (from backend/, stack must be up on :8000):
    python scripts/live_dataset_runner.py
    python scripts/live_dataset_runner.py --ids TC-R01,TC-R02,TC-R03
    python scripts/live_dataset_runner.py --list

Prints one line per case: ID | PASS/FAIL | latency_s | notes
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("API_TOKEN", "").strip()
    if token:
        headers["X-API-Key"] = token
    return headers


def _req(base: str, method: str, path: str, data: dict | None = None) -> dict[str, Any]:
    url = base + path
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, method=method, headers=_headers())
    with urllib.request.urlopen(request, timeout=300) as resp:
        return json.loads(resp.read().decode())


def _trace_types(events: list[dict[str, Any]]) -> list[str]:
    return [str(e.get("type") or e.get("event_type") or "") for e in events]


def _events_of_type(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [
        e for e in events
        if str(e.get("type") or e.get("event_type") or "") == event_type
    ]


def _validate_fast(_run: dict, events: list[dict], result: str) -> tuple[bool, str]:
    types = _trace_types(events)
    if _run.get("status") != "completed":
        return False, f"status={_run.get('status')!r}"
    router = _events_of_type(events, "router_decision")
    if not router or router[0].get("path") != "fast":
        return False, "expected router path=fast"
    if "plan_created" in types or "agent_spawned" in types:
        return False, "fast path should not spawn agents"
    if len(result.strip()) < 20:
        return False, "answer too short"
    return True, "fast path ok"


def _validate_plan_tools(_run: dict, events: list[dict], result: str) -> tuple[bool, str]:
    types = _trace_types(events)
    if _run.get("status") != "completed":
        return False, f"status={_run.get('status')!r}"
    if "plan_created" not in types:
        return False, "missing plan_created"
    tool_ok = any(
        e.get("type") == "tool_call_finished" and e.get("status") == "ok"
        for e in events
    )
    if not tool_ok:
        return False, "no successful tool_call_finished"
    if len(result.strip()) < 40:
        return False, "answer too short"
    return True, "plan + tools ok"


@dataclass(frozen=True)
class DatasetCase:
    case_id: str
    prompt: str
    description: str
    timeout_s: float = 300.0
    auto_approve: bool = False
    validate: Callable[[dict[str, Any], list[dict[str, Any]], str], tuple[bool, str]] | None = None


def _validate_velocity(_run: dict, events: list[dict], result: str) -> tuple[bool, str]:
    ok, note = _validate_plan_tools(_run, events, result)
    if not ok:
        return ok, note
    lower = result.lower()
    if not any(c.isdigit() for c in result):
        return False, "expected numeric forecast"
    if not any(w in lower for w in ("sprint", "velocity", "point", "forecast", "predict")):
        return False, "expected sprint/velocity language"
    return True, "velocity forecast ok"


def _validate_bugs(_run: dict, events: list[dict], result: str) -> tuple[bool, str]:
    ok, note = _validate_plan_tools(_run, events, result)
    if not ok:
        return ok, note
    lower = result.lower()
    if "bug" not in lower and "sprint" not in lower:
        return False, "expected bug/sprint discussion"
    if not any(w in lower for w in ("trend", "up", "down", "stable", "declin", "increas")):
        return False, "expected trend language"
    return True, "bug trend ok"


def _validate_projects(_run: dict, events: list[dict], result: str) -> tuple[bool, str]:
    ok, note = _validate_plan_tools(_run, events, result)
    if not ok:
        return ok, note
    lower = result.lower()
    found = sum(1 for key in ("plat", "nova", "anlt", "pay") if key in lower)
    if found < 2:
        return False, f"expected multiple projects, found {found}"
    return True, f"projects listed ({found}/4 keys)"


def _validate_parallel(_run: dict, events: list[dict], result: str) -> tuple[bool, str]:
    ok, note = _validate_plan_tools(_run, events, result)
    if not ok:
        return ok, note
    lower = result.lower()
    if "plat" not in lower or "nova" not in lower:
        return False, "expected PLAT and NOVA in answer"
    spawned = sum(1 for e in events if e.get("type") == "agent_spawned")
    if spawned < 2:
        return False, f"expected parallel spawns, got {spawned}"
    return True, "parallel health ok"


def _validate_hitl(_run: dict, events: list[dict], result: str) -> tuple[bool, str]:
    types = _trace_types(events)
    if _run.get("status") != "completed":
        return False, f"status={_run.get('status')!r}"
    if "approval_required" not in types:
        return False, "missing approval_required"
    if "tool_call_finished" not in types:
        return False, "missing tool_call_finished after approval"
    if len(result.strip()) < 20:
        return False, "answer too short"
    return True, "hitl approve ok"


def _validate_issue_status(_run: dict, events: list[dict], result: str) -> tuple[bool, str]:
    ok, note = _validate_plan_tools(_run, events, result)
    if not ok:
        return ok, note
    if not re.search(r"plat-9", result, re.IGNORECASE):
        return False, "expected PLAT-9 reference"
    return True, "issue status ok"


CASES: tuple[DatasetCase, ...] = (
    DatasetCase("TC-R01", "Hi, what can you do?", "Fast path", 90, validate=_validate_fast),
    DatasetCase(
        "TC-R02",
        "Predict the velocity for our next sprint based on previous Jira sprints",
        "Velocity forecast",
        360,
        validate=_validate_velocity,
    ),
    DatasetCase(
        "TC-R03",
        "Compare bug counts across the last 3 sprints and analyze their trend",
        "Bug trend",
        360,
        validate=_validate_bugs,
    ),
    DatasetCase(
        "TC-R04",
        "What Jira projects exist and who leads each?",
        "Project listing",
        240,
        validate=_validate_projects,
    ),
    DatasetCase(
        "TC-B03",
        "Compare PLAT and NOVA project health side by side in parallel",
        "Parallel health",
        360,
        validate=_validate_parallel,
    ),
    DatasetCase(
        "TC-B04",
        "Give me a side-by-side health summary for PLAT, NOVA, ANLT, and PAY",
        "Batch health",
        360,
        validate=_validate_plan_tools,
    ),
    DatasetCase(
        "TC-B01",
        "Create a Jira issue in project PLAT with summary Track next sprint commitments",
        "HITL create issue",
        240,
        auto_approve=True,
        validate=_validate_hitl,
    ),
    DatasetCase(
        "Q-10",
        "What is the status of PLAT-9?",
        "Issue lookup",
        240,
        validate=_validate_issue_status,
    ),
    DatasetCase(
        "Q-30",
        "Compare PLAT and NOVA project health side by side",
        "Health comparison",
        360,
        validate=_validate_parallel,
    ),
)


def wait_run(base: str, run_id: str, *, timeout_s: float, auto_approve: bool) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    run: dict[str, Any] = {}
    while time.monotonic() < deadline:
        run = _req(base, "GET", f"/runs/{run_id}")
        status = str(run.get("status") or "")
        if status == "awaiting_approval" and auto_approve:
            _req(base, "POST", f"/runs/{run_id}/approve", {"approved": True})
            time.sleep(0.5)
            continue
        if status in ("completed", "failed", "awaiting_clarification", "awaiting_approval"):
            return run
        time.sleep(2)
    return run


def _trace_metrics(events: list[dict[str, Any]]) -> dict[str, int]:
    types = [str(e.get("type") or "") for e in events]
    return {
        "llm_calls": sum(1 for t in types if t == "llm_call_started"),
        "tool_calls": sum(1 for t in types if t == "tool_call_started"),
        "tool_parallel": sum(
            1 for e in events
            if e.get("type") == "tool_call_started" and e.get("mode") == "parallel"
        ),
    }


def run_case(base: str, case: DatasetCase) -> dict[str, Any]:
    t0 = time.perf_counter()
    session = _req(base, "POST", "/sessions", {"title": f"live-dataset {case.case_id}"})
    session_id = str(session["id"])
    started = time.perf_counter()
    run_id = _req(base, "POST", "/chat", {
        "session_id": session_id,
        "message": case.prompt,
    })["run_id"]
    run = wait_run(base, run_id, timeout_s=case.timeout_s, auto_approve=case.auto_approve)
    latency_s = round(time.perf_counter() - started, 2)
    total_s = round(time.perf_counter() - t0, 2)

    # Traces may lag run status by a moment on fast-path turns.
    events: list[dict[str, Any]] = []
    for _ in range(5):
        traces = _req(base, "GET", f"/runs/{run_id}/traces")
        events = traces.get("events") if isinstance(traces.get("events"), list) else []
        if events or str(run.get("status")) != "completed":
            break
        time.sleep(0.5)
    result = str(run.get("result_summary") or run.get("result") or "")

    passed = False
    note = "no validator"
    if case.validate is not None:
        try:
            passed, note = case.validate(run, events, result)
        except Exception as exc:  # noqa: BLE001
            passed, note = False, f"validator error: {type(exc).__name__}"
    else:
        passed = run.get("status") == "completed"
        note = str(run.get("status") or "")

    metrics = _trace_metrics(events)

    return {
        "id": case.case_id,
        "prompt": case.prompt,
        "passed": passed,
        "latency_s": latency_s,
        "total_s": total_s,
        "status": run.get("status"),
        "run_id": run_id,
        "note": note,
        "metrics": metrics,
        "result_preview": result[:200].replace("\n", " "),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Live TEST_DATASET runner")
    parser.add_argument("--base", default="http://127.0.0.1:8000/api")
    parser.add_argument("--ids", default="", help="Comma-separated case IDs (default: all)")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        for c in CASES:
            print(f"{c.case_id}\t{c.description}\t{c.prompt[:60]}...")
        return 0

    try:
        ready = _req(args.base, "GET", "/health/ready")
        mcp = _req(args.base, "GET", "/mcp/health")
    except urllib.error.URLError as exc:
        print(f"FAIL: API not reachable at {args.base} ({exc})")
        print("Start stack: .\\scripts\\start_all.ps1")
        return 1

    if ready.get("status") != "ok":
        print(f"FAIL: health/ready degraded: {ready}")
        return 1

    jira_status = (mcp.get("servers") or {}).get("jira", {}).get("status")
    print(f"stack ready | jira={jira_status!r} | cases={len(CASES)}")
    print("-" * 72)

    selected = CASES
    if args.ids.strip():
        wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
        selected = tuple(c for c in CASES if c.case_id in wanted)
        if not selected:
            print(f"FAIL: no cases matched --ids {args.ids!r}")
            return 1

    results: list[dict[str, Any]] = []
    failures = 0
    for case in selected:
        print(f"[{case.case_id}] running: {case.prompt[:70]}...", flush=True)
        row = run_case(args.base, case)
        results.append(row)
        status = "PASS" if row["passed"] else "FAIL"
        if not row["passed"]:
            failures += 1
        print(
            f"[{case.case_id}] {status} | latency={row['latency_s']}s | "
            f"llm={row['metrics']['llm_calls']} tools={row['metrics']['tool_calls']} | "
            f"status={row['status']} | {row['note']}",
            flush=True,
        )
        if row["result_preview"]:
            print(f"         preview: {row['result_preview']}", flush=True)
        print("-" * 72, flush=True)

    out_path = BACKEND / "data" / "live_dataset_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    total_latency = sum(r["latency_s"] for r in results)
    print(f"SUMMARY: {len(results) - failures}/{len(results)} passed | total_latency={total_latency:.1f}s")
    print(f"Results saved: {out_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
