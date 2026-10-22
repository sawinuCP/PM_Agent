"""Voyage 1024-d migration SQL (shared with scripts/migrate_voyage_1024.py)."""

from app.db.sql.memory_indexes import MEMORY_HNSW_INDEX_DDL
from app.db.tables import EPISODIC_RUNS, SEMANTIC_MEMORIES

TARGET_DIM = 1024

ALTER_EPISODIC_EMBEDDING = (
    f"ALTER TABLE {EPISODIC_RUNS} ALTER COLUMN goal_embedding "
    "TYPE vector(1024) USING goal_embedding::text::vector"
)
ALTER_SEMANTIC_EMBEDDING = (
    f"ALTER TABLE {SEMANTIC_MEMORIES} ALTER COLUMN embedding "
    "TYPE vector(1024) USING embedding::text::vector"
)

SELECT_EPISODIC_TO_REEMBED = f"""
SELECT id, goal_text FROM {EPISODIC_RUNS}
WHERE goal_embedding IS NULL OR vector_dims(goal_embedding) <> %s
"""

SELECT_SEMANTIC_TO_REEMBED = f"""
SELECT id, content FROM {SEMANTIC_MEMORIES}
WHERE embedding IS NULL OR vector_dims(embedding) <> %s
"""

UPDATE_EPISODIC_EMBEDDING = f"UPDATE {EPISODIC_RUNS} SET goal_embedding = %s::vector WHERE id = %s"
UPDATE_SEMANTIC_EMBEDDING = f"UPDATE {SEMANTIC_MEMORIES} SET embedding = %s::vector WHERE id = %s"

VERIFY_VECTOR_DIM_COUNTS = f"""
SELECT '{EPISODIC_RUNS}' AS table_name,
       count(*) FILTER (WHERE goal_embedding IS NULL
                        OR vector_dims(goal_embedding) <> %s) AS bad,
       count(*) AS total
FROM {EPISODIC_RUNS}
UNION ALL
SELECT '{SEMANTIC_MEMORIES}',
       count(*) FILTER (WHERE embedding IS NULL
                        OR vector_dims(embedding) <> %s),
       count(*)
FROM {SEMANTIC_MEMORIES}
"""

INDEX_DDL = MEMORY_HNSW_INDEX_DDL
