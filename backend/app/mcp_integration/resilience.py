"""Tool-call resilience: timeout, retry with exponential backoff, circuit breaker.

Bonus #1. Applied uniformly to every MCP call by the Manager — agents never
see retry/timeout mechanics.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum

from app.core.errors import MCPCircuitOpenError, MCPConnectionError, MCPError, MCPTimeoutError

logger = logging.getLogger(__name__)


@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_backoff_s: float = 0.4
    max_backoff_s: float = 6.0


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Per-server breaker: trips after N consecutive failures; half-open recovery."""

    failure_threshold: int = 5
    recovery_seconds: float = 30.0
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def _allow_probe(self) -> bool:
        if self.state is CircuitState.CLOSED:
            return True
        if self.state is CircuitState.OPEN:
            if time.monotonic() - self.opened_at >= self.recovery_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True  # half-open: allow one probe

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if (
            self.state is CircuitState.HALF_OPEN
            or self.consecutive_failures >= self.failure_threshold
        ):
            self.state = CircuitState.OPEN
            self.opened_at = time.monotonic()
            logger.warning(
                "circuit OPENED after %s consecutive failures", self.consecutive_failures
            )

    @property
    def is_open(self) -> bool:
        self._allow_probe()
        return self.state is CircuitState.OPEN

    def snapshot(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "seconds_until_probe": max(
                0.0, round(self.recovery_seconds - (time.monotonic() - self.opened_at), 1)
            )
            if self.state is CircuitState.OPEN
            else 0.0,
        }


def classify_error(exc: BaseException) -> MCPError:
    """Map transport-layer exceptions onto the typed MCP error hierarchy."""
    if isinstance(exc, MCPError):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return MCPTimeoutError(f"timeout: {exc}")
    if isinstance(exc, (ConnectionError, OSError)):
        return MCPConnectionError(f"connection: {exc}")
    return MCPError(f"{type(exc).__name__}: {exc}")


async def call_with_resilience(
    operation,  # noqa: ANN001 - zero-arg async callable
    *,
    policy: RetryPolicy,
    breaker: CircuitBreaker,
    timeout_s: float,
) -> object:
    """Run `operation` under timeout + retry + circuit-breaker policy.

    Retries only transient failures (timeouts/connection). Deterministic tool
    errors surfaced in results are NOT retried (they are returned as results).
    Raises the typed MCPError when retries are exhausted or breaker is open.
    """
    if breaker.is_open:
        raise MCPCircuitOpenError("circuit open for tool server; failing fast")

    last_error: MCPError | None = None
    for attempt in range(policy.max_retries + 1):
        try:
            result = await asyncio.wait_for(operation(), timeout=timeout_s)
            breaker.record_success()
            return result
        except Exception as exc:  # noqa: BLE001 - classified below
            last_error = classify_error(exc)
            transient = isinstance(last_error, (MCPTimeoutError, MCPConnectionError))
            breaker.record_failure()
            if breaker.is_open:
                break
            if not transient or attempt >= policy.max_retries:
                break
            backoff = min(policy.max_backoff_s, policy.base_backoff_s * (2**attempt))
            backoff *= 0.5 + random.random() * 0.5  # jitter
            logger.warning(
                "MCP call failed (%s); retry %s/%s in %.2fs",
                last_error.detail,
                attempt + 1,
                policy.max_retries,
                backoff,
            )
            await asyncio.sleep(backoff)
    assert last_error is not None
    raise last_error
