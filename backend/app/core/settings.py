"""Environment-driven application settings (single source of configuration truth).

Runtime LLM mode is controlled only by ``LLM_PROVIDER`` in ``backend/.env``:

* ``mock``   — deterministic scripted provider (keyless)
* ``openai`` — live OpenAI-compatible endpoint (requires ``OPENAI_API_KEY``)
* ``auto``   — live when a key is present, otherwise mock

Nothing else (scripts, compose, process leftovers) should force a different
provider. Pytest is the sole exception: it pins mock so the suite stays free.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values, load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def backend_root() -> Path:
    """Directory that owns backend/.env — independent of process cwd."""
    return Path(__file__).resolve().parents[2]


def env_file_path() -> Path:
    return backend_root() / ".env"


class Settings(BaseSettings):
    """Application configuration, loaded from environment / backend/.env."""

    model_config = SettingsConfigDict(
        env_file=env_file_path(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # An empty process env var must not hide a real value in backend/.env.
        env_ignore_empty=True,
    )

    app_name: str = "Dynamic AI Agent Orchestration Platform"
    api_prefix: str = "/api"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    cors_allow_credentials: bool = False
    cors_max_age: int = 600
    # Optional protection for non-local deployments. Empty keeps the keyless
    # assessment demo frictionless; set it behind a reverse proxy in production.
    api_token: str | None = None

    llm_provider: str = "openai"  # openai | mock (mock: pytest only)
    openai_api_key: str | None = None  # any prefix is valid; trimmed only
    openai_base_url: str = "https://api.commandcode.ai/provider/v1"
    openai_model: str = "z-ai/glm-5.3-flash"
    llm_temperature: float = 0.2
    # GLM-5.3-flash window (input) and max completion length per API call.
    llm_context_tokens: int = 1_048_576
    llm_max_tokens: int = 131_072
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 2
    llm_planner_model: str | None = None
    llm_reasoning_enabled: bool = True
    llm_reasoning_effort_cheap: str = "low"
    llm_reasoning_effort_standard: str = "high"
    llm_reasoning_effort_strong: str = "high"
    llm_reasoning_effort_planner: str = "high"
    planner_first: bool = True
    max_clarify_replans: int = 3

    # Voyage voyage-3.5-lite: 1024 dims, 32k ctx, $0.02/M tok, 200M free tokens.
    embeddings_provider: Literal["auto", "voyage", "hash"] = "auto"
    voyage_api_key: str | None = None  # any prefix is valid; trimmed only
    voyage_model: str = "voyage-3.5-lite"
    # Must match the active model's output dimension; guarded at call time.
    embeddings_dim: int = 1024

    database_url: str = "sqlite+aiosqlite:///./data/orchestrator.db"
    checkpoint_db_path: str = "./data/checkpoints.sqlite"
    # SQLAlchemy async pool (Postgres). SQLite uses NullPool (see session.py).
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout_s: float = 30.0
    db_pool_recycle_s: int = 1800
    db_sqlite_busy_timeout_s: float = 30.0
    # psycopg pool for pgvector memory paths (episodic / semantic).
    db_psycopg_pool_min: int = 1
    db_psycopg_pool_max: int = 8
    # Postgres: apply Alembic revisions on startup (disable when CI runs migrations).
    db_auto_migrate: bool = True

    episodic_ttl_days: int = 14
    episodic_weight_similarity: float = 0.60
    episodic_weight_recency: float = 0.25
    episodic_weight_success: float = 0.15
    semantic_dedupe_similarity: float = 0.92
    summarization_threshold: float = 0.80  # fraction of llm_context_tokens
    summarization_reset: float = 0.30
    summary_min_turns_gap: int = 3

    mcp_servers_config: str = "config/mcp_servers.yaml"
    mcp_circuit_failure_threshold: int = 5
    mcp_circuit_recovery_s: float = 30.0
    sandbox_max_runtime_s: int = 25
    sandbox_call_timeout_s: float = 40.0

    agents_config_dir: str = "config/agents"
    default_working_token_budget: int = 16000

    # 0 disables. Caps accidental /chat loops against a paid model.
    chat_rate_limit_per_minute: int = 30
    worker_native_tools: bool = True
    worker_tool_shortlist_max: int = 32
    worker_max_tool_rounds: int = 4
    worker_max_tool_rounds_analyst: int = 3
    worker_max_tools_per_wave: int = 6
    worker_tool_error_retries: int = 2

    log_level: str = "INFO"
    log_json: bool = False
    trace_write_batch_size: int = 8
    pricing_json: str = "config/pricing.yaml"
    cost_per_1k_prompt_default: float = 0.00015
    cost_per_1k_completion_default: float = 0.0006

    demo_user_id: str = "demo"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _validate_cors_origins(cls, value: object) -> str:
        from app.core.cors import normalize_cors_origins_csv

        if value is None:
            return ""
        text = str(value).strip()
        if not text:
            return ""
        return normalize_cors_origins_csv(text)

    @field_validator("llm_provider", mode="before")
    @classmethod
    def _normalize_llm_provider(cls, value: object) -> object:
        if value is None:
            return "auto"
        return str(value).strip().lower() or "auto"

    @field_validator(
        "openai_api_key", "voyage_api_key", "openai_base_url",
        "openai_model", "llm_planner_model", mode="before",
    )
    @classmethod
    def _strip_secret_or_url(cls, value: object) -> object:
        """Trim quotes/whitespace only. Keys may start with any prefix."""
        if value is None:
            return None
        text = str(value).strip().strip('"').strip("'")
        return text or None


# These keys are owned by backend/.env at runtime. A stale shell export must
# not silently switch mock ↔ openai or swap the CommandCode key.
_ENV_OWNED_LLM_KEYS = (
    "LLM_PROVIDER",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "LLM_PLANNER_MODEL",
    "LLM_CONTEXT_TOKENS",
    "LLM_MAX_TOKENS",
)


def load_app_env() -> Path:
    """Load backend/.env. LLM_* / OPENAI_* in the file always win.

    Other variables fill in only when the process has not already set them
    (so scripts can isolate DATABASE_URL). Pytest skips this loader.
    """
    path = env_file_path()
    if not path.is_file() or os.environ.get("PYTEST_CURRENT_TEST"):
        return path
    load_dotenv(path, override=False)
    parsed = dotenv_values(path)
    for key in _ENV_OWNED_LLM_KEYS:
        raw = parsed.get(key)
        if raw is None:
            continue
        text = str(raw).strip().strip('"').strip("'")
        if text:
            os.environ[key] = text
    return path


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor. LLM keys come from backend/.env via load_app_env."""
    load_app_env()
    return Settings()
