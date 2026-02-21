"""Tests for automatic impression recording."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def _enable_impressions():
    """Ensure auto-record is enabled."""
    with patch("inference.impressions.settings") as mock_settings:
        mock_settings.inference_auto_record_impressions = True
        yield mock_settings


@pytest.fixture
def _disable_impressions():
    """Ensure auto-record is disabled."""
    with patch("inference.impressions.settings") as mock_settings:
        mock_settings.inference_auto_record_impressions = False
        yield mock_settings


class TestRecordImpression:
    @pytest.mark.usefixtures("_enable_impressions")
    def test_commits_event_when_enabled(self):
        mock_session = MagicMock()
        with patch("inference.impressions.SessionLocal", return_value=mock_session):
            from inference.impressions import record_impression

            record_impression(
                project_id=1,
                deployment_slot_id=2,
                user_id="user_42",
                request_id="550e8400-e29b-41d4-a716-446655440000",
            )

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()

        event = mock_session.add.call_args[0][0]
        assert event.project_id == 1
        assert event.deployment_slot_id == 2
        assert event.user_id == "user_42"
        assert event.event_type == "impression"
        assert event.metadata_json == {"source": "inference_auto"}

    @pytest.mark.usefixtures("_disable_impressions")
    def test_skips_when_disabled(self):
        mock_session = MagicMock()
        with patch("inference.impressions.SessionLocal", return_value=mock_session):
            from inference.impressions import record_impression

            record_impression(
                project_id=1,
                deployment_slot_id=2,
                user_id="user_42",
                request_id="550e8400-e29b-41d4-a716-446655440000",
            )

        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_called()
        mock_session.close.assert_not_called()

    @pytest.mark.usefixtures("_enable_impressions")
    def test_does_not_propagate_db_error(self):
        mock_session = MagicMock()
        mock_session.commit.side_effect = RuntimeError("DB connection lost")
        with patch("inference.impressions.SessionLocal", return_value=mock_session):
            from inference.impressions import record_impression

            # Must not raise
            record_impression(
                project_id=1,
                deployment_slot_id=2,
                user_id="user_42",
                request_id="550e8400-e29b-41d4-a716-446655440000",
            )

        mock_session.rollback.assert_called_once()
        mock_session.close.assert_called_once()

    @pytest.mark.usefixtures("_enable_impressions")
    def test_session_always_closed_on_error(self):
        mock_session = MagicMock()
        mock_session.add.side_effect = Exception("unexpected")
        with patch("inference.impressions.SessionLocal", return_value=mock_session):
            from inference.impressions import record_impression

            record_impression(
                project_id=1,
                deployment_slot_id=2,
                user_id="user_42",
                request_id="550e8400-e29b-41d4-a716-446655440000",
            )

        mock_session.close.assert_called_once()
