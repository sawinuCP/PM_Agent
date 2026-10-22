"""Episodic memory (L3) psycopg queries."""

from __future__ import annotations

from app.core.settings import get_settings
from app.db.tables import EPISODIC_RUNS

EPISODIC_INSERT = f"""
INSERT INTO {EPISODIC_RUNS}
    (thread_id, goal_text, goal_embedding, plan, agents, tools, outcome,
     cost_usd, duration_s)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_RECENCY_HALF_LIFE_S = 604800.0  # weekly decay, matches SQL-side scoring


def _score_expression(*, embedding_col: str = "goal_embedding") -> str:
    weights = get_settings()
    return (
        f"({weights.episodic_weight_similarity} * (1 - ({embedding_col} <=> %s)::real) "
        f"+ {weights.episodic_weight_recency} * exp(- EXTRACT(EPOCH FROM (now() - created_at)) / {_RECENCY_HALF_LIFE_S}) "
        f"+ {weights.episodic_weight_success} * (CASE WHEN outcome = 'success' THEN 1.0 ELSE 0.0 END))"
    )


def episodic_search_sql(*, thread_scoped: bool = False) -> str:
    """Build TTL-filtered episodic hint query (optionally scoped to one thread)."""
    score = _score_expression()
    where = "WHERE created_at >= now() - (%s || ' days')::interval"
    if thread_scoped:
        where += "\n  AND thread_id = %s"
    return f"""SELECT {score} AS score,
       goal_text, plan, outcome
FROM {EPISODIC_RUNS}
{where}
ORDER BY score DESC
LIMIT %s"""
