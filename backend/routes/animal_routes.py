"""routes/animal_routes.py — Animal batches, mortality, medication.
Routes: /farms/{farm_id}/animals/batches, .../mortality, .../medication
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

import crud
from core.auth import require_farm_role
from routes._deps import log

router = APIRouter(prefix="/farms/{farm_id}/animals", tags=["Animals"])

# Workers can log day-to-day events (mortality, medication) but not create/delete batches.
_MANAGE_ROLES = ("farmer", "farm_manager")
_RECORD_ROLES = ("farmer", "farm_manager", "worker")
_FINANCE_VIEW_ROLES = ("farmer", "farm_manager", "accountant")  # matches finance_routes.py — batch cost/profit is financial data, not a worker-facing view

Species = Literal[
    "chicken_layer", "chicken_broiler", "pig", "cattle", "goat", "sheep",
    "rabbit", "fish", "turkey", "duck", "bee", "other",
]
MedicationType = Literal["vaccine", "medicine", "deworming", "treatment"]

# Kept for anything that still wants the plain set (e.g. tests, docs).
VALID_SPECIES = set(Species.__args__)


class AnimalBatchCreate(BaseModel):
    batch_name: str = Field(min_length=1, max_length=150)
    species: Species
    breed: str | None = None
    quantity_initial: int = Field(gt=0)
    purchase_date: date | None = None
    purchase_price_total: float | None = None
    supplier: str | None = None
    average_weight_kg: float | None = None
    expected_selling_date: date | None = None
    notes: str | None = None


class MortalityCreate(BaseModel):
    date: date
    quantity: int = Field(gt=0)
    cause: str | None = None
    notes: str | None = None


class MedicationCreate(BaseModel):
    type: MedicationType
    name: str
    date_administered: date | None = None
    next_due_date: date | None = None
    dosage: str | None = None
    administered_by: str | None = None
    cost: float | None = Field(default=None, ge=0)
    notes: str | None = None


def _get_batch_or_404(farm_id: str, batch_id: str) -> dict:
    batch = crud.get_batch(farm_id, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Animal batch not found")
    return batch


@router.post("/batches", status_code=status.HTTP_201_CREATED)
def create_batch(farm_id: str, data: AnimalBatchCreate, _member: dict = Depends(require_farm_role(*_MANAGE_ROLES))):
    payload = data.model_dump()
    payload["purchase_date"] = payload["purchase_date"].isoformat() if payload["purchase_date"] else None
    payload["expected_selling_date"] = payload["expected_selling_date"].isoformat() if payload["expected_selling_date"] else None
    return crud.create_batch(farm_id, payload)


@router.get("/batches")
def list_batches(farm_id: str, status_filter: str | None = None, _member: dict = Depends(require_farm_role())):
    return crud.list_batches(farm_id, status_filter)


@router.get("/batches/{batch_id}")
def get_batch(farm_id: str, batch_id: str, _member: dict = Depends(require_farm_role())):
    return _get_batch_or_404(farm_id, batch_id)


@router.post("/batches/{batch_id}/mortality", status_code=status.HTTP_201_CREATED)
def record_mortality(farm_id: str, batch_id: str, data: MortalityCreate, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    """Recording a death atomically decrements the batch's live quantity —
    quantity_current is the single source of truth dashboards read from."""
    batch = _get_batch_or_404(farm_id, batch_id)
    if data.quantity > batch["quantity_current"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Cannot record {data.quantity} deaths — only {batch['quantity_current']} animals remain in this batch",
        )
    payload = data.model_dump()
    payload["date"] = payload["date"].isoformat()
    record = crud.create_mortality_record(batch_id, payload)
    try:
        crud.decrement_batch_quantity(batch, data.quantity)
    except ValueError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This batch's stock just changed (likely another sale or mortality record "
            "happening at the same time). The mortality record was saved — please refresh "
            "to see the current count.",
        )
    return record


@router.get("/batches/{batch_id}/mortality")
def list_mortality(farm_id: str, batch_id: str, _member: dict = Depends(require_farm_role())):
    _get_batch_or_404(farm_id, batch_id)
    return crud.list_mortality_records(batch_id)


@router.post("/batches/{batch_id}/medication", status_code=status.HTTP_201_CREATED)
def record_medication(farm_id: str, batch_id: str, data: MedicationCreate, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    _get_batch_or_404(farm_id, batch_id)
    payload = data.model_dump()
    payload["date_administered"] = payload["date_administered"].isoformat() if payload["date_administered"] else None
    payload["next_due_date"] = payload["next_due_date"].isoformat() if payload["next_due_date"] else None
    return crud.create_medication_record(batch_id, payload)


@router.get("/batches/{batch_id}/medication")
def list_medication(farm_id: str, batch_id: str, _member: dict = Depends(require_farm_role())):
    _get_batch_or_404(farm_id, batch_id)
    return crud.list_medication_records(batch_id)


@router.get("/batches/{batch_id}/profit")
def batch_profit(farm_id: str, batch_id: str, _member: dict = Depends(require_farm_role(*_FINANCE_VIEW_ROLES))):
    """Real per-batch cost-allocated profit — see crud/finance.py's
    batch_profit_summary() docstring for the weighted-average costing
    method (AUDIT.md FWA-006). Restricted to finance-viewing roles, same
    as finance_routes.py's /finance-summary — a worker who can log a
    mortality event doesn't automatically get to see the batch's cost
    and profit figures."""
    _get_batch_or_404(farm_id, batch_id)
    return crud.batch_profit_summary(farm_id, batch_id)
