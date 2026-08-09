"""crud/farms.py — farms, farm_members."""

from __future__ import annotations

from typing import Optional

from core.db import supabase
from crud._helpers import _now, _new_id, _one, _many


def create_farm(name: str, owner_id: str, location=None, size_hectares=None, description=None, currency="USD") -> dict:
    row = {
        "id": _new_id(),
        "name": name,
        "owner_id": owner_id,
        "location": location,
        "size_hectares": size_hectares,
        "description": description,
        "currency": currency,
        "deleted_at": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    res = supabase.table("farms").insert(row).execute()
    farm = _one(res)
    add_member(farm["id"], owner_id, role="farmer")
    return farm


def get_farm(farm_id: str) -> Optional[dict]:
    res = supabase.table("farms").select("*").eq("id", farm_id).is_("deleted_at", "null").limit(1).execute()
    return _one(res)


def list_farms_for_user(user_id: str) -> list[dict]:
    member_res = supabase.table("farm_members").select("farm_id").eq("user_id", user_id).execute()
    farm_ids = [m["farm_id"] for m in _many(member_res)]
    if not farm_ids:
        return []
    res = supabase.table("farms").select("*").in_("id", farm_ids).is_("deleted_at", "null").execute()
    return _many(res)


def update_farm(farm_id: str, fields: dict) -> Optional[dict]:
    fields = {**fields, "updated_at": _now()}
    res = supabase.table("farms").update(fields).eq("id", farm_id).execute()
    return _one(res)


def soft_delete_farm(farm_id: str) -> None:
    supabase.table("farms").update({"deleted_at": _now()}).eq("id", farm_id).execute()


# ── Members ──────────────────────────────────────────────────────────────

def add_member(farm_id: str, user_id: str, role: str, invited_by: str | None = None) -> dict:
    row = {
        "id": _new_id(),
        "farm_id": farm_id,
        "user_id": user_id,
        "role": role,
        "invited_by": invited_by,
        "created_at": _now(),
        "updated_at": _now(),
    }
    res = supabase.table("farm_members").insert(row).execute()
    return _one(res)


def get_membership(farm_id: str, user_id: str) -> Optional[dict]:
    res = (
        supabase.table("farm_members")
        .select("*")
        .eq("farm_id", farm_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return _one(res)


def list_members(farm_id: str) -> list[dict]:
    res = supabase.table("farm_members").select("*").eq("farm_id", farm_id).execute()
    return _many(res)
