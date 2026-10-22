"""CORS origin allowlist and guard middleware."""

from __future__ import annotations

import pytest
from app.core.cors import (
    normalize_cors_origins_csv,
    normalize_origin,
    origin_is_allowed,
    parse_cors_origins,
)
from app.core.settings import get_settings


def test_normalize_origin_canonicalizes_host_and_port() -> None:
    assert normalize_origin("http://LocalHost:5173/") == "http://localhost:5173"
    assert normalize_origin("https://app.example.com") == "https://app.example.com"


def test_normalize_origin_rejects_wildcard_and_paths() -> None:
    with pytest.raises(ValueError, match="wildcard"):
        normalize_origin("*")
    with pytest.raises(ValueError, match="path"):
        normalize_origin("http://localhost:5173/app")


def test_parse_cors_origins_deduplicates() -> None:
    raw = "http://localhost:5173, http://LOCALHOST:5173,http://127.0.0.1:5173"
    assert parse_cors_origins(raw) == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def test_settings_validator_rejects_invalid_origin(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "not-a-url")
    get_settings.cache_clear()
    with pytest.raises(ValueError):
        get_settings()


def test_settings_validator_normalizes_csv(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "HTTP://LOCALHOST:5173/, http://127.0.0.1:5173")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.cors_origins == "http://localhost:5173,http://127.0.0.1:5173"


def test_origin_is_allowed() -> None:
    allowed = frozenset(parse_cors_origins("http://localhost:5173"))
    assert origin_is_allowed("http://localhost:5173", allowed)
    assert not origin_is_allowed("http://evil.example", allowed)


async def test_cors_allows_configured_origin_preflight(monkeypatch) -> None:
    import httpx
    from app.main import create_app

    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
    get_settings.cache_clear()
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/api/health/live",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "POST" in (response.headers.get("access-control-allow-methods") or "")


async def test_cors_blocks_disallowed_origin(monkeypatch) -> None:
    import httpx
    from app.main import create_app

    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
    get_settings.cache_clear()
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/health/live",
            headers={"Origin": "http://evil.example"},
        )
    assert response.status_code == 403
    assert response.json()["error"] == "Origin not allowed."


async def test_requests_without_origin_header_pass_through(monkeypatch) -> None:
    import httpx
    from app.main import create_app

    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
    get_settings.cache_clear()
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health/live")
    assert response.status_code == 200
