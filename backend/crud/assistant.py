"""crud/assistant.py — ai_messages (per-user, per-farm conversation history)."""

from __future__ import annotations

from core.db import supabase
from crud._helpers import _now, _new_id, _one, _many

HISTORY_LIMIT = 50  # most recent turns kept per user+farm — enough context, not unbounded growth


def create_ai_message(farm_id: str, user_id: str, role: str, content: str) -> dict:
    row = {
        "id": _new_id(), "farm_id": farm_id, "user_id": user_id,
        "role": role, "content": content, "created_at": _now(), "updated_at": _now(),
    }
    res = supabase.table("ai_messages").insert(row).execute()
    return _one(res)


def list_ai_messages(farm_id: str, user_id: str, limit: int = HISTORY_LIMIT) -> list[dict]:
    res = (
        supabase.table("ai_messages").select("*")
        .eq("farm_id", farm_id).eq("user_id", user_id)
        .order("created_at", desc=True).limit(limit).execute()
    )
    return list(reversed(_many(res)))  # chronological order for display/prompt construction


def clear_ai_history(farm_id: str, user_id: str) -> None:
    supabase.table("ai_messages").delete().eq("farm_id", farm_id).eq("user_id", user_id).execute()
