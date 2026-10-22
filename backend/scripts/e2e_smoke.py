"""End-to-end smoke test: platform boot → chat → trace validation (multi-profile).

Keyless by default (MockLLM + hash embeddings + Jira simulator). Profiles mirror
README demo scenarios (deliverable #11).

Usage (from backend/):
    python scripts/e2e_smoke.py                    # default: velocity profile
    python scripts/e2e_smoke.py --profile fast
    python scripts/e2e_smoke.py --profile all
    python scripts/e2e_smoke.py --list-profiles

Environment overrides:
    AZEND_E2E_PROFILE   profile name (same as --profile)
    AZEND_E2E_GOAL      override goal for single-goal profiles only
    AZEND_E2E_LIVE_LLM=1       use backend/.env LLM provider
    AZEND_E2E_LIVE_VOYAGE=1    use live Voyage embeddings
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))
os.chdir(BACKEND)

from e2e_scenarios import (  # noqa: E402
    PROFILE_NAMES,
    SCENARIO_PROFILES,
    ScenarioContext,
    ScenarioProfile,
    get_profile,
)

# Smoke is keyless/deterministic unless AZEND_E2E_LIVE_LLM=1 (uses backend/.env provider).
if os.environ.get("AZEND_E2E_LIVE_LLM") != "1":
    os.environ["LLM_PROVIDER"] = "mock"
else:
    os.environ.setdefault("LLM_PROVIDER", "openai")

if os.environ.get("AZEND_E2E_LIVE_VOYAGE") == "1":
    os.environ["EMBEDDINGS_PROVIDER"] = "voyage"
    os.environ["EMBEDDINGS_DIM"] = "1024"
else:
    os.environ["VOYAGE_API_KEY"] = ""
    os.environ["EMBEDDINGS_PROVIDER"] = "hash"
    os.environ["EMBEDDINGS_DIM"] = "64"
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/smoke.db")
os.environ.setdefault("CHECKPOINT_DB_PATH", "./data/smoke_cp.sqlite")
os.environ.setdefault("API_TOKEN", "")
_SMOKE_JIRA_PORT = os.environ.get("JIRA_MCP_PORT", "8011")
os.environ["JIRA_MCP_PORT"] = _SMOKE_JIRA_PORT

from app.core.settings import get_settings  # noqa: E402

get_settings.cache_clear()


async def _spawn_jira_server() -> asyncio.subprocess.Process:
    port = _SMOKE_JIRA_PORT
    os.environ["JIRA_MCP_URL"] = f"http://127.0.0.1:{port}/mcp"
    os.environ["MCP_SERVERS_CONFIG"] = str(BACKEND / "config" / "mcp_servers.yaml")
    get_settings.cache_clear()
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-W", "ignore", str(BACKEND / "mcp_servers" / "jira" / "server.py"),
        cwd=str(BACKEND),
        env={**os.environ, "JIRA_MCP_PORT": port, "JIRA_MCP_HOST": "127.0.0.1"},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    import httpx

    url = f"http://127.0.0.1:{port}/mcp"
    for _ in range(60):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    json={"jsonrpc": "2.0", "id": 0, "method": "ping"},
                    timeout=1.0,
                )
            if response.status_code < 500:
                print(f"[boot] jira server accepting on :{port}")
                return process
        except Exception:
            await asyncio.sleep(0.25)
    process.kill()
    raise RuntimeError("jira server did not become ready")


async def _wait_for_run(
    run_id: str,
    *,
    timeout_s: float,
    profile: ScenarioProfile,
    ctx: ScenarioContext,
    app_db: Any,
    platform: Any,
) -> bool:
    from app.services import chat_service

    task = chat_service._BACKGROUND.get(run_id)
    if task is None:
        print(f"[FAIL] no background task for run {run_id}", flush=True)
        return False

    deadline = time.monotonic() + timeout_s
    while not task.done():
        if profile.mid_run is not None:
            await profile.mid_run(ctx, app_db, platform)
        if time.monotonic() >= deadline:
            task.cancel()
            print(f"[FAIL] run pipeline timed out after {timeout_s:.0f}s", flush=True)
            traces = await app_db.list_traces(run_id)
            print("[trace] event types so far:", [t.event_type for t in traces], flush=True)
            return False
        await asyncio.sleep(0.05)

    if profile.mid_run is not None and not ctx.extras.get("hitl_resolved"):
        print("[FAIL] run finished without resolving required HITL approval", flush=True)
        return False

    try:
        await task
    except asyncio.CancelledError:
        return False
    return True


async def _run_profile(
    profile: ScenarioProfile,
    *,
    platform: Any,
    app_db: Any,
    goal_override: str | None,
) -> tuple[bool, ScenarioContext | None]:
    goals = list(profile.goals)
    if goal_override and len(goals) == 1:
        goals = [goal_override]

    from app.services import chat_service

    session = await app_db.create_session_record(f"smoke-{profile.name}", "demo")
    all_runs: list[Any] = []
    all_trace_sets: list[list[Any]] = []
    last_ctx: ScenarioContext | None = None

    for turn_idx, goal in enumerate(goals):
        print(f"[{profile.name}] turn {turn_idx + 1}/{len(goals)} goal:", goal[:80], flush=True)
        outcome = await chat_service.handle_chat(
            session.id, goal,
            executor=platform.executor, bus=platform.bus, platform=platform,
        )
        print(f"[{profile.name}] run:", outcome.run_id, flush=True)
        ctx = ScenarioContext(
            run_id=outcome.run_id,
            session_id=session.id,
            thread_id=session.thread_id,
            goal=goal,
            run=None,  # type: ignore[arg-type]
            traces=[],
            all_runs=all_runs,
            all_trace_sets=all_trace_sets,
        )
        if not await _wait_for_run(
            outcome.run_id,
            timeout_s=profile.timeout_s,
            profile=profile,
            ctx=ctx,
            app_db=app_db,
            platform=platform,
        ):
            return False, None

        run = await app_db.get_run_record(outcome.run_id)
        if run is None:
            print(f"[{profile.name}] FAIL run row missing", flush=True)
            return False, None
        traces = await app_db.list_traces(outcome.run_id)
        all_runs.append(run)
        all_trace_sets.append(traces)
        ctx.run = run
        ctx.traces = traces
        ctx.all_runs = all_runs
        ctx.all_trace_sets = all_trace_sets
        last_ctx = ctx
        print(f"[{profile.name}] status:", run.status)
        print(f"[{profile.name}] trace:", [t.event_type for t in traces])
        print(f"[{profile.name}] result:", (run.result_summary or "")[:280])

    if last_ctx is None or profile.validate is None:
        return True, last_ctx

    ok, failures = profile.validate(last_ctx)
    if failures:
        for note in failures:
            print(f"[{profile.name}] check FAIL:", note, flush=True)
    else:
        print(f"[{profile.name}] all checks passed", flush=True)
    return ok, last_ctx


async def _boot_platform(app_db: Any) -> tuple[Any, asyncio.subprocess.Process]:
    from app.services.platform import build_platform, set_platform

    jira_proc = await _spawn_jira_server()
    print("[boot] jira spawned", flush=True)
    await app_db.init_schema()
    platform = await build_platform()
    set_platform(platform)
    print("[boot] MCP servers:", await platform.manager.health(), flush=True)
    catalog = platform.registry.capability_catalog()
    print("[registry]", [a.name for a in catalog], flush=True)
    return platform, jira_proc


async def _shutdown(platform: Any, jira_proc: asyncio.subprocess.Process) -> None:
    from app.services.platform import set_platform

    try:
        await platform.shutdown()
        set_platform(None)
        await asyncio.sleep(0.1)
    except BaseException:  # noqa: BLE001
        print("[shutdown] best-effort teardown completed with noise", flush=True)
    if not jira_proc.returncode:
        jira_proc.kill()


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Azend end-to-end smoke scenarios")
    parser.add_argument(
        "--profile", "-p",
        default=os.environ.get("AZEND_E2E_PROFILE", "velocity"),
        help=f"scenario profile ({', '.join(PROFILE_NAMES)})",
    )
    parser.add_argument("--list-profiles", action="store_true", help="print profiles and exit")
    parser.add_argument(
        "--goal",
        default=None,
        help="override goal text (single-goal profiles only; ignores profile goal)",
    )
    args = parser.parse_args(argv)

    if args.list_profiles:
        for name, profile in SCENARIO_PROFILES.items():
            print(f"  {name:16} {profile.description}")
        print("  all              run every profile above")
        return 0

    profile_key = args.profile.strip().lower()
    profiles: list[ScenarioProfile]
    if profile_key == "all":
        profiles = list(SCENARIO_PROFILES.values())
    else:
        try:
            profiles = [get_profile(profile_key)]
        except KeyError:
            print(f"unknown profile {args.profile!r}; choose from: {', '.join(PROFILE_NAMES)}")
            return 2

    from app.models import db as app_db

    platform, jira_proc = await _boot_platform(app_db)
    results: list[tuple[str, bool]] = []

    try:
        goal_override = args.goal
        if goal_override is None and profile_key == "velocity" and len(profiles) == 1:
            goal_override = os.environ.get("AZEND_E2E_GOAL")

        for profile in profiles:
            print(f"\n=== scenario: {profile.name} ===", flush=True)
            ok, ctx = await _run_profile(
                profile,
                platform=platform,
                app_db=app_db,
                goal_override=goal_override if len(profiles) == 1 else None,
            )
            if ok and profile.name == "velocity" and ctx is not None:
                from app.memory import episodic
                hints = await episodic.hints_for_goal(
                    ctx.goal, thread_id=ctx.thread_id,
                )
                print(f"[{profile.name}] episodic hints:", len(hints), flush=True)
                if not hints:
                    ok = False
                    print(f"[{profile.name}] check FAIL: expected episodic hint after run")
            results.append((profile.name, ok))
    finally:
        await _shutdown(platform, jira_proc)

    print("\n=== summary ===", flush=True)
    all_ok = True
    for name, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  {name:16} {status}")
        all_ok = all_ok and ok
    print("E2E SMOKE:", "PASS" if all_ok else "FAIL", flush=True)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
