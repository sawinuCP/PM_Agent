"""Postgres DDL for app tables and pgvector memory tables."""

from app.db.sql.memory_indexes import MEMORY_HNSW_INDEX_DDL


def postgres_extension_ddl() -> list[str]:
    return ["CREATE EXTENSION IF NOT EXISTS vector"]


def postgres_app_table_ddl() -> list[str]:
    return [
        """CREATE TABLE IF NOT EXISTS sessions (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL DEFAULT 'demo',
            title VARCHAR(255) NOT NULL DEFAULT 'New session',
            thread_id VARCHAR(36) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            message_count INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            total_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
            conversation_summary TEXT,
            memory_window_tokens INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS messages (
            id VARCHAR(36) PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            run_id VARCHAR(36),
            role VARCHAR(20) NOT NULL,
            content JSONB NOT NULL,
            token_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        "CREATE INDEX IF NOT EXISTS ix_messages_session_id ON messages (session_id)",
        """CREATE TABLE IF NOT EXISTS runs (
            id VARCHAR(36) PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            thread_id VARCHAR(36) NOT NULL,
            goal_text TEXT NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'pending',
            plan JSONB,
            result_summary TEXT,
            error TEXT,
            token_usage JSONB,
            cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ
        )""",
        "CREATE INDEX IF NOT EXISTS ix_runs_session_id ON runs (session_id)",
        "CREATE INDEX IF NOT EXISTS ix_runs_thread_id ON runs (thread_id)",
        """CREATE TABLE IF NOT EXISTS traces (
            id VARCHAR(36) PRIMARY KEY,
            run_id VARCHAR(36) NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL,
            event_type VARCHAR(40) NOT NULL,
            agent_name VARCHAR(100),
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        "CREATE INDEX IF NOT EXISTS ix_traces_run_id ON traces (run_id)",
        """CREATE TABLE IF NOT EXISTS tool_results (
            id VARCHAR(36) PRIMARY KEY,
            run_id VARCHAR(36) NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            step_id VARCHAR(20) NOT NULL,
            tool_name VARCHAR(100) NOT NULL,
            args_digest JSONB NOT NULL,
            payload JSONB,
            digest TEXT NOT NULL,
            byte_size INTEGER NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'ok',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        "CREATE INDEX IF NOT EXISTS ix_tool_results_run_id ON tool_results (run_id)",
    ]


def postgres_session_alter_ddl() -> list[str]:
    return [
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS conversation_summary TEXT",
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS memory_window_tokens INTEGER NOT NULL DEFAULT 0",
    ]


def postgres_memory_table_ddl(dim: int) -> list[str]:
    return [
        f"""CREATE TABLE IF NOT EXISTS episodic_runs (
            id BIGSERIAL PRIMARY KEY,
            thread_id TEXT NOT NULL,
            goal_text TEXT NOT NULL,
            goal_embedding vector({dim}),
            plan JSONB,
            agents TEXT[],
            tools TEXT[],
            outcome TEXT NOT NULL DEFAULT 'success',
            cost_usd REAL NOT NULL DEFAULT 0,
            duration_s REAL NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        *MEMORY_HNSW_INDEX_DDL[:1],
        f"""CREATE TABLE IF NOT EXISTS semantic_memories (
            id BIGSERIAL PRIMARY KEY,
            scope TEXT NOT NULL DEFAULT 'user',
            content TEXT NOT NULL,
            embedding vector({dim}),
            source_run_id TEXT,
            importance REAL NOT NULL DEFAULT 0.5,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        MEMORY_HNSW_INDEX_DDL[1],
    ]


def postgres_bootstrap_ddl(dim: int) -> list[str]:
    """Full idempotent Postgres bootstrap (extension, app tables, memory, alters)."""
    return [
        *postgres_extension_ddl(),
        *postgres_app_table_ddl(),
        *postgres_session_alter_ddl(),
        *postgres_memory_table_ddl(dim),
    ]
