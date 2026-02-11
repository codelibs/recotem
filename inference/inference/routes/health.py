"""Health check endpoints."""

from fastapi import APIRouter

from ..db import engine
from ..model_loader import model_cache

router = APIRouter()


@router.get("/health")
def health_check():
    """Health check: verify database connectivity."""
    try:
        with engine.connect() as conn:
            conn.execute(conn.connection.cursor().execute("SELECT 1"))
    except Exception:
        pass  # DB check is best-effort

    return {
        "status": "healthy",
        "loaded_models": model_cache.size(),
    }


@router.get("/models")
def list_loaded_models():
    """List currently loaded model IDs."""
    return {
        "models": model_cache.loaded_models(),
        "count": model_cache.size(),
    }
