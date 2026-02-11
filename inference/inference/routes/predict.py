"""Prediction endpoints."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import ApiKey, require_scope
from ..db import get_db
from ..model_loader import get_or_load_model
from ..models import TrainedModel

logger = logging.getLogger(__name__)

router = APIRouter()


class PredictRequest(BaseModel):
    user_id: str
    cutoff: int = Field(default=10, ge=1, le=1000)


class PredictBatchRequest(BaseModel):
    user_ids: list[str]
    cutoff: int = Field(default=10, ge=1, le=1000)


class RecommendationItem(BaseModel):
    item_id: str
    score: float


class PredictResponse(BaseModel):
    items: list[RecommendationItem]
    model_id: int
    request_id: str


class PredictBatchResponse(BaseModel):
    results: list[PredictResponse]


@router.post("/predict/{model_id}", response_model=PredictResponse)
def predict(
    model_id: int,
    body: PredictRequest,
    api_key: ApiKey = Depends(require_scope("predict")),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    """Get top-K recommendations for a single user."""
    model_record = db.query(TrainedModel).filter(TrainedModel.id == model_id).first()
    if model_record is None:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found")

    try:
        rec = get_or_load_model(model_id, model_record.file)
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

    return PredictResponse(items=items, model_id=model_id, request_id=request_id)


@router.post("/predict/{model_id}/batch", response_model=PredictBatchResponse)
def predict_batch(
    model_id: int,
    body: PredictBatchRequest,
    api_key: ApiKey = Depends(require_scope("predict")),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    """Get top-K recommendations for multiple users."""
    if len(body.user_ids) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 users per batch")

    model_record = db.query(TrainedModel).filter(TrainedModel.id == model_id).first()
    if model_record is None:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found")

    try:
        rec = get_or_load_model(model_id, model_record.file)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to load model: {e}") from e

    results = []
    for user_id in body.user_ids:
        request_id = str(uuid.uuid4())
        try:
            recs = rec.get_recommendation_for_known_user_id(user_id, body.cutoff)
            items = [RecommendationItem(item_id=iid, score=s) for iid, s in recs]
        except KeyError:
            items = []
        results.append(
            PredictResponse(items=items, model_id=model_id, request_id=request_id)
        )

    return PredictBatchResponse(results=results)
