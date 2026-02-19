"""Shared fixtures for inference service tests."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_db_session():
    """Return a MagicMock SQLAlchemy session."""
    return MagicMock()
