"""Background impression recording for A/B test tracking."""

import logging
import uuid
from datetime import UTC, datetime

from .config import settings
from .db import SessionLocal
from .models import ConversionEvent

logger = logging.getLogger(__name__)


def record_impression(
    *,
    project_id: int,
    deployment_slot_id: int,
    user_id: str,
    request_id: str,
) -> None:
    """Record an impression event in the background.

    Failures are logged but never propagated to avoid impacting the
    response path.
    """
    if not settings.inference_auto_record_impressions:
        return

    db = SessionLocal()
    try:
        event = ConversionEvent(
            project_id=project_id,
            deployment_slot_id=deployment_slot_id,
            user_id=user_id,
            item_id="",
            event_type="impression",
            recommendation_request_id=uuid.UUID(request_id),
            timestamp=datetime.now(UTC),
            metadata_json={"source": "inference_auto"},
        )
        db.add(event)
        db.commit()
    except Exception:
        logger.exception("Failed to record impression for request %s", request_id)
        db.rollback()
    finally:
        db.close()
