"""Postgres admin queries for pgvector dimension reconciliation."""

from app.db.tables import EPISODIC_RUNS, SEMANTIC_MEMORIES

REGCLASS_EPISODIC_RUNS = f"SELECT to_regclass('public.{EPISODIC_RUNS}')"

GOAL_EMBEDDING_COLUMN_TYPE = f"""
SELECT format_type(a.atttypid, a.atttypmod)
FROM pg_attribute a
WHERE a.attrelid = '{EPISODIC_RUNS}'::regclass
  AND a.attname = 'goal_embedding' AND NOT a.attisdropped
"""

DROP_SEMANTIC_MEMORIES = f"DROP TABLE IF EXISTS {SEMANTIC_MEMORIES} CASCADE"
DROP_EPISODIC_RUNS = f"DROP TABLE IF EXISTS {EPISODIC_RUNS} CASCADE"

PGVECTOR_EXTENSION = "SELECT extname FROM pg_extension WHERE extname = 'vector'"
COLUMN_EXISTS = (
    "SELECT 1 FROM information_schema.columns"
    " WHERE table_name=%s AND column_name=%s"
)
