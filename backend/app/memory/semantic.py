"""Semantic memory (L4) — durable facts, preferences, and glossary.

Post-run extraction by a cheap LLM call, dedupe on cosine similarity threshold,
provision-time retrieval. Postgres stores rows in ``semantic_memories``; SQLite
falls back to an in-process store.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from app.ai.provider import get_provider
from app.core.settings import get_settings
from app.db.sql import semantic as semantic_sql
from app.memory import embeddings as emb
from app.memory.pg import get_psycopg_pool, vector_literal
from app.models.memory import SemanticFacts

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """\
Extract up to 3 durable facts learned from this run that would improve future
runs (domain parameters, user preferences, definitions). Reply ONLY with JSON:
{{"facts": ["..."]}}. If nothing durable was learned, reply {{"facts": []}}.
Run goal: {goal}
Step summaries: {summaries}"""

# scope key: (database_url) -> list[records]; user-scoped in production.
# Postgres path persists to the semantic_memories pgvector table instead.
_STORE: dict[str, list[dict[str, Any]]] = {}


async def relevant_facts_for(goal: str, role: str, *, top_k: int = 3) -> list[str]:
    """Deterministic retrieval used at agent provision time (L4 read path)."""
    settings = get_settings()
    if settings.database_url.startswith("postgresql"):
        rows = await asyncio.to_thread(_pg_search_sync, goal, role, top_k)
        return [row[0] for row in rows]
    records = _STORE.get(settings.database_url, [])
    if not records:
        return []
    query = emb.embed_text(f"{role}: {goal}", input_type="query")
    ranked = sorted(
        records,
        key=lambda r: emb.similarity_01(query, r["embedding"]),
        reverse=True,
    )
    return [r["content"] for r in ranked[:top_k]]


def _pg_search_sync(goal: str, role: str, top_k: int) -> list[tuple]:
    with get_psycopg_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(semantic_sql.SEMANTIC_SEARCH,
                    (vector_literal(emb.embed_text(f"{role}: {goal}", input_type="query")), top_k))
        return cur.fetchall()


async def extract_and_store(*, goal: str, step_summaries: list[str],
                            run_id: str) -> list[str]:
    """Post-run L4 write path: extraction, dedupe, upsert."""
    settings = get_settings()
    provider = get_provider()
    store = _STORE.setdefault(settings.database_url, [])
    try:
        extracted, _usage = await provider.generate_structured(
            "You extract durable knowledge from completed agent runs.",
            _EXTRACTION_PROMPT.format(goal=goal, summaries=step_summaries[:6]),
            SemanticFacts, tier="cheap")
        facts = extracted.facts
    except Exception as exc:  # noqa: BLE001 - extraction is best-effort by design
        logger.warning("semantic extraction skipped for run=%s: %s", run_id, exc)
        return []
    stored: list[str] = []
    use_pg = settings.database_url.startswith("postgresql")
    for fact in facts[:3]:
        if not isinstance(fact, str) or len(fact.strip()) < 8:
            continue
        fact = fact.strip()
        vector = await asyncio.to_thread(
            emb.embed_text, fact, input_type="document")
        if use_pg:
            stored.extend(await asyncio.to_thread(_pg_upsert_sync, fact, vector, run_id, settings))
            continue
        store = _STORE.setdefault(settings.database_url, [])
        duplicate = next(
            (r for r in store
             if emb.cosine(vector, r["embedding"]) >= settings.semantic_dedupe_similarity),
            None,
        )
        if duplicate is not None:
            duplicate["updated_at"] = datetime.now(UTC).isoformat()
            duplicate["importance"] = min(1.0, duplicate["importance"] + 0.1)
            continue
        store.append({
            "content": fact,
            "embedding": vector,
            "source_run_id": run_id,
            "importance": 0.5,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        })
        stored.append(fact)
    if stored:
        logger.info("semantic facts stored: %d", len(stored))
    return stored


def _pg_upsert_sync(fact: str, vector: list[float], run_id: str,
                    settings) -> list[str]:  # noqa: ANN001 - settings type
    """Dedupe-on-write: cosine >= threshold bumps importance instead of inserting."""
    threshold = 1.0 - settings.semantic_dedupe_similarity  # cosine similarity -> distance
    literal = vector_literal(vector)
    with get_psycopg_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(semantic_sql.SEMANTIC_DEDUPE_PROBE, (literal, literal))
        row = cur.fetchone()
        if row is not None and (1 - float(row[0])) >= settings.semantic_dedupe_similarity:
            cur.execute(semantic_sql.SEMANTIC_BUMP_IMPORTANCE, (literal, threshold))
            return []
        cur.execute(semantic_sql.SEMANTIC_INSERT, (fact, literal, run_id, 0.5))
    return [fact]


def reset_store() -> None:
    _STORE.clear()
