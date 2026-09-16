"""crud/animals.py — animal_batches, mortality_records, medication_records."""

from __future__ import annotations

import uuid as _uuid
from typing import Optional

from core.db import supabase
from crud._helpers import _now, _new_id, _one, _many

MEDIA_BUCKET = "batch-media"  # separate from field_reports' "field-reports" bucket — see farmwise_batch_media_migration.sql


def upload_media(farm_id: str, file_bytes: bytes, filename: str, content_type: str) -> dict:
    """Uploads one photo/video to Supabase Storage and returns
    {"url": public_url, "type": "image"|"video"} ready to append to a
    mortality or medication record's `media` list.

    Mirrors crud/field_reports.py's upload_media exactly — same
    content-type-derived extension (never the client-supplied filename,
    which could inject path segments — see that function's docstring for
    the full reasoning), same allowlist validated one layer up in
    routes/animal_routes.py before this is ever called.
    """
    _EXT_BY_CONTENT_TYPE = {
        "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
        "image/heic": "heic", "image/heif": "heif",
        "video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm",
    }
    ext = _EXT_BY_CONTENT_TYPE.get(content_type, "bin")
    storage_path = f"{farm_id}/{_uuid.uuid4()}.{ext}"

    supabase.storage.from_(MEDIA_BUCKET).upload(
        storage_path, file_bytes, {"content-type": content_type}
    )
    public_url = supabase.storage.from_(MEDIA_BUCKET).get_public_url(storage_path)

    media_type = "video" if content_type.startswith("video/") else "image"
    return {"url": public_url, "type": media_type}


# ── Batches ──────────────────────────────────────────────────────────────

def create_batch(farm_id: str, data: dict) -> dict:
    row = {
        "id": _new_id(),
        "farm_id": farm_id,
        "quantity_current": data["quantity_initial"],
        "status": "active",
        "deleted_at": None,
        "created_at": _now(),
        "updated_at": _now(),
        **data,
    }
    res = supabase.table("animal_batches").insert(row).execute()
    return _one(res)


def get_batch(farm_id: str, batch_id: str) -> Optional[dict]:
    res = (
        supabase.table("animal_batches")
        .select("*")
        .eq("id", batch_id)
        .eq("farm_id", farm_id)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    return _one(res)


def list_batches(farm_id: str, status_filter: str | None = None) -> list[dict]:
    query = supabase.table("animal_batches").select("*").eq("farm_id", farm_id).is_("deleted_at", "null")
    if status_filter:
        query = query.eq("status", status_filter)
    res = query.order("created_at", desc=True).execute()
    return _many(res)


def update_batch(farm_id: str, batch_id: str, fields: dict) -> Optional[dict]:
    """species and quantity_current are deliberately never accepted here
    (routes/animal_routes.py's BatchUpdate schema doesn't expose them) —
    species defines what the batch fundamentally is, and quantity_current
    is only ever changed through the atomic, stock-reconciling paths
    above/below (sales, mortality) or at creation. This is for the
    editable metadata: name, breed, purchase price, supplier, expected
    selling date, notes."""
    fields = {**fields, "updated_at": _now()}
    res = supabase.table("animal_batches").update(fields).eq("id", batch_id).eq("farm_id", farm_id).is_("deleted_at", "null").execute()
    return _one(res)


def soft_delete_batch(farm_id: str, batch_id: str) -> None:
    """Soft delete (the deleted_at column already existed in the schema
    for exactly this — get_batch/list_batches already filter it out, this
    was just never wired up to a route). Mortality/medication/feed-
    consumption/sales rows referencing this batch are left alone — they're
    historical records of what actually happened and stay queryable by
    id even after the batch itself is hidden from normal listings;
    Postgres's default FK behavior (no ON DELETE CASCADE was ever set on
    any of those foreign keys) means a real hard-delete would fail loudly
    against any batch with history anyway, which is the right behavior —
    soft delete sidesteps that entirely rather than fighting it."""
    supabase.table("animal_batches").update({"deleted_at": _now(), "updated_at": _now()}).eq("id", batch_id).eq("farm_id", farm_id).execute()


def decrement_batch_quantity(batch: dict, amount: int) -> dict:
    """Atomic, race-safe adjustment. Positive amount decrements (a sale,
    a mortality record); negative amount increments (restoring stock
    when a sale/mortality record is edited or deleted — see
    routes/finance_routes.py's update_sale/delete_sale and
    routes/animal_routes.py's delete_mortality).

    Retries with optimistic concurrency control on `updated_at` — the
    same pattern crud/inventory.py's adjust_stock uses: compute the new
    value from a specific read, and only commit if `updated_at` still
    matches that same read at write time; a concurrent writer changes
    `updated_at`, so a losing attempt gets zero matched rows and retries
    against a freshly re-read row.

    This replaces an earlier version whose WHERE-clause guard
    (`quantity_current >= amount`) correctly stopped the count from ever
    going negative, but did NOT stop a lost update: if two concurrent
    decrements both individually satisfied that guard (each against the
    live row at ITS check-time), the second to commit would still WRITE
    its own stale pre-read value, silently discarding the first
    decrement's result — e.g. batch at 10, two concurrent sales of 3
    each could both "succeed" but leave the batch at 7 instead of the
    correct 4. Caught while adding more callers that reuse this function
    with a negative amount to restore stock (this pass's sale/mortality
    edit-and-delete routes) — worth fixing the underlying primitive
    correctly now rather than building more call sites on top of a
    subtly wrong one. See tests/test_stock_concurrency.py for the
    regression tests, including one that reproduces the exact lost-
    update scenario above.
    """
    for _attempt in range(5):
        current_quantity = batch["quantity_current"]
        current_version = batch["updated_at"]
        new_quantity = current_quantity - amount
        if new_quantity < 0:
            raise ValueError("insufficient_stock")

        fields = {"quantity_current": new_quantity, "updated_at": _now()}
        if new_quantity == 0:
            fields["status"] = "closed"
        elif new_quantity > 0 and batch.get("status") == "closed":
            # Restoring stock (a sale/mortality edit or delete) on a batch
            # that auto-closed at zero should reopen it — a "closed" batch
            # with a positive headcount is an inconsistent state that
            # could never happen before this pass added stock-restoring
            # callers.
            fields["status"] = "active"

        res = (
            supabase.table("animal_batches")
            .update(fields)
            .eq("id", batch["id"])
            .eq("updated_at", current_version)
            .execute()
        )
        updated = _one(res)
        if updated is not None:
            return updated

        # Lost the race — re-read the current row and retry against it.
        refreshed = get_batch(batch["farm_id"], batch["id"])
        if refreshed is None:
            raise ValueError("batch_not_found")
        batch = refreshed

    raise ValueError("stock_adjustment_contention")  # exhausted retries under heavy concurrent writes


# ── Mortality ────────────────────────────────────────────────────────────

def create_mortality_record(batch_id: str, data: dict) -> dict:
    row = {"id": _new_id(), "batch_id": batch_id, "created_at": _now(), "updated_at": _now(), **data}
    res = supabase.table("mortality_records").insert(row).execute()
    return _one(res)


def get_mortality_record(batch_id: str, record_id: str) -> Optional[dict]:
    res = supabase.table("mortality_records").select("*").eq("id", record_id).eq("batch_id", batch_id).limit(1).execute()
    return _one(res)


def update_mortality_record(batch_id: str, record_id: str, fields: dict) -> Optional[dict]:
    """quantity is deliberately never accepted here (routes/animal_routes.py's
    MortalityUpdate schema doesn't expose it) — a mortality record's
    quantity is tied to a batch stock adjustment made atomically at
    creation time (crud.decrement_batch_quantity); changing it after the
    fact would need the exact same delta-reconciliation dance as sales
    (see crud/finance.py's update_sale). Editing date/cause/notes is safe
    and doesn't touch stock at all — for a wrong quantity, delete the
    record (routes/animal_routes.py's delete restores the stock
    correctly) and log a new one with the right number."""
    fields = {**fields, "updated_at": _now()}
    res = supabase.table("mortality_records").update(fields).eq("id", record_id).eq("batch_id", batch_id).execute()
    return _one(res)


def delete_mortality_record(batch_id: str, record_id: str) -> None:
    supabase.table("mortality_records").delete().eq("id", record_id).eq("batch_id", batch_id).execute()


def list_mortality_records(batch_id: str) -> list[dict]:
    res = (
        supabase.table("mortality_records")
        .select("*")
        .eq("batch_id", batch_id)
        .order("date", desc=True)
        .execute()
    )
    return _many(res)


# ── Medication ───────────────────────────────────────────────────────────

def create_medication_record(batch_id: str, data: dict) -> dict:
    row = {"id": _new_id(), "batch_id": batch_id, "created_at": _now(), "updated_at": _now(), **data}
    res = supabase.table("medication_records").insert(row).execute()
    return _one(res)


def get_medication_record(batch_id: str, record_id: str) -> Optional[dict]:
    res = supabase.table("medication_records").select("*").eq("id", record_id).eq("batch_id", batch_id).limit(1).execute()
    return _one(res)


def update_medication_record(batch_id: str, record_id: str, fields: dict) -> Optional[dict]:
    fields = {**fields, "updated_at": _now()}
    res = supabase.table("medication_records").update(fields).eq("id", record_id).eq("batch_id", batch_id).execute()
    return _one(res)


def delete_medication_record(batch_id: str, record_id: str) -> None:
    supabase.table("medication_records").delete().eq("id", record_id).eq("batch_id", batch_id).execute()


def list_medication_records(batch_id: str) -> list[dict]:
    res = (
        supabase.table("medication_records")
        .select("*")
        .eq("batch_id", batch_id)
        .order("next_due_date")
        .execute()
    )
    return _many(res)
