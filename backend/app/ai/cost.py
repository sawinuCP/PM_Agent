"""Cost tracking: token usage → per-node / per-run / per-session totals.

Pricing table is config (`config/pricing.yaml`). MockLLM estimates tokens
from prompt/completion text so the inspector can show usage on keyless runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.ai.tokens import count_tokens
from app.core.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    visible_completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_totals(self, cost_usd: float, *, calls: int = 1) -> dict[str, float | int]:
        return {
            "tokens": self.total_tokens,
            "prompt": self.prompt_tokens,
            "completion": self.completion_tokens,
            "reasoning": self.reasoning_tokens,
            "visible_completion": self.visible_completion_tokens or max(
                0, self.completion_tokens - self.reasoning_tokens),
            "cost_usd": round(cost_usd, 6),
            "calls": calls,
        }


def estimate_usage(prompt: str, completion: str = "") -> Usage:
    """Count tokens for a prompt/completion pair (tiktoken, char fallback)."""
    return Usage(
        prompt_tokens=count_tokens(prompt) if prompt else 0,
        completion_tokens=count_tokens(completion) if completion else 0,
    )


def cost_of(usage: Usage, model: str) -> float:
    return pricing_for(model).cost(usage.prompt_tokens, usage.completion_tokens)


@dataclass(frozen=True)
class Pricing:
    prompt_per_1k: float
    completion_per_1k: float

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens * self.prompt_per_1k +
                completion_tokens * self.completion_per_1k) / 1000.0


def pricing_for(model: str) -> Pricing:
    table = _pricing()
    entry = table.get(model, table.get("default"))
    if entry is None:
        entry = {"prompt_per_1k": 0.0, "completion_per_1k": 0.0}
    return Pricing(**entry)


@lru_cache
def _pricing() -> dict[str, dict[str, float]]:
    settings = get_settings()
    path = Path(settings.pricing_json)
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(data.get("models"), dict):
                return {k: {"prompt_per_1k": float(v.get("prompt_per_1k", 0.0)),
                            "completion_per_1k": float(v.get("completion_per_1k", 0.0))}
                        for k, v in data["models"].items()}
        except Exception:  # noqa: BLE001 - fall back to defaults on bad config
            logger.warning("pricing config invalid at %s; using defaults", path)
    defaults = {
        "prompt_per_1k": settings.cost_per_1k_prompt_default,
        "completion_per_1k": settings.cost_per_1k_completion_default,
    }
    return {"default": defaults, "mock": defaults}
