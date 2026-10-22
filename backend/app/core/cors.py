"""CORS — explicit origin allowlist and preflight hardening.

Cross-origin browser access is opt-in via ``CORS_ORIGINS``. Wildcards are
rejected; only ``http``/``https`` origins with host (no path) are accepted.
Requests that include a disallowed ``Origin`` header are rejected with 403
before route handlers run.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.settings import Settings

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_CORS_ALLOW_METHODS = ["GET", "POST", "DELETE", "OPTIONS"]
_CORS_ALLOW_HEADERS = ["Accept", "Content-Type", "X-API-Key", "Authorization"]


def normalize_origin(origin: str) -> str:
    """Return a canonical browser origin or raise ``ValueError``."""
    text = origin.strip()
    if not text:
        raise ValueError("CORS origin must not be empty")
    if text == "*":
        raise ValueError("wildcard '*' is not permitted in CORS_ORIGINS")
    parsed = urlparse(text)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"CORS origin must use http or https: {origin!r}")
    if not parsed.hostname:
        raise ValueError(f"CORS origin must include a host: {origin!r}")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError(f"CORS origin must not include query or fragment: {origin!r}")
    if parsed.path not in ("", "/"):
        raise ValueError(f"CORS origin must not include a path: {origin!r}")

    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    port = parsed.port
    default_port = 443 if scheme == "https" else 80
    if port is not None and port != default_port:
        netloc = f"{host}:{port}"
    else:
        netloc = host
    return f"{scheme}://{netloc}"


def parse_cors_origins(raw: str) -> list[str]:
    """Parse comma-separated ``CORS_ORIGINS`` into a deduplicated allowlist."""
    if not raw or not raw.strip():
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        normalized = normalize_origin(piece)
        if normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def normalize_cors_origins_csv(raw: str) -> str:
    """Validate and canonicalize a CSV origin list (settings validator hook)."""
    origins = parse_cors_origins(raw)
    return ",".join(origins)


def origin_is_allowed(origin: str, allowed: frozenset[str]) -> bool:
    if not allowed:
        return False
    try:
        return normalize_origin(origin) in allowed
    except ValueError:
        return False


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """Reject browser requests whose ``Origin`` is not on the allowlist."""

    def __init__(self, app, *, allowed_origins: frozenset[str]) -> None:  # noqa: ANN001
        super().__init__(app)
        self._allowed_origins = allowed_origins

    async def dispatch(self, request: Request, call_next) -> Response:  # noqa: ANN001
        origin = request.headers.get("origin")
        if origin is not None and not origin_is_allowed(origin, self._allowed_origins):
            logger.warning(
                "blocked cross-origin request origin=%s path=%s",
                origin, request.url.path,
            )
            return JSONResponse(
                status_code=403,
                content={"error": "Origin not allowed."},
            )
        return await call_next(request)


def configure_cors(app: FastAPI, settings: Settings) -> None:
    """Attach CORS + origin guard middleware (outermost = added last)."""
    origins = parse_cors_origins(settings.cors_origins)
    allowed = frozenset(origins)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed),
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=_CORS_ALLOW_METHODS,
        allow_headers=_CORS_ALLOW_HEADERS,
        max_age=settings.cors_max_age,
    )
    app.add_middleware(OriginGuardMiddleware, allowed_origins=allowed)

    if allowed:
        logger.info("CORS allowlist: %s", ", ".join(sorted(allowed)))
    else:
        logger.info("CORS allowlist empty — cross-origin browser access disabled")
