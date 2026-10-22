"""Embedding provider — Voyage AI (production) + deterministic hash mock (tests).

- voyage   Voyage AI REST API (https://api.voyageai.com/v1/embeddings), used
           via httpx with no extra dependency. Model voyage-3.5-lite: 1024
           dims, 32k context, $0.02/M tokens, 200M free tokens per account.
           input_type="query" on memory reads / "document" on memory writes
           applies Voyage's retrieval-tuned encodings on both sides.
- hash     deterministic offline embeddings — used by tests and as a keyless
           fallback when VOYAGE_API_KEY is missing. NOT semantic (similar
           texts do not map to nearby vectors); never used in production.

All memory callers treat embedding failures as best-effort: a run never
breaks because embeddings are unavailable. The dim guard keeps the vector
store self-consistent — swapping the model or dimension with an existing
store would silently mix incompatible vector spaces, so refusing is cheaper
than silent retrieval corruption. See scripts/migrate_voyage_1024.py for the
one-time re-embedding migration when the dimension changes.
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import httpx

from app.ai.cost import Usage, cost_of
from app.ai.tokens import count_tokens
from app.core.errors import ConfigError
from app.core.settings import get_settings

logger = logging.getLogger(__name__)

MASK64 = (1 << 64) - 1

_VOYAGE_EMBED_CACHE: dict[str, tuple[list[float], int]] | None = None
_VOYAGE_TIMEOUT_S = 10.0
_VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
_EMBED_BUCKET: ContextVar[list[EmbedCall] | None] = ContextVar(
    "embed_bucket", default=None)


@dataclass(frozen=True)
class EmbedCall:
    """One embedding call's inspector telemetry (Voyage or hash mock)."""

    tokens: int
    cost_usd: float
    ms: int
    model: str
    provider: str
    input_type: str
    cached: bool
    chars: int


@contextmanager
def capture_embeddings() -> Iterator[list[EmbedCall]]:
    """Collect embed_text telemetry for the current task (and to_thread copies)."""
    bucket: list[EmbedCall] = []
    token = _EMBED_BUCKET.set(bucket)
    try:
        yield bucket
    finally:
        _EMBED_BUCKET.reset(token)


def _record_call(call: EmbedCall) -> None:
    bucket = _EMBED_BUCKET.get()
    if bucket is not None:
        bucket.append(call)


def _estimate_tokens(text: str) -> int:
    return max(1, count_tokens(text)) if text else 0


def _embed_cost(tokens: int, model: str) -> float:
    return cost_of(Usage(prompt_tokens=tokens, completion_tokens=0), model)


def active_provider() -> str:
    """Resolve the configured provider without exposing credential material."""
    settings = get_settings()
    if settings.embeddings_provider == "auto":
        return "voyage" if settings.voyage_api_key else "hash"
    return settings.embeddings_provider


def embed_text(text: str, *, input_type: str | None = None) -> list[float]:
    """Embed text via Voyage AI (or the hash mock when no key is configured).

    input_type is "query" (memory reads) or "document" (memory writes); the
    hash mock ignores it. The dim guard verifies the returned vector length
    against EMBEDDINGS_DIM before anything is persisted.
    """
    settings = get_settings()
    provider = active_provider()
    started = time.perf_counter()
    cached = False
    tokens = 0
    if provider == "voyage":
        if not settings.voyage_api_key:
            raise ConfigError(
                "EMBEDDINGS_PROVIDER=voyage requires VOYAGE_API_KEY")
        vector, tokens, cached = _embed_voyage(text, input_type)
        model = settings.voyage_model
    else:
        vector = _embed_hash(text)
        tokens = _estimate_tokens(text)
        model = "hash"
    ms = max(0, int((time.perf_counter() - started) * 1000))
    _assert_dimension(vector, text)
    _record_call(EmbedCall(
        tokens=tokens,
        cost_usd=round(_embed_cost(tokens, model), 6),
        ms=ms,
        model=model,
        provider=provider,
        input_type=input_type or "",
        cached=cached,
        chars=len(text),
    ))
    return vector


def clear_voyage_cache() -> None:
    """Drop the per-process response cache (used by tests)."""
    global _VOYAGE_EMBED_CACHE
    _VOYAGE_EMBED_CACHE = None


def _assert_dimension(vector: list[float], text: str) -> None:
    expected = get_settings().embeddings_dim
    if len(vector) != expected:
        raise ConfigError(
            f"embedding dimension mismatch: provider returned {len(vector)}, "
            f"configured EMBEDDINGS_DIM={expected} (from {text[:50]!r})")


def _voyage_cache() -> dict[str, tuple[list[float], int]]:
    global _VOYAGE_EMBED_CACHE
    if _VOYAGE_EMBED_CACHE is None:
        _VOYAGE_EMBED_CACHE = {}
    return _VOYAGE_EMBED_CACHE


def _embed_voyage(text: str, input_type: str | None = None,
                  ) -> tuple[list[float], int, bool]:
    """Voyage AI embeddings call (synchronous httpx POST; memory calls it via
    asyncio.to_thread).

    Cache keys include input_type because the same text embeds differently
    per type under Voyage's retrieval-tuned encodings.
    Returns (vector, tokens, cached).
    """
    cache = _voyage_cache()
    cache_key = f"{input_type or ''}|{text}"
    if cache_key in cache:
        vector, tokens = cache[cache_key]
        return vector, tokens, True
    settings = get_settings()
    try:
        response = httpx.post(
            _VOYAGE_API_URL,
            headers={
                "Authorization": f"Bearer {settings.voyage_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "input": [text],
                "model": settings.voyage_model,
                "input_type": input_type,
                "truncation": True,
            },
            timeout=_VOYAGE_TIMEOUT_S,
        )
        response.raise_for_status()
        body = response.json()
        vector = [float(v) for v in body["data"][0]["embedding"]]
        usage = body.get("usage") if isinstance(body, dict) else None
        tokens = 0
        if isinstance(usage, dict):
            tokens = int(usage.get("total_tokens") or 0)
        if tokens <= 0:
            tokens = _estimate_tokens(text)
    except Exception as exc:  # noqa: BLE001 - converted to a typed config error
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 429:
            logger.warning("voyage rate-limited; using hash fallback for this embed")
            return _embed_hash(text), _estimate_tokens(text), False
        raise ConfigError(
            f"voyage embeddings call failed: {type(exc).__name__}") from exc
    cache[cache_key] = (vector, tokens)
    return vector, tokens, False


def _embed_hash(text: str) -> list[float]:
    """Deterministic offline embedding for tests/keyless fallback (NOT semantic).

    Vector length follows EMBEDDINGS_DIM so the dim guard stays consistent.
    """
    dim = get_settings().embeddings_dim
    seed = int.from_bytes(hashlib.sha256(text.lower().encode()).digest()[:8], "big")
    state: list[int] = [seed or 1]

    def _next() -> float:
        # xorshift64: deterministic pseudo-random in [-1, 1]
        state[0] ^= (state[0] << 13) & MASK64
        state[0] ^= state[0] >> 7
        state[0] ^= (state[0] << 17) & MASK64
        return (state[0] / MASK64) * 2 - 1

    vec = [_next() for _ in range(dim)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]; empty vectors → 0."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def similarity_01(a: list[float], b: list[float]) -> float:
    """Cosine normalized to [0, 1] so weighted sums are comparable."""
    return (cosine(a, b) + 1.0) / 2.0
