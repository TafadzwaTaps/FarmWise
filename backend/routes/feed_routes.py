"""routes/feed_routes.py — Feed purchases + consumption.
Routes: /farms/{farm_id}/feed/purchases, .../consumption, .../cost-summary
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
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
    if data.batch_id and crud.get_batch(farm_id, data.batch_id) is None:
        # AUDIT.md (Phase 8): unlike create_sale/create_expense, this never
        # checked that a client-supplied batch_id actually belongs to this
        # farm before inserting — a feed_consumption row could reference
        # any existing batch_id anywhere (the FK only checks the row
        # exists, not which farm it belongs to), polluting this farm's own
        # feed-cost data with a foreign batch reference. Same principle as
        # every other batch_id check in this codebase: never trust a
        # client-supplied id without verifying it belongs to this farm.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch not found")
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
