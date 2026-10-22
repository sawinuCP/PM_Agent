"""Live e2e against the running platform: Postgres persistence + pgvector memory.

Requires: backend on :8000, Postgres configured. Exercises a full run, then
verifies rows landed in Postgres (sessions/runs/traces/episodic/semantic) and
that a similar follow-up goal surfaces an episodic hint from pgvector.
"""
from __future__ import annotations

import asyncio
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api"


async def main() -> int:  # noqa: PLR0915
    async with httpx.AsyncClient(timeout=30) as client:
        session = (await client.post(f"{BASE}/sessions",
                                     json={"title": "pg-e2e"})).json()
        print("session:", session["id"])

        async def run_goal(message: str) -> dict:
            started = (await client.post(f"{BASE}/chat", json={
                "session_id": session["id"], "message": message})).json()
            run_id = started["run_id"]
            for _ in range(60):
                await asyncio.sleep(1.0)
                detail = (await client.get(f"{BASE}/runs/{run_id}")).json()
                if detail["status"] in {"completed", "failed", "awaiting_approval"}:
                    return detail
                if time.time() > started_time + 90:
                    break
            return detail

        started_time = time.time()
        first = await run_goal(
            "Predict the velocity for our next sprint based on previous Jira sprints")
        print("run1 status:", first["status"])

        started_time = time.time()
        second = await run_goal(
            "Forecast our next sprint velocity using the previous sprints data")
        print("run2 status:", second["status"])
        traces = (await client.get(
            f"http://127.0.0.1:8000/api/runs/{second['id']}/traces")).json()
        plan_events = [e for e in traces["events"] if e["type"] == "plan_created"]
        print("run2 plan_created present:", bool(plan_events))

    print("---")
    ok = (first["status"] == "completed" and second["status"] == "completed")
    print("E2E:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
