"""One-time migration: re-embed memory tables for Voyage voyage-3.5-lite (1024d).

Switching embedding models changes the vector space, so the old 384-dim
bge-small/pgvector columns cannot be mixed with new 1024-dim Voyage vectors
(the runtime dim guard refuses). This script performs the safe in-place
migration with zero history loss:

  1. Pre-flight: one live Voyage embed — aborts before touching the DB if the
     API is unreachable or misconfigured.
  2. episodic_runs: widen `goal_embedding` to vector(1024) (drop HNSW index,
     ALTER, re-embed every row from goal_text, recreate index). All other
     columns (plan, agents, tools, outcome, cost, timing) are untouched.
  3. semantic_memories: same for `embedding` (re-embed from content). The
     importance-bump dedupe path means content is the source of truth.
  4. Verifies every row: vector_dims(embedding) == 1024, non-null.

Usage (backend venv):
    cd backend
    ..\\.venv\\Scripts\\python.exe scripts\\migrate_voyage_1024.py

Idempotent: re-running after a successful pass is a no-op (0 rows to fix).
Safe to re-run after a partial failure (e.g. network drop mid-table): rows
already re-embedded keep their fresh vectors, the rest are retried.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

os.chdir(BACKEND)  # .env lives in backend/

from app.core.settings import get_settings  # noqa: E402
from app.db.sql.postgres_admin import COLUMN_EXISTS, PGVECTOR_EXTENSION  # noqa: E402
from app.db.sql.voyage_migration import (  # noqa: E402
    ALTER_EPISODIC_EMBEDDING,
    ALTER_SEMANTIC_EMBEDDING,
    INDEX_DDL,
    SELECT_EPISODIC_TO_REEMBED,
    SELECT_SEMANTIC_TO_REEMBED,
    TARGET_DIM,
    UPDATE_EPISODIC_EMBEDDING,
    UPDATE_SEMANTIC_EMBEDDING,
    VERIFY_VECTOR_DIM_COUNTS,
)
from app.memory.pg import vector_literal  # noqa: E402


def main() -> int:
    settings = get_settings()
    if not settings.database_url.startswith("postgresql"):
        print(f"ABORT: DATABASE_URL is not Postgres ({settings.database_url})")
        return 2
    if not settings.voyage_api_key:
        print("ABORT: VOYAGE_API_KEY not set; refusing to write mock vectors")
        return 2

    # Pre-flight: prove the Voyage path works before mutating anything.
    from app.memory import embeddings as emb

    try:
        probe = emb.embed_text("voyage migration pre-flight probe",
                               input_type="document")
    except Exception as exc:  # noqa: BLE001 - report and bail out cleanly
        print(f"ABORT: Voyage pre-flight embed failed: {type(exc).__name__}: {exc}")
        return 2
    if len(probe) != TARGET_DIM:
        print(f"ABORT: Voyage returned dim {len(probe)}, expected {TARGET_DIM} "
              f"(check VOYAGE_MODEL={settings.voyage_model})")
        return 2
    print(f"pre-flight OK: voyage model={settings.voyage_model} dim={len(probe)}")

    import psycopg

    conninfo = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    with psycopg.connect(conninfo) as conn, conn.cursor() as cur:
        cur.execute(PGVECTOR_EXTENSION)
        if cur.fetchone() is None:
            print("ABORT: pgvector extension not installed")
            return 2

        for table, column, text_column, select_sql, update_sql, alter_sql in (
            ("episodic_runs", "goal_embedding", "goal_text",
             SELECT_EPISODIC_TO_REEMBED, UPDATE_EPISODIC_EMBEDDING, ALTER_EPISODIC_EMBEDDING),
            ("semantic_memories", "embedding", "content",
             SELECT_SEMANTIC_TO_REEMBED, UPDATE_SEMANTIC_EMBEDDING, ALTER_SEMANTIC_EMBEDDING),
        ):
            cur.execute(COLUMN_EXISTS, (table, column))
            if cur.fetchone() is None:
                print(f"{table}.{column} missing; skipping (schema not created yet)")
                continue
            cur.execute(f"ALTER TABLE {table} DROP INDEX IF EXISTS "
                        f"ix_{table.split('_')[0]}_embedding")  # noqa: S608
            cur.execute(alter_sql)
            cur.execute(select_sql, (TARGET_DIM,))
            rows = cur.fetchall()
            print(f"{table}: re-embedding {len(rows)} row(s) from {text_column}")
            for row_id, text in rows:
                vec = emb.embed_text(text, input_type="document")
                cur.execute(update_sql, (vector_literal(vec), row_id))

        for stmt in INDEX_DDL:
            cur.execute(stmt)
        conn.commit()

        cur.execute(VERIFY_VECTOR_DIM_COUNTS, (TARGET_DIM, TARGET_DIM))
        failures = 0
        for table_name, bad, total in cur.fetchall():
            print(f"verify {table_name}: {total - bad}/{total} rows at dim {TARGET_DIM}")
            failures += int(bad)
    if failures:
        print(f"MIGRATION INCOMPLETE: {failures} row(s) not at dim {TARGET_DIM}")
        return 1
    print("MIGRATION OK: episodic_runs + semantic_memories re-embedded for Voyage 1024d")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
