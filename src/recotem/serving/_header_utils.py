"""Helpers for extracting and normalising values from artifact header dicts."""

from __future__ import annotations

from typing import Any


def extract_algorithms(header_dict: dict[str, Any]) -> list[str]:
    """Return the algorithm list from *header_dict*.

    Prefers ``header_dict["algorithms"]``; falls back to
    ``header_dict["tuning"]["tried_algorithms"]`` when the primary key is
    absent or empty.  Returns an empty list when neither is available.
    """
    algorithms = header_dict.get("algorithms")
    if algorithms:
        return list(algorithms)
    tuning = header_dict.get("tuning") or {}
    tried = tuning.get("tried_algorithms")
    if tried:
        return list(tried)
    return []


def normalize_config_digest(raw: str | None) -> str | None:
    """Normalize a raw config_digest value to ``sha256:<hex>`` or ``None``.

    - Empty string or ``None`` → ``None``
    - Already prefixed with ``sha256:`` → returned as-is
    - Bare hex string without prefix → ``sha256:<raw>`` added
    """
    if not raw:
        return None
    if raw.startswith("sha256:"):
        return raw
    return f"sha256:{raw}"
