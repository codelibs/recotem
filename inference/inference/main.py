"""FastAPI inference service for Recotem recommendations."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from .config import settings
from .db import SessionLocal
from .hot_swap import start_listener
from .model_loader import get_or_load_model
from .models import TrainedModel
from .rate_limit import limiter
from .routes import health, predict, project

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background services on startup."""
    logger.info("Starting inference service on port %d", settings.inference_port)
    start_listener()
    logger.info("Model event listener started")

    # Pre-load models if specified
    if settings.inference_preload_model_ids:
        ids = [
            int(x.strip())
            for x in settings.inference_preload_model_ids.split(",")
            if x.strip()
        ]
        db = SessionLocal()
        try:
            for model_id in ids:
                model = (
                    db.query(TrainedModel).filter(TrainedModel.id == model_id).first()
                )
                if model and model.file:
                    try:
                        get_or_load_model(model_id, model.file)
                        logger.info("Pre-loaded model %d", model_id)
                    except Exception:
                        logger.exception("Failed to pre-load model %d", model_id)
                else:
                    logger.warning("Model %d not found or has no file", model_id)
        finally:
            db.close()

    yield
    logger.info("Shutting down inference service")


app = FastAPI(
    title="Recotem Inference API",
    version="2.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(health.router, tags=["Health"])
app.include_router(predict.router, tags=["Prediction"])
app.include_router(project.router, tags=["Project Prediction"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.inference_port)
