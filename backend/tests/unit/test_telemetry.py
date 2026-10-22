"""Telemetry roll-ups stay keyless and do not change run persistence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.models import db
from app.models.schemas import TelemetryOut
from app.services.telemetry import summarize_runs


def test_summarize_runs_buckets_and_success_rate() -> None:
    now = datetime(2026, 9, 5, 15, 0, tzinfo=UTC)

    class _R:
        def __init__(self, **kw):  # noqa: ANN003
            self.__dict__.update(kw)

    runs = [
        _R(id="a", status="completed", goal_text="velocity",
           token_usage={"tokens": 100, "prompt": 70, "completion": 30, "calls": 2},
           cost_usd=0.01,
           started_at=now - timedelta(hours=2),
           finished_at=now - timedelta(hours=2) + timedelta(seconds=3)),
        _R(id="b", status="failed", goal_text="broken",
           token_usage={"tokens": 20, "prompt": 20, "completion": 0, "calls": 1},
           cost_usd=0.0,
           started_at=now - timedelta(hours=1),
           finished_at=now - timedelta(hours=1) + timedelta(seconds=1)),
    ]
    report = summarize_runs(
        runs, range_name="day", start=now - timedelta(hours=24), end=now)
    TelemetryOut.model_validate(report)
    assert report["totals"]["traces"] == 2
    assert report["totals"]["completed"] == 1
    assert report["totals"]["failed"] == 1
    assert report["totals"]["tokens"] == 120
    assert report["totals"]["success_rate"] == 0.5
    assert report["series"]
    assert sum(b["traces"] for b in report["series"]) == 2


def test_telemetry_endpoint_reads_persisted_runs() -> None:
    async def run() -> dict:
        await db.init_schema()
        session = await db.create_session_record("tel", "demo")
        created = await db.create_run_record(
            session.id, session.thread_id, "open tickets", run_id="tel-run-1")
        await db.update_run_record(
            created.id, status="completed", cost_usd=0.002,
            token_usage={"tokens": 40, "prompt": 30, "completion": 10, "calls": 1},
            finished_at=datetime.now(UTC))
        from app.services.telemetry import collect_telemetry

        return await collect_telemetry("week")

    report = asyncio.run(run())
    assert report["totals"]["traces"] >= 1
    assert any(row["id"] == "tel-run-1" for row in report["recent"])
