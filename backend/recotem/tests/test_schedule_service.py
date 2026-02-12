"""Tests for schedule_service (sync with django-celery-beat)."""

import pytest
from unittest.mock import MagicMock, patch

from recotem.api.services.schedule_service import _parse_cron, sync_schedule_to_beat


class TestParseCron:
    def test_valid_cron(self):
        result = _parse_cron("0 2 * * 0")
        assert result == {
            "minute": "0",
            "hour": "2",
            "day_of_month": "*",
            "month_of_year": "*",
            "day_of_week": "0",
        }

    def test_every_minute(self):
        result = _parse_cron("* * * * *")
        assert result["minute"] == "*"
        assert result["hour"] == "*"

    def test_invalid_cron_too_few_fields(self):
        with pytest.raises(ValueError, match="Invalid cron"):
            _parse_cron("0 2 *")

    def test_invalid_cron_too_many_fields(self):
        with pytest.raises(ValueError, match="Invalid cron"):
            _parse_cron("0 2 * * * *")

    def test_whitespace_stripped(self):
        result = _parse_cron("  0 3 * * 1  ")
        assert result["minute"] == "0"
        assert result["hour"] == "3"


@pytest.mark.django_db
class TestSyncScheduleToBeat:
    @patch("recotem.api.services.schedule_service.PeriodicTask")
    @patch("recotem.api.services.schedule_service.CrontabSchedule")
    def test_disabled_schedule_deletes_task(self, mock_crontab, mock_periodic):
        schedule = MagicMock()
        schedule.id = 1
        schedule.is_enabled = False

        sync_schedule_to_beat(schedule)

        mock_periodic.objects.filter.assert_called_once()
        mock_periodic.objects.filter.return_value.delete.assert_called_once()

    @patch("recotem.api.services.schedule_service.PeriodicTask")
    @patch("recotem.api.services.schedule_service.CrontabSchedule")
    def test_enabled_schedule_creates_task(self, mock_crontab, mock_periodic):
        schedule = MagicMock()
        schedule.id = 42
        schedule.is_enabled = True
        schedule.cron_expression = "30 4 * * 2"

        mock_crontab.objects.get_or_create.return_value = (MagicMock(), True)

        sync_schedule_to_beat(schedule)

        mock_crontab.objects.get_or_create.assert_called_once_with(
            minute="30", hour="4", day_of_month="*", month_of_year="*", day_of_week="2"
        )
        mock_periodic.objects.update_or_create.assert_called_once()
        call_kwargs = mock_periodic.objects.update_or_create.call_args
        assert "recotem_retrain_schedule_42" in str(call_kwargs)
