"""crud/workers.py — workers, worker_attendance, worker_payments."""

from __future__ import annotations

from typing import Optional

from postgrest.exceptions import APIError

from core.db import supabase
from crud._helpers import _now, _new_id, _one, _many

_UNIQUE_VIOLATION = "23505"  # Postgres SQLSTATE for a unique-constraint violation


# ── Workers ──────────────────────────────────────────────────────────────

def create_worker(farm_id: str, data: dict) -> dict:
    row = {
        "id": _new_id(), "farm_id": farm_id, "status": "active",
        "created_at": _now(), "updated_at": _now(), **data,
    }
    res = supabase.table("workers").insert(row).execute()
    return _one(res)


def get_worker(farm_id: str, worker_id: str) -> Optional[dict]:
    res = (
        supabase.table("workers").select("*")
        .eq("id", worker_id).eq("farm_id", farm_id).limit(1).execute()
    )
    return _one(res)


def list_workers(farm_id: str, status_filter: str | None = None) -> list[dict]:
    query = supabase.table("workers").select("*").eq("farm_id", farm_id)
    if status_filter:
        query = query.eq("status", status_filter)
    res = query.order("created_at", desc=True).execute()
    return _many(res)


def update_worker(farm_id: str, worker_id: str, fields: dict) -> Optional[dict]:
    fields = {**fields, "updated_at": _now()}
    res = supabase.table("workers").update(fields).eq("id", worker_id).eq("farm_id", farm_id).execute()
    return _one(res)


def delete_worker(farm_id: str, worker_id: str) -> None:
    supabase.table("workers").delete().eq("id", worker_id).eq("farm_id", farm_id).execute()


# ── Attendance ───────────────────────────────────────────────────────────

def record_attendance(worker_id: str, data: dict) -> dict:
    """The route layer (routes/worker_routes.py) already does a friendly
    pre-check for the common case, but a pre-check alone is a
    check-then-act race — two concurrent submissions for the same
    worker+date could both pass it. The real backstop is the database's
    UNIQUE(worker_id, date) constraint (see
    farmwise_indexes_and_constraints_migration.sql); this catches that
    constraint firing and turns it into the same clean signal the
    pre-check gives, instead of letting a raw postgrest 23505 surface as
    an unhandled 500."""
    row = {"id": _new_id(), "worker_id": worker_id, "created_at": _now(), "updated_at": _now(), **data}
    try:
        res = supabase.table("worker_attendance").insert(row).execute()
    except APIError as exc:
        if exc.code == _UNIQUE_VIOLATION:
            raise ValueError("duplicate_attendance") from exc
        raise
    return _one(res)


def list_attendance(worker_id: str) -> list[dict]:
    res = (
        supabase.table("worker_attendance").select("*")
        .eq("worker_id", worker_id).order("date", desc=True).execute()
    )
    return _many(res)


# ── Payments ─────────────────────────────────────────────────────────────

def create_payment(worker_id: str, data: dict) -> dict:
    row = {"id": _new_id(), "worker_id": worker_id, "created_at": _now(), "updated_at": _now(), **data}
    res = supabase.table("worker_payments").insert(row).execute()
    return _one(res)


def list_payments(worker_id: str) -> list[dict]:
    res = (
        supabase.table("worker_payments").select("*")
        .eq("worker_id", worker_id).order("payment_date", desc=True).execute()
    )
    return _many(res)
