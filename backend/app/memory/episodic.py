"""Episodic memory (L3) — how goals were solved; hints at plan time, never data answers.

Written once per completed run; read at planning time as recency-weighted
hints. Postgres stores rows in ``episodic_runs`` (pgvector + HNSW); SQLite
falls back to an in-process store with identical scoring.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.settings import get_settings
from app.db.sql import episodic as episodic_sql
from app.memory import embeddings as emb
from app.memory.pg import get_psycopg_pool, vector_literal

logger = logging.getLogger(__name__)

_STORES: dict[str, list[dict[str, Any]]] = {}


def _ttl_cutoff() -> datetime:
    return datetime.now(UTC) - timedelta(days=get_settings().episodic_ttl_days)


async def record_run(*, thread_id: str, goal_text: str, plan: dict[str, Any],
                     outcome: str, cost_usd: float, duration_s: float) -> None:
    """Store one episodic record (called post-run)."""
    settings = get_settings()
    record = {
        "thread_id": thread_id,
        "goal_text": goal_text,
        "embedding": await asyncio.to_thread(
            emb.embed_text, goal_text, input_type="document"),
        "plan": plan,
        "agents": [s.get("agent") for s in plan.get("steps", [])],
        "tools": sorted({t for s in plan.get("steps", []) for t in s.get("tools", [])}),
        "outcome": outcome,  # success | failure
        "cost_usd": cost_usd,
        "duration_s": duration_s,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if settings.database_url.startswith("postgresql"):
        await _pg_insert_episodic(record)
    else:
        _STORES.setdefault(settings.database_url, []).append(record)
    logger.info("episodic record stored for thread=%s outcome=%s", thread_id, outcome)


def _pg_insert_episodic_sync(record: dict[str, Any]) -> None:
    with get_psycopg_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            episodic_sql.EPISODIC_INSERT,
            (record["thread_id"], record["goal_text"],
             vector_literal(record["embedding"]),
             json.dumps(record["plan"]),
             record["agents"], record["tools"], record["outcome"],
             record["cost_usd"], record["duration_s"]),
        )


async def _pg_insert_episodic(record: dict[str, Any]) -> None:
    """Durable pgvector insert; runs in a thread to keep the loop free."""
    await asyncio.to_thread(_pg_insert_episodic_sync, record)


async def hints_for_goal(
    goal_text: str, *, thread_id: str | None = None, top_k: int = 2,
) -> list[dict[str, Any]]:
    """Recency-weighted retrieval for the Planner (structure-only hints).

    When ``thread_id`` is set, only prior runs in that conversation thread are
    considered so unrelated sessions do not leak plan structure.
    """
    settings = get_settings()
    if settings.database_url.startswith("postgresql"):
        try:
            rows = await asyncio.to_thread(
                _pg_search_episodic_sync, goal_text, top_k, thread_id)
            hints = _pg_rows_to_hints(rows)
        except Exception:  # noqa: BLE001 - memory must never block planning
            logger.warning("episodic retrieval unavailable; planning without hints")
            hints = []
    else:
        hints = _local_hints(goal_text, top_k=top_k, thread_id=thread_id)
    if hints:
        logger.info("episodic hints surfaced: %d thread=%s", len(hints), thread_id)
    return hints


def _pg_search_episodic_sync(
    goal_text: str, top_k: int, thread_id: str | None = None,
) -> list[tuple]:
    vector = emb.embed_text(goal_text, input_type="query")
    ttl = str(get_settings().episodic_ttl_days)
    with get_psycopg_pool().connection() as conn, conn.cursor() as cur:
        if thread_id:
            cur.execute(
                episodic_sql.episodic_search_sql(thread_scoped=True),
                (vector_literal(vector), ttl, thread_id, top_k),
            )
        else:
            cur.execute(
                episodic_sql.episodic_search_sql(thread_scoped=False),
                (vector_literal(vector), ttl, top_k),
            )
        return cur.fetchall()


def _pg_rows_to_hints(rows: list[tuple]) -> list[dict[str, Any]]:
    hints = []
    for score, goal, plan, outcome in rows:
        steps = (plan or {}).get("steps", []) if isinstance(plan, dict) else []
        hints.append({
            "score": round(float(score), 4),
            "goal": goal,
            "plan_outline": [
                {"agent": s.get("agent"), "task": s.get("task"), "tools": s.get("tools")}
                for s in steps
            ],
            "outcome": outcome,
        })
    return hints


def _local_hints(
    goal_text: str, *, top_k: int, thread_id: str | None = None,
) -> list[dict[str, Any]]:
    settings = get_settings()
    records = _STORES.get(settings.database_url, [])
    query_vec = emb.embed_text(goal_text, input_type="query")
    if not records:
        return []
    cutoff = _ttl_cutoff().isoformat()
    now = datetime.now(UTC)
    scored: list[tuple[float, dict[str, Any]]] = []
    for record in records:
        if thread_id and record.get("thread_id") != thread_id:
            continue
        if record["created_at"] < cutoff:
            continue
        similarity = emb.similarity_01(query_vec, record["embedding"])
        age_days = (now - datetime.fromisoformat(record["created_at"])).total_seconds() / 86400
        recency = math.exp(-age_days / 7.0)  # weekly decay, [0,1]
        success = 1.0 if record["outcome"] == "success" else 0.0
        score = (settings.episodic_weight_similarity * similarity +
                 settings.episodic_weight_recency * recency +
                 settings.episodic_weight_success * success)
        scored.append((score, record))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    hints = []
    for score, record in scored[:top_k]:
        hints.append({
            "score": round(score, 4),
            "goal": record["goal_text"],
            "plan_outline": [
                {"agent": s.get("agent"), "task": s.get("task"), "tools": s.get("tools")}
                for s in record.get("plan", {}).get("steps", [])
            ],
            "outcome": record["outcome"],
        })
    return hints

