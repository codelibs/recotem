"""Tests for RECOTEM_MAX_BODY_BYTES (serve-side request body cap)."""

from __future__ import annotations

import pytest

from recotem.config import DEFAULT_MAX_BODY_BYTES, get_max_body_bytes


def test_max_body_bytes_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RECOTEM_MAX_BODY_BYTES", raising=False)
    assert get_max_body_bytes() == DEFAULT_MAX_BODY_BYTES
    assert DEFAULT_MAX_BODY_BYTES == 128 * 1024 * 1024


def test_max_body_bytes_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_BODY_BYTES", str(4 * 1024 * 1024))
    assert get_max_body_bytes() == 4 * 1024 * 1024


def test_max_body_bytes_below_min_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_BODY_BYTES", "0")
    # Clamp to 1 MiB minimum
    assert get_max_body_bytes() == 1024 * 1024


def test_max_body_bytes_above_max_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_BODY_BYTES", str(64 * 1024 * 1024 * 1024))
    # Clamp to 2 GiB maximum
    assert get_max_body_bytes() == 2 * 1024 * 1024 * 1024


def test_max_body_bytes_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_BODY_BYTES", "not-a-number")
    assert get_max_body_bytes() == DEFAULT_MAX_BODY_BYTES
