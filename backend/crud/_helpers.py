"""crud/_helpers.py — small shared helpers used across every crud submodule."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _one(res) -> Optional[dict]:
    """Supabase response -> first row dict, or None."""
    data = res.data
    return data[0] if data else None


def _many(res) -> list[dict]:
    """Supabase response -> list of row dicts (empty list if none)."""
    return res.data or []
