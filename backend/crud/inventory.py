"""crud/inventory.py — inventory_items."""

from __future__ import annotations

from typing import Optional

from core.db import supabase
from crud._helpers import _now, _new_id, _one, _many


def create_item(farm_id: str, data: dict) -> dict:
    row = {"id": _new_id(), "farm_id": farm_id, "created_at": _now(), "updated_at": _now(), **data}
    res = supabase.table("inventory_items").insert(row).execute()
    return _with_low_stock(_one(res))


def get_item(farm_id: str, item_id: str) -> Optional[dict]:
    res = supabase.table("inventory_items").select("*").eq("id", item_id).eq("farm_id", farm_id).limit(1).execute()
    item = _one(res)
    return _with_low_stock(item) if item else None


def list_items(farm_id: str, low_stock_only: bool = False) -> list[dict]:
    res = supabase.table("inventory_items").select("*").eq("farm_id", farm_id).execute()
    items = [_with_low_stock(i) for i in _many(res)]
    if low_stock_only:
        items = [i for i in items if i["is_low_stock"]]
    return items


def update_item(farm_id: str, item_id: str, fields: dict) -> Optional[dict]:
    """farm_id is included in the WHERE clause (not just checked earlier by
    the caller) as defense-in-depth — the same principle applied to
    animal_batches in crud/animals.py: a resource mutation should never
    rely solely on an id that *happened* to be pre-validated elsewhere."""
    fields = {**fields, "updated_at": _now()}
    res = supabase.table("inventory_items").update(fields).eq("id", item_id).eq("farm_id", farm_id).execute()
    item = _one(res)
    return _with_low_stock(item) if item else None


def adjust_stock(farm_id: str, item: dict, delta: float) -> dict:
    """Atomic, race-safe stock adjustment.

    The old version read `item["quantity_on_hand"]` (fetched moments
    earlier) and wrote the new total unconditionally — the same
    check-then-act race fixed for animal_batches in crud/animals.py.
    Two concurrent adjustments (e.g. two workers logging feed usage
    against the same item within the same moment) could silently lose
    one of the two deltas.

    PostgREST/supabase-py has no server-side `x = x + delta` UPDATE
    without a custom RPC function, and guarding on exact equality of the
    *quantity itself* would compare floats round-tripped through JSON —
    risky to get precision-exact without a live DB to verify against.
    Instead this guards on `updated_at`, an optimistic-concurrency
    "version" check: the UPDATE only commits if the row's `updated_at`
    still matches what was just read. A concurrent writer racing in
    between changes `updated_at`, so the guard fails, zero rows match,
    and this retries against a freshly re-read row rather than silently
    dropping the delta.
    """
    for _attempt in range(5):
        current_qty = float(item["quantity_on_hand"])
        current_version = item["updated_at"]
        new_quantity = current_qty + delta
        fields = {"quantity_on_hand": new_quantity, "updated_at": _now()}
        res = (
            supabase.table("inventory_items")
            .update(fields)
            .eq("id", item["id"])
            .eq("farm_id", farm_id)
            .eq("updated_at", current_version)
            .execute()
        )
        updated = _one(res)
        if updated is not None:
            return _with_low_stock(updated)
        # Lost the race — re-read the current row and retry with the fresh value.
        refreshed = get_item(farm_id, item["id"])
        if refreshed is None:
            raise ValueError("item_not_found")
        item = refreshed
    raise ValueError("stock_adjustment_contention")  # exhausted retries under heavy concurrent writes


def delete_item(farm_id: str, item_id: str) -> None:
    supabase.table("inventory_items").delete().eq("id", item_id).eq("farm_id", farm_id).execute()


def _with_low_stock(item: dict) -> dict:
    item["is_low_stock"] = float(item["quantity_on_hand"]) <= float(item.get("low_stock_threshold") or 0)
    return item
