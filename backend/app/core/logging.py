"""Structured logging with run/thread correlation context.

Correlation IDs (`run_id`, `thread_id`) are injected into every log line via a
contextvar-based filter. Field-style secrets (`api_key=`, `authorization=`)
are redacted. Keys are not matched by prefix — they may start with anything.
"""

import contextvars
import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
thread_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("thread_id", default=None)

_REDACT_PATTERNS = re.compile(
    r"(api[_-]?key|authorization|token|secret|password)[\"']?\s*[:=]\s*[\"']?[^\"'\s,}]+",
    re.IGNORECASE,
)


class CorrelationFilter(logging.Filter):
    """Injects run_id / thread_id contextvars and redacts secret-looking values."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.run_id = run_id_var.get()
        record.thread_id = thread_id_var.get()
        if isinstance(record.msg, str):
            record.msg = _redact(record.msg)
        if isinstance(record.args, dict):
            record.args = {k: _redact(v) for k, v in record.args.items()}
        elif isinstance(record.args, tuple):
            record.args = tuple(_redact(value) for value in record.args)
        return True


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _REDACT_PATTERNS.sub(r"\1=***", value)
    return value


class JsonFormatter(logging.Formatter):
    """Minimal JSON formatter (structured logs without external deps)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in ("run_id", "thread_id"):
            if getattr(record, field, None):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    """Idempotent root-logger configuration for the whole process.

    Uvicorn installs handlers on ``uvicorn.*`` loggers before lifespan runs.
    Clearing only the root logger leaves those detached, so access/app logs
    vanish in the terminal (especially with ``--reload`` on Windows). Route
    everything through one stdout handler instead.
    """
    log_level = level.upper()
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except Exception:  # noqa: BLE001 - best-effort; some terminals reject this
            pass

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(CorrelationFilter())
    handler.setFormatter(JsonFormatter() if json_logs else logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] run=%(run_id)s thread=%(thread_id)s %(message)s"
    ))

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()
    root.addHandler(handler)

    # Fold uvicorn (incl. access) and app loggers onto the same handler.
    for name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "uvicorn.asgi",
        "fastapi",
        "alembic",
    ):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
        lg.setLevel(log_level)

    for noisy in ("httpx", "httpcore", "openai", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def bind_run_context(run_id: str | None = None, thread_id: str | None = None) -> None:
    """Set correlation IDs for the current async context (used by the executor)."""
    if run_id is not None:
        run_id_var.set(run_id)
    if thread_id is not None:
        thread_id_var.set(thread_id)
