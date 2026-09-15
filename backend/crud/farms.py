"""crud/farms.py — farms, farm_members."""

from __future__ import annotations

from typing import Optional

from postgrest.exceptions import APIError

from core.db import supabase
from crud._helpers import _now, _new_id, _one, _many

_UNIQUE_VIOLATION = "23505"  # Postgres SQLSTATE for a unique-constraint violation


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
    member_res = supabase.table("farm_members").select("farm_id, role").eq("user_id", user_id).execute()
    memberships = _many(member_res)
    role_by_farm = {m["farm_id"]: m["role"] for m in memberships}
    farm_ids = list(role_by_farm.keys())
    if not farm_ids:
        return []
    res = supabase.table("farms").select("*").in_("id", farm_ids).is_("deleted_at", "null").execute()
    farms = _many(res)
    # The mobile/web app has no other way to know the caller's role on each
    # farm — without this, every screen has to assume the most permissive
    # role, which is exactly how a worker account ends up seeing full admin
    # CRUD it has no server-side permission to actually use.
    for farm in farms:
        farm["my_role"] = role_by_farm.get(farm["id"])
    return farms


def update_farm(farm_id: str, fields: dict) -> Optional[dict]:
    fields = {**fields, "updated_at": _now()}
    res = supabase.table("farms").update(fields).eq("id", farm_id).execute()
    return _one(res)


def soft_delete_farm(farm_id: str) -> None:
    supabase.table("farms").update({"deleted_at": _now()}).eq("id", farm_id).execute()


# ── Members ──────────────────────────────────────────────────────────────

def add_member(farm_id: str, user_id: str, role: str, invited_by: str | None = None) -> dict:
    """farm_create's call site (a brand-new farm has zero members) can
    never hit the UNIQUE(farm_id, user_id) constraint — the invite path
    added in routes/farm_routes.py can, when someone invites a user who's
    already a member. Catch it and raise a clean ValueError rather than
    letting a raw postgrest 23505 surface as an unhandled 500 (same
    pattern as crud/workers.py's record_attendance)."""
    row = {
        "id": _new_id(),
        "farm_id": farm_id,
        "user_id": user_id,
        "role": role,
        "invited_by": invited_by,
        "created_at": _now(),
        "updated_at": _now(),
    }
    try:
        res = supabase.table("farm_members").insert(row).execute()
    except APIError as exc:
        if exc.code == _UNIQUE_VIOLATION:
            raise ValueError("already_a_member") from exc
        raise
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


def get_member_by_id(farm_id: str, member_id: str) -> Optional[dict]:
    """member_id is farm_members.id (the membership row's own primary key),
    NOT user_id — routes/farm_routes.py's URLs are .../members/{member_id}
    for exactly this reason: a user can only ever have one membership row
    per farm (enforced by the UNIQUE(farm_id, user_id) constraint added in
    farmwise_indexes_and_constraints_migration.sql), so either id works to
    look them up, but member_id matches what list_members() already
    returns to the client without an extra round trip."""
    res = (
        supabase.table("farm_members").select("*")
        .eq("id", member_id).eq("farm_id", farm_id).limit(1).execute()
    )
    return _one(res)


def update_member_role(farm_id: str, member_id: str, role: str) -> Optional[dict]:
    fields = {"role": role, "updated_at": _now()}
    res = supabase.table("farm_members").update(fields).eq("id", member_id).eq("farm_id", farm_id).execute()
    return _one(res)


def remove_member(farm_id: str, member_id: str) -> None:
    supabase.table("farm_members").delete().eq("id", member_id).eq("farm_id", farm_id).execute()
