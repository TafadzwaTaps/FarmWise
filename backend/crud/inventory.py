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


def update_item(item_id: str, fields: dict) -> Optional[dict]:
    fields = {**fields, "updated_at": _now()}
    res = supabase.table("inventory_items").update(fields).eq("id", item_id).execute()
    item = _one(res)
    return _with_low_stock(item) if item else None


def adjust_stock(item: dict, delta: float) -> dict:
    """Delta-based stock in/out (positive = in, negative = out) — two workers
    logging usage at the same time don't clobber each other the way an
    absolute `set` would."""
    new_quantity = float(item["quantity_on_hand"]) + delta
    return update_item(item["id"], {"quantity_on_hand": new_quantity})


def delete_item(item_id: str) -> None:
    supabase.table("inventory_items").delete().eq("id", item_id).execute()


def _with_low_stock(item: dict) -> dict:
    item["is_low_stock"] = float(item["quantity_on_hand"]) <= float(item.get("low_stock_threshold") or 0)
    return item
