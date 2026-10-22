"""Initial Postgres schema (app tables + pgvector memory).

Revision ID: 001_initial
Revises:
Create Date: 2026-09-07

Uses shared DDL from ``app.db.sql.postgres_ddl`` so Alembic and legacy bootstrap
stay aligned. Memory vectors are created at 1024 dimensions (Voyage default);
run ``scripts/migrate_voyage_1024.py`` after changing embedding models, or add a
new Alembic revision for dimension changes.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from app.db.sql.postgres_ddl import (
    postgres_app_table_ddl,
    postgres_extension_ddl,
    postgres_memory_table_ddl,
    postgres_session_alter_ddl,
)

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_VECTOR_DIM = 1024


def upgrade() -> None:
    for statement in postgres_extension_ddl():
        op.execute(statement)
    for statement in postgres_app_table_ddl():
        op.execute(statement)
    for statement in postgres_session_alter_ddl():
        op.execute(statement)
    for statement in postgres_memory_table_ddl(DEFAULT_VECTOR_DIM):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS semantic_memories CASCADE")
    op.execute("DROP TABLE IF EXISTS episodic_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS tool_results CASCADE")
    op.execute("DROP TABLE IF EXISTS traces CASCADE")
    op.execute("DROP TABLE IF EXISTS runs CASCADE")
    op.execute("DROP TABLE IF EXISTS messages CASCADE")
    op.execute("DROP TABLE IF EXISTS sessions CASCADE")
