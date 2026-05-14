"""Tests for RECOTEM_MAX_DOWNLOAD_BYTES and RECOTEM_HTTP_TIMEOUT_SECONDS."""

from __future__ import annotations

import pytest

from recotem.config import (
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_MAX_DOWNLOAD_BYTES,
    get_http_timeout_seconds,
    get_max_download_bytes,
)


def test_max_download_bytes_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RECOTEM_MAX_DOWNLOAD_BYTES", raising=False)
    assert get_max_download_bytes() == DEFAULT_MAX_DOWNLOAD_BYTES
    assert DEFAULT_MAX_DOWNLOAD_BYTES == 256 * 1024 * 1024


def test_max_download_bytes_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_DOWNLOAD_BYTES", str(1024 * 1024))  # 1 MiB
    assert get_max_download_bytes() == 1024 * 1024


def test_max_download_bytes_below_min_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_DOWNLOAD_BYTES", "0")
    # Clamp to 1 MiB minimum
    assert get_max_download_bytes() == 1024 * 1024


def test_max_download_bytes_above_max_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_DOWNLOAD_BYTES", str(64 * 1024 * 1024 * 1024))
    # Clamp to 16 GiB maximum
    assert get_max_download_bytes() == 16 * 1024 * 1024 * 1024


def test_max_download_bytes_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_DOWNLOAD_BYTES", "not-a-number")
    assert get_max_download_bytes() == DEFAULT_MAX_DOWNLOAD_BYTES


def test_http_timeout_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RECOTEM_HTTP_TIMEOUT_SECONDS", raising=False)
    assert get_http_timeout_seconds() == DEFAULT_HTTP_TIMEOUT_SECONDS
    assert DEFAULT_HTTP_TIMEOUT_SECONDS == 30


def test_http_timeout_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_HTTP_TIMEOUT_SECONDS", "60")
    assert get_http_timeout_seconds() == 60


def test_http_timeout_below_min_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_HTTP_TIMEOUT_SECONDS", "0")
    assert get_http_timeout_seconds() == 1


def test_http_timeout_above_max_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_HTTP_TIMEOUT_SECONDS", "9999")
    assert get_http_timeout_seconds() == 600


def test_http_timeout_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_HTTP_TIMEOUT_SECONDS", "abc")
    assert get_http_timeout_seconds() == DEFAULT_HTTP_TIMEOUT_SECONDS
