"""Shared token counting (tiktoken with char fallback)."""

from __future__ import annotations

_encoding = None


def _get_encoding():  # noqa: ANN202
    global _encoding
    if _encoding is None:
        try:
            import tiktoken

            _encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:  # noqa: BLE001 - offline fallback
            _encoding = False
    return _encoding


def count_tokens(text: str) -> int:
    """Token count with a ~4-chars-per-token fallback when tiktoken is unavailable."""
    encoding = _get_encoding()
    if encoding:
        return len(encoding.encode(text))
    return max(1, len(text) // 4)
