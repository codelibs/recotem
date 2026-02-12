"""Service for syncing retraining schedules with django-celery-beat."""

import json
import logging

from django_celery_beat.models import CrontabSchedule, PeriodicTask

logger = logging.getLogger(__name__)

TASK_NAME_PREFIX = "recotem_retrain_schedule_"


def _parse_cron(expression: str) -> dict:
    """Parse a cron expression into CrontabSchedule fields."""
    parts = expression.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression: {expression}")
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day_of_month": parts[2],
        "month_of_year": parts[3],
        "day_of_week": parts[4],
    }


def sync_schedule_to_beat(schedule) -> None:
    """Create, update, or delete the PeriodicTask for a RetrainingSchedule."""
    task_name = f"{TASK_NAME_PREFIX}{schedule.id}"

    if not schedule.is_enabled:
        PeriodicTask.objects.filter(name=task_name).delete()
        logger.info("Deleted periodic task for schedule %d", schedule.id)
        return

    cron_fields = _parse_cron(schedule.cron_expression)
    crontab, _ = CrontabSchedule.objects.get_or_create(**cron_fields)

    PeriodicTask.objects.update_or_create(
        name=task_name,
        defaults={
            "task": "recotem.api.tasks.task_scheduled_retrain",
            "crontab": crontab,
            "args": json.dumps([schedule.id]),
            "enabled": True,
        },
    )
    logger.info("Synced periodic task for schedule %d", schedule.id)


def delete_beat_task(schedule) -> None:
    """Delete the PeriodicTask for a schedule, regardless of is_enabled."""
    task_name = f"{TASK_NAME_PREFIX}{schedule.id}"
    PeriodicTask.objects.filter(name=task_name).delete()
    logger.info(
        "Deleted periodic task for schedule %d (schedule destroyed)", schedule.id
    )
