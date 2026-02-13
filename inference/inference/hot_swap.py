"""Redis Pub/Sub listener for model hot-swapping."""

import json
import logging
import threading

import redis

from .config import settings
from .db import SessionLocal
from .model_loader import get_or_load_model
from .models import TrainedModel

logger = logging.getLogger(__name__)


def _handle_model_event(message: dict) -> None:
    """Process a model_trained event."""
    try:
        data = json.loads(message["data"])
        if data.get("event") != "model_trained":
            return

        model_id = data["model_id"]
        logger.info("Received model_trained event for model %d", model_id)

        db = SessionLocal()
        try:
            model = db.query(TrainedModel).filter(TrainedModel.id == model_id).first()
            if model is None:
                logger.warning("Model %d not found in database", model_id)
                return
            if not model.file:
                logger.warning("Model %d has no file", model_id)
                return

            # Load model in background to avoid blocking the listener
            get_or_load_model(model_id, model.file)
            logger.info("Hot-swapped model %d", model_id)
        finally:
            db.close()

    except Exception:
        logger.exception("Failed to handle model event")


def start_listener() -> threading.Thread:
    """Start the Redis Pub/Sub listener in a background thread."""

    def _listen():
        while True:
            try:
                r = redis.from_url(settings.model_events_redis_url)
                pubsub = r.pubsub()
                pubsub.subscribe("recotem:model_events")
                logger.info("Subscribed to recotem:model_events")

                for message in pubsub.listen():
                    if message["type"] == "message":
                        _handle_model_event(message)
            except redis.ConnectionError:
                logger.warning("Redis connection lost, reconnecting in 5s...")
                import time

                time.sleep(5)
            except Exception:
                logger.exception("Unexpected error in model event listener")
                import time

                time.sleep(5)

    thread = threading.Thread(target=_listen, daemon=True, name="model-event-listener")
    thread.start()
    return thread
