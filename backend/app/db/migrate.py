"""Alembic helpers — sync URL conversion and programmatic upgrade."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from app.core.settings import backend_root, get_settings


def sync_database_url(url: str | None = None) -> str:
    """Convert async SQLAlchemy Postgres URL to a sync psycopg URL for Alembic."""
    raw = url if url is not None else get_settings().database_url
    if raw.startswith("postgresql+asyncpg://"):
        return raw.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+psycopg://", 1)
    return raw


def is_postgres_url(url: str | None = None) -> bool:
    raw = url if url is not None else get_settings().database_url
    return raw.startswith("postgresql")


def alembic_config() -> Config:
    root = backend_root()
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", sync_database_url())
    cfg.attributes["configure_logging"] = False
    return cfg


def run_alembic_upgrade(revision: str = "head") -> None:
    """Apply pending Alembic revisions (Postgres only; no-op caller guard elsewhere)."""
    command.upgrade(alembic_config(), revision)
