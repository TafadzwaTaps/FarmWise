"""routes/animal_routes.py — Animal batches, mortality, medication.
Routes: /farms/{farm_id}/animals/batches, .../mortality, .../medication, .../media
"""

from datetime import date
from datetime import date as _date  # alias for use in fields literally named "date" that also carry a default — see MortalityUpdate below
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from pydantic import BaseModel, Field, field_validator

import crud
from core.auth import require_farm_role, get_current_user
from routes._deps import audit, log

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

# Evidence photo/video attachments on mortality/medication records — same
# shape and same allowlist reasoning as routes/field_report_routes.py's
# ALLOWED_MEDIA_TYPES (an explicit allowlist, not a prefix match, since
# "image/" would also admit "image/svg+xml" — SVG can embed <script>,
# a stored-XSS vector if ever rendered from its public Storage URL).
MAX_MEDIA_BYTES = 25 * 1024 * 1024  # 25 MB per file
ALLOWED_MEDIA_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    "video/mp4", "video/quicktime", "video/webm",
}


class MediaItem(BaseModel):
    url: str
    type: str  # "image" | "video"


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


BatchStatus = Literal["active", "closed"]


class AnimalBatchUpdate(BaseModel):
    """Matches the mobile app's single-form batch edit exactly (species,
    status, and current count included) rather than the previous web-only
    design that excluded them — see update_batch below for how a
    quantity_current change is still routed through the atomic,
    race-safe adjustment path rather than a raw overwrite, even though
    it's now reachable from this one form instead of a separate
    "Adjust stock" flow."""
    batch_name: str | None = Field(default=None, min_length=1, max_length=150)
    species: Species | None = None
    breed: str | None = None
    quantity_current: int | None = Field(default=None, ge=0)
    purchase_date: date | None = None
    purchase_price_total: float | None = None
    supplier: str | None = None
    status: BatchStatus | None = None
    average_weight_kg: float | None = None
    expected_selling_date: date | None = None
    notes: str | None = None
    adjustment_reason: str | None = None  # optional context if quantity_current changed — not required, matching mobile's simpler form


class BatchStockAdjustment(BaseModel):
    """A direct manual correction to a batch's live headcount — separate
    from sales and mortality, which already adjust it correctly through
    their own reconciled flows. This is for the cases neither of those
    covers: a miscount at creation, a physical recount, an animal that
    wandered back. Deliberately requires a reason — an unexplained
    quantity jump in a farm's records is exactly the kind of thing an
    owner reviewing history later needs context for."""
    delta: int = Field(description="Positive to add animals, negative to remove — never zero.")
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("delta")
    @classmethod
    def delta_not_zero(cls, v):
        if v == 0:
            raise ValueError("delta must not be zero — there's nothing to adjust")
        return v


class MortalityCreate(BaseModel):
    date: date
    quantity: int = Field(gt=0)
    cause: str | None = None
    notes: str | None = None
    media: list[MediaItem] = Field(default_factory=list)


class MortalityUpdate(BaseModel):
    """quantity isn't editable here — see crud.update_mortality_record's
    docstring. date/cause/notes/media are safe to fix without touching stock."""
    date: _date | None = None
    cause: str | None = None
    notes: str | None = None
    media: list[MediaItem] | None = None


class MedicationCreate(BaseModel):
    type: MedicationType
    name: str
    date_administered: date | None = None
    next_due_date: date | None = None
    dosage: str | None = None
    administered_by: str | None = None
    cost: float | None = Field(default=None, ge=0)
    notes: str | None = None
    media: list[MediaItem] = Field(default_factory=list)


class MedicationUpdate(BaseModel):
    type: MedicationType | None = None
    name: str | None = Field(default=None, min_length=1)
    date_administered: date | None = None
    next_due_date: date | None = None
    dosage: str | None = None
    administered_by: str | None = None
    cost: float | None = Field(default=None, ge=0)
    notes: str | None = None
    media: list[MediaItem] | None = None


def _get_batch_or_404(farm_id: str, batch_id: str) -> dict:
    batch = crud.get_batch(farm_id, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Animal batch not found")
    return batch


@router.post("/media")
async def upload_media(farm_id: str, file: UploadFile = File(...), _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    """Uploads one evidence photo/video for a mortality or medication
    record and returns {"url": ..., "type": "image"|"video"} — the
    frontend collects these into a `media` list and includes it when
    creating/updating the actual record, same two-step flow already used
    for field reports (upload first, attach the result)."""
    if not file.content_type or file.content_type.lower() not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only JPEG, PNG, WEBP, HEIC images or MP4/MOV/WEBM videos are allowed",
        )

    file_bytes = await file.read()
    if len(file_bytes) > MAX_MEDIA_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large (max 25 MB)")
    if not file_bytes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")

    try:
        return crud.upload_batch_media(farm_id, file_bytes, file.filename or "upload", file.content_type)
    except Exception as exc:
        log.error("animal_media_upload_failed farm_id=%s error=%s", farm_id, exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not upload media right now — make sure the 'batch-media' storage bucket exists in Supabase.",
        )


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


@router.patch("/batches/{batch_id}")
def update_batch(farm_id: str, batch_id: str, data: AnimalBatchUpdate, _member: dict = Depends(require_farm_role(*_MANAGE_ROLES)), user: dict = Depends(get_current_user)):
    """A single form/save action can now change the batch's live headcount
    (quantity_current) alongside its other fields — matching the mobile
    app's one-form edit exactly. quantity_current is NOT written directly
    though: it's routed through the same atomic, race-safe
    crud.decrement_batch_quantity used everywhere else a batch's count
    changes (sales, mortality, the standalone /adjust endpoint), computed
    as a signed delta from the batch's current value. This preserves
    every correctness guarantee that function provides (no lost updates
    under concurrent writes, auto reopen/close on the status transition)
    while still letting the count be edited from this one form the way
    mobile's UI does, instead of a separate screen/modal.
    """
    batch = _get_batch_or_404(farm_id, batch_id)
    fields = {k: v for k, v in data.model_dump().items() if v is not None and k not in ("quantity_current", "adjustment_reason")}
    quantity_current = data.quantity_current

    if not fields and quantity_current is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to update")

    if "purchase_date" in fields:
        fields["purchase_date"] = fields["purchase_date"].isoformat()
    if "expected_selling_date" in fields:
        fields["expected_selling_date"] = fields["expected_selling_date"].isoformat()

    if quantity_current is not None and quantity_current != batch["quantity_current"]:
        delta = quantity_current - batch["quantity_current"]
        try:
            batch = crud.decrement_batch_quantity(batch, -delta)
        except ValueError:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This batch's stock just changed (another operation happening at the same time). "
                "Please refresh and try again.",
            )
        audit(
            "batch_stock_adjusted", farm_id=farm_id, batch_id=batch_id, delta=delta,
            reason=data.adjustment_reason or "Manual correction via batch edit", by_user=user["user_id"],
        )
        # An explicit status choice in this same save should still win over
        # decrement_batch_quantity's own auto-close-at-zero/auto-reopen
        # logic — a manager closing a batch on purpose (or deliberately
        # keeping it open despite a zero count, however unusual) shouldn't
        # be silently overridden by the side effect of a stock edit made
        # in the same action.

    if fields:
        batch = crud.update_batch(farm_id, batch_id, fields)

    return batch


@router.delete("/batches/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch(farm_id: str, batch_id: str, _member: dict = Depends(require_farm_role(*_MANAGE_ROLES))):
    """Soft delete — the batch's sales/mortality/medication/feed-consumption
    history stays intact and queryable by id, just hidden from normal
    batch listings going forward (see crud.soft_delete_batch's docstring)."""
    _get_batch_or_404(farm_id, batch_id)
    crud.soft_delete_batch(farm_id, batch_id)


@router.post("/batches/{batch_id}/adjust")
def adjust_batch_stock(farm_id: str, batch_id: str, data: BatchStockAdjustment, _member: dict = Depends(require_farm_role(*_MANAGE_ROLES)), user: dict = Depends(get_current_user)):
    """Manual stock correction — see BatchStockAdjustment's docstring for
    why this exists alongside sales/mortality rather than just editing
    quantity_current directly. Reuses the same atomic, race-safe
    adjustment crud.decrement_batch_quantity already provides for
    sale/mortality reconciliation (see crud/animals.py) — a positive
    delta here means "add stock", which is a negative amount to that
    function (which decrements); same inversion used by
    routes/finance_routes.py's delete_sale."""
    batch = _get_batch_or_404(farm_id, batch_id)
    try:
        updated = crud.decrement_batch_quantity(batch, -data.delta)
    except ValueError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This batch's stock just changed (another operation happening at the same time), "
            "or this adjustment would take it below zero. Please refresh and try again.",
        )
    audit("batch_stock_adjusted", farm_id=farm_id, batch_id=batch_id, delta=data.delta, reason=data.reason, by_user=user["user_id"])
    return updated


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


@router.patch("/batches/{batch_id}/mortality/{record_id}")
def update_mortality(farm_id: str, batch_id: str, record_id: str, data: MortalityUpdate, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    _get_batch_or_404(farm_id, batch_id)
    if crud.get_mortality_record(batch_id, record_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mortality record not found")
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to update")
    if "date" in fields:
        fields["date"] = fields["date"].isoformat()
    return crud.update_mortality_record(batch_id, record_id, fields)


@router.delete("/batches/{batch_id}/mortality/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mortality(farm_id: str, batch_id: str, record_id: str, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    batch = _get_batch_or_404(farm_id, batch_id)
    record = crud.get_mortality_record(batch_id, record_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mortality record not found")
    # Deleting a mortality record means those animals didn't actually die
    # (a logging mistake) — restore the stock it removed at creation time.
    crud.decrement_batch_quantity(batch, -record["quantity"])  # negative amount = give back, see crud/animals.py
    crud.delete_mortality_record(batch_id, record_id)


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


@router.patch("/batches/{batch_id}/medication/{record_id}")
def update_medication(farm_id: str, batch_id: str, record_id: str, data: MedicationUpdate, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    _get_batch_or_404(farm_id, batch_id)
    if crud.get_medication_record(batch_id, record_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Medication record not found")
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to update")
    if "date_administered" in fields:
        fields["date_administered"] = fields["date_administered"].isoformat()
    if "next_due_date" in fields:
        fields["next_due_date"] = fields["next_due_date"].isoformat()
    return crud.update_medication_record(batch_id, record_id, fields)


@router.delete("/batches/{batch_id}/medication/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_medication(farm_id: str, batch_id: str, record_id: str, _member: dict = Depends(require_farm_role(*_RECORD_ROLES))):
    _get_batch_or_404(farm_id, batch_id)
    if crud.get_medication_record(batch_id, record_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Medication record not found")
    crud.delete_medication_record(batch_id, record_id)


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
