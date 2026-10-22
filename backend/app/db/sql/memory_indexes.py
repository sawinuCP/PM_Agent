"""pgvector HNSW indexes shared by bootstrap and migration scripts."""

MEMORY_HNSW_INDEX_DDL: list[str] = [
    """CREATE INDEX IF NOT EXISTS ix_episodic_embedding
        ON episodic_runs USING hnsw (goal_embedding vector_cosine_ops)""",
    """CREATE INDEX IF NOT EXISTS ix_semantic_embedding
        ON semantic_memories USING hnsw (embedding vector_cosine_ops)""",
]
