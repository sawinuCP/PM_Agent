"""Pytest fixtures: tmp DB env, fresh registry/fakes, MockLLM everywhere."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

# Pytest is the only exception to backend/.env: keep the suite on MockLLM
# so unit/integration tests never spend live inference credits.
os.environ["LLM_PROVIDER"] = "mock"
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/test.db")
os.environ.setdefault("CHECKPOINT_DB_PATH", "./data/test_cp.sqlite")
# Embeddings: keep tests keyless + deterministic. VOYAGE_API_KEY is scrubbed so
# the hash mock engages even when a developer .env has a real key, and the dim
# is shrunk to 8 to keep in-process stores tiny. (Env beats .env in
# pydantic-settings, so this pin cannot be overridden by config.)
os.environ["VOYAGE_API_KEY"] = ""
os.environ["EMBEDDINGS_PROVIDER"] = "hash"
os.environ["EMBEDDINGS_DIM"] = "8"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    """Fresh SQLite DB + cleared memory stores per test (isolation guarantees)."""
    from app.core.settings import get_settings
    from app.models import db

    url = f"sqlite+aiosqlite:///{tmp_path/'t.db'}"
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    db.reset_engine_cache()
    from app.memory import embeddings, episodic, semantic

    embeddings.clear_voyage_cache()
    episodic._STORES.clear()
    semantic.reset_store()
    from app.ai.provider import reset_provider
    from app.core.security import reset_chat_rate_limit

    reset_provider()
    reset_chat_rate_limit()
    yield
    episodic._STORES.clear()
    semantic.reset_store()
    db.reset_engine_cache()
    get_settings.cache_clear()
    reset_provider()
    reset_chat_rate_limit()


@pytest.fixture
def registry():
    from app.agents.registry import AgentRegistry

    return AgentRegistry.load("config/agents")


@pytest.fixture
def fake_manager():
    from tests.fakes import FakeMCPManager

    return FakeMCPManager()
