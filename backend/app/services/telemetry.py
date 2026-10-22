"""Platform telemetry roll-ups (Langfuse/LangSmith-style project stats).

Reads persisted runs only — no extra instrumentation and no change to the
orchestration path. Ranges are UTC: last 24h (hourly), 7d / 30d (daily).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from app.core.clock import utcnow
from app.models import db

RangeName = Literal["day", "week", "month"]

_RANGE_SPEC: dict[RangeName, tuple[timedelta, str]] = {
    "day": (timedelta(hours=24), "hour"),
    "week": (timedelta(days=7), "day"),
    "month": (timedelta(days=30), "day"),
}


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _usage(run: db.Run) -> dict[str, float]:
    raw = run.token_usage if isinstance(run.token_usage, dict) else {}
    prompt = float(raw.get("prompt") or 0)
    completion = float(raw.get("completion") or 0)
    reasoning = float(raw.get("reasoning") or 0)
    tokens = float(raw.get("tokens") or (prompt + completion))
    calls = float(raw.get("calls") or 0)
    cost = float(run.cost_usd or raw.get("cost_usd") or 0)
    return {
        "prompt": prompt,
        "completion": completion,
        "reasoning": reasoning,
        "tokens": tokens,
        "calls": calls,
        "cost": cost,
    }


def _bucket_key(when: datetime, grain: str) -> str:
    if grain == "hour":
        return when.replace(minute=0, second=0, microsecond=0).isoformat()
    return when.date().isoformat()


def _iter_buckets(start: datetime, end: datetime, grain: str) -> list[str]:
    keys: list[str] = []
    cursor = start.replace(minute=0, second=0, microsecond=0) if grain == "hour" else (
        datetime(start.year, start.month, start.day, tzinfo=UTC))
    step = timedelta(hours=1) if grain == "hour" else timedelta(days=1)
    while cursor < end:
        keys.append(_bucket_key(cursor, grain))
        cursor += step
    return keys or [_bucket_key(start, grain)]


def summarize_runs(runs: list[db.Run], *, range_name: RangeName,
                   start: datetime, end: datetime) -> dict[str, Any]:
    grain = _RANGE_SPEC[range_name][1]
    buckets = {
        key: {"traces": 0, "tokens": 0, "cost_usd": 0.0, "errors": 0,
              "latency_sum": 0.0, "latency_n": 0}
        for key in _iter_buckets(start, end, grain)
    }
    by_status: dict[str, int] = defaultdict(int)
    latencies: list[float] = []
    prompt = completion = tokens = calls = cost = reasoning = 0.0
    completed = failed = running = 0
    recent: list[dict[str, Any]] = []

    for run in runs:
        started = _as_aware(run.started_at) or start
        finished = _as_aware(run.finished_at)
        usage = _usage(run)
        status = (run.status or "unknown").lower()
        by_status[status] += 1
        if status == "completed":
            completed += 1
        elif status == "failed":
            failed += 1
        elif status in {"running", "pending", "awaiting_approval", "awaiting_clarification"}:
            running += 1
        prompt += usage["prompt"]
        completion += usage["completion"]
        reasoning += usage["reasoning"]
        tokens += usage["tokens"]
        calls += usage["calls"]
        cost += usage["cost"]
        duration_ms = None
        if finished and started:
            duration_ms = max(0.0, (finished - started).total_seconds() * 1000)
            latencies.append(duration_ms)
        key = _bucket_key(started, grain)
        if key in buckets:
            bucket = buckets[key]
            bucket["traces"] += 1
            bucket["tokens"] += int(usage["tokens"])
            bucket["cost_usd"] += usage["cost"]
            if status == "failed":
                bucket["errors"] += 1
            if duration_ms is not None:
                bucket["latency_sum"] += duration_ms
                bucket["latency_n"] += 1
        recent.append({
            "id": run.id,
            "status": run.status,
            "goal": (run.goal_text or "")[:160],
            "tokens": int(usage["tokens"]),
            "cost_usd": round(usage["cost"], 6),
            "started_at": started.isoformat(),
            "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
        })

    series = []
    for key, bucket in buckets.items():
        avg = (bucket["latency_sum"] / bucket["latency_n"]) if bucket["latency_n"] else None
        series.append({
            "bucket": key,
            "traces": bucket["traces"],
            "tokens": bucket["tokens"],
            "cost_usd": round(bucket["cost_usd"], 6),
            "errors": bucket["errors"],
            "avg_latency_ms": round(avg, 1) if avg is not None else None,
        })

    decided = completed + failed
    recent.sort(key=lambda row: row["started_at"], reverse=True)
    return {
        "range": range_name,
        "from_ts": start.isoformat(),
        "to_ts": end.isoformat(),
        "totals": {
            "traces": len(runs),
            "completed": completed,
            "failed": failed,
            "running": running,
            "tokens": int(tokens),
            "prompt_tokens": int(prompt),
            "completion_tokens": int(completion),
            "reasoning_tokens": int(reasoning),
            "cost_usd": round(cost, 6),
            "llm_calls": int(calls),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "success_rate": round(completed / decided, 4) if decided else None,
        },
        "series": series,
        "by_status": dict(by_status),
        "recent": recent[:25],
    }


async def collect_telemetry(range_name: RangeName) -> dict[str, Any]:
    if range_name not in _RANGE_SPEC:
        range_name = "week"
    window, _grain = _RANGE_SPEC[range_name]
    end = utcnow()
    start = end - window
    runs = await db.list_runs_since(start)
    return summarize_runs(runs, range_name=range_name, start=start, end=end)
