"""Semantic memory (L4) psycopg queries."""

from app.db.tables import SEMANTIC_MEMORIES

SEMANTIC_SEARCH = f"""
SELECT content
FROM {SEMANTIC_MEMORIES}
ORDER BY embedding <=> %s
LIMIT %s
"""

SEMANTIC_DEDUPE_PROBE = f"""
SELECT (embedding <=> %s) AS distance, importance
FROM {SEMANTIC_MEMORIES}
ORDER BY embedding <=> %s
LIMIT 1
"""

SEMANTIC_INSERT = f"""
INSERT INTO {SEMANTIC_MEMORIES} (content, embedding, source_run_id, importance)
VALUES (%s, %s, %s, %s)
"""

SEMANTIC_BUMP_IMPORTANCE = (
    f"UPDATE {SEMANTIC_MEMORIES} "
    "SET importance = LEAST(1.0, importance + 0.1), updated_at = now() "
    "WHERE embedding <=> %s <= %s"
)
