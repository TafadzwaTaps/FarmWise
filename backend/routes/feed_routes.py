"""routes/feed_routes.py — Feed purchases + consumption.
Routes: /farms/{farm_id}/feed/purchases, .../consumption, .../cost-summary
"""

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

import crud
from core.auth import require_farm_role

router = APIRouter(prefix="/farms/{farm_id}/feed", tags=["Feed"])

_MANAGE_ROLES = ("farmer", "farm_manager")
_RECORD_ROLES = ("farmer", "farm_manager", "worker")


class FeedPurchaseCreate(BaseModel):
    feed_type: str = Field(min_length=1, max_length=100)
    supplier: str | None = None
    quantity_kg: float = Field(gt=0)
    unit_cost: float = Field(ge=0)
    purchase_date: date
    notes: str | None = None


class FeedConsumptionCreate(BaseModel):
    batch_id: str | None = None
    feed_type: str = Field(min_length=1, max_length=100)
    quantity_kg: float = Field(gt=0)
    date: date
    notes: str | None = None


@router.post("/purchases", status_code=201)
def create_feed_purchase(farm_id: str, data: FeedPurchaseCreate, _member: dict = Depends(require_farm_role(*_MANAGE_ROLES))):
    payload = data.model_dump()
    payload["purchase_date"] = payload["purchase_date"].isoformat()
    return crud.create_feed_purchase(farm_id, payload)


@router.get("/purchases")
def list_feed_purchases(farm_id: str, _member: dict = Depends(require_farm_role())):
    return crud.list_feed_purchases(farm_id)


@router.post("/consumption", status_code=201)
def record_feed_consumption(farm_id: str, data: FeedConsumptionCreate, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    payload = data.model_dump()
    payload["date"] = payload["date"].isoformat()
    return crud.create_feed_consumption(farm_id, payload)


@router.get("/consumption")
def list_feed_consumption(farm_id: str, batch_id: str | None = None, _member: dict = Depends(require_farm_role())):
    return crud.list_feed_consumption(farm_id, batch_id)


@router.get("/cost-summary")
def feed_cost_summary(farm_id: str, batch_id: str | None = None, _member: dict = Depends(require_farm_role())):
    """Cost per animal / per batch / per kg — feeds the dashboard and AI assistant."""
    return crud.feed_cost_summary(farm_id, batch_id)
