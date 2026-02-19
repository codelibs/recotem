"""Project-level prediction with deployment slot routing."""

import logging
import random
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import ApiKey, require_scope
from ..config import settings
from ..db import get_db
from ..model_loader import get_or_load_model
from ..models import DeploymentSlot, TrainedModel
from ..rate_limit import limiter
from .predict import RecommendationItem

logger = logging.getLogger(__name__)

router = APIRouter()


class ProjectPredictRequest(BaseModel):
    user_id: str
    cutoff: int = Field(default=10, ge=1, le=1000)


class ProjectPredictResponse(BaseModel):
    items: list[RecommendationItem]
    model_id: int
    slot_id: int
    slot_name: str
    request_id: str


def _select_slot_by_weight(slots: list[DeploymentSlot]) -> DeploymentSlot:
    """Weighted random selection among active deployment slots."""
    weights = [s.weight for s in slots]
    return random.choices(slots, weights=weights, k=1)[0]


@router.post("/predict/project/{project_id}", response_model=ProjectPredictResponse)
@limiter.limit(settings.inference_rate_limit)
def predict_by_project(
    request: Request,
    project_id: int,
    body: ProjectPredictRequest,
    api_key: ApiKey = Depends(require_scope("predict")),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    """Get recommendations via project's deployment slots (A/B routing)."""
    # Verify API key has access to this project
    if api_key.project_id != project_id:
        raise HTTPException(
            status_code=403, detail="API key not authorized for this project"
        )

    slots = (
        db.query(DeploymentSlot)
        .filter(
            DeploymentSlot.project_id == project_id,
            DeploymentSlot.is_active.is_(True),
        )
        .all()
    )

    if not slots:
        raise HTTPException(
            status_code=404, detail="No active deployment slots for project"
        )

    selected_slot = _select_slot_by_weight(slots)

    model_record = (
        db.query(TrainedModel)
        .filter(TrainedModel.id == selected_slot.trained_model_id)
        .first()
    )
    if model_record is None:
        raise HTTPException(status_code=500, detail="Deployment slot model not found")

    try:
        rec = get_or_load_model(model_record.id, model_record.file)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to load model: {e}") from e

    request_id = str(uuid.uuid4())
    try:
        results = rec.get_recommendation_for_known_user_id(body.user_id, body.cutoff)
        items = [
            RecommendationItem(item_id=item_id, score=score)
            for item_id, score in results
        ]
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"User '{body.user_id}' not found in model"
        ) from None

    return ProjectPredictResponse(
        items=items,
        model_id=model_record.id,
        slot_id=selected_slot.id,
        slot_name=selected_slot.name,
        request_id=request_id,
    )
