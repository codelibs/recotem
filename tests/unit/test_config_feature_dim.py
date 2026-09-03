"""Tests for RECOTEM_MAX_FEATURE_DIM (recotem._features's dimension cap)."""

from __future__ import annotations

import pytest

from recotem.config import DEFAULT_MAX_FEATURE_DIM, get_max_feature_dim


def test_max_feature_dim_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RECOTEM_MAX_FEATURE_DIM", raising=False)
    assert get_max_feature_dim() == DEFAULT_MAX_FEATURE_DIM
    assert DEFAULT_MAX_FEATURE_DIM == 5000


def test_max_feature_dim_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_FEATURE_DIM", "1234")
    assert get_max_feature_dim() == 1234


def test_max_feature_dim_below_min_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_FEATURE_DIM", "0")
    assert get_max_feature_dim() == 16


def test_max_feature_dim_above_max_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_FEATURE_DIM", str(10_000_000))
    assert get_max_feature_dim() == 100_000


def test_max_feature_dim_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOTEM_MAX_FEATURE_DIM", "not-a-number")
    assert get_max_feature_dim() == DEFAULT_MAX_FEATURE_DIM
