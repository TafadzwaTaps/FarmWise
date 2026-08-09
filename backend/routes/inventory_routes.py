"""routes/inventory_routes.py — Inventory items.
Routes: /farms/{farm_id}/inventory, .../{item_id}, .../{item_id}/adjust
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

import crud
from core.auth import require_farm_role

router = APIRouter(prefix="/farms/{farm_id}/inventory", tags=["Inventory"])

_MANAGE_ROLES = ("farmer", "farm_manager")
_ADJUST_ROLES = ("farmer", "farm_manager", "worker")

INVENTORY_CATEGORIES = {"feed", "medicine", "equipment", "tools", "packaging"}


class InventoryItemCreate(BaseModel):
    category: str
    name: str = Field(min_length=1, max_length=150)
    unit: str = Field(min_length=1, max_length=20)
    quantity_on_hand: float = Field(default=0, ge=0)
    low_stock_threshold: float = Field(default=0, ge=0)
    unit_cost: float | None = None


class InventoryItemUpdate(BaseModel):
    name: str | None = None
    unit: str | None = None
    low_stock_threshold: float | None = None
    unit_cost: float | None = None


class InventoryAdjustment(BaseModel):
    """Positive delta = stock in, negative = stock out."""
    delta: float
    reason: str | None = None


def _get_item_or_404(farm_id: str, item_id: str) -> dict:
    item = crud.get_item(farm_id, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inventory item not found")
    return item


@router.post("", status_code=status.HTTP_201_CREATED)
def create_item(farm_id: str, data: InventoryItemCreate, _member: dict = Depends(require_farm_role(*_MANAGE_ROLES))):
    return crud.create_item(farm_id, data.model_dump())


@router.get("")
def list_items(farm_id: str, low_stock_only: bool = False, _member: dict = Depends(require_farm_role())):
    return crud.list_items(farm_id, low_stock_only)


@router.patch("/{item_id}")
def update_item(farm_id: str, item_id: str, data: InventoryItemUpdate, _member: dict = Depends(require_farm_role(*_MANAGE_ROLES))):
    _get_item_or_404(farm_id, item_id)
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    return crud.update_item(item_id, fields)


@router.post("/{item_id}/adjust")
def adjust_stock(farm_id: str, item_id: str, data: InventoryAdjustment, _member: dict = Depends(require_farm_role(*_ADJUST_ROLES))):
    """Delta-based stock in/out — see InventoryAdjustment for why it's a delta, not a set."""
    item = _get_item_or_404(farm_id, item_id)
    if float(item["quantity_on_hand"]) + data.delta < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Adjustment would drop stock below zero")
    return crud.adjust_stock(item, data.delta)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(farm_id: str, item_id: str, _member: dict = Depends(require_farm_role(*_MANAGE_ROLES))):
    _get_item_or_404(farm_id, item_id)
    crud.delete_item(item_id)
