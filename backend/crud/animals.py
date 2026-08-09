"""crud/animals.py — animal_batches, mortality_records, medication_records."""

from __future__ import annotations

from typing import Optional

from core.db import supabase
from crud._helpers import _now, _new_id, _one, _many


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


def update_batch(batch_id: str, fields: dict) -> Optional[dict]:
    fields = {**fields, "updated_at": _now()}
    res = supabase.table("animal_batches").update(fields).eq("id", batch_id).execute()
    return _one(res)


def decrement_batch_quantity(batch: dict, amount: int) -> dict:
    new_quantity = batch["quantity_current"] - amount
    fields = {"quantity_current": new_quantity}
    if new_quantity == 0:
        fields["status"] = "closed"
    return update_batch(batch["id"], fields)


# ── Mortality ────────────────────────────────────────────────────────────

def create_mortality_record(batch_id: str, data: dict) -> dict:
    row = {"id": _new_id(), "batch_id": batch_id, "created_at": _now(), "updated_at": _now(), **data}
    res = supabase.table("mortality_records").insert(row).execute()
    return _one(res)


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


def list_medication_records(batch_id: str) -> list[dict]:
    res = (
        supabase.table("medication_records")
        .select("*")
        .eq("batch_id", batch_id)
        .order("next_due_date")
        .execute()
    )
    return _many(res)
