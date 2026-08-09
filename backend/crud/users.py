"""
crud/users.py — users, refresh_tokens, otp_codes.

Return convention (matches WaziBot's crud/*.py):
  • Single-row lookups return a dict or None.
  • Multi-row lookups return a list of dicts.
  • Create / update operations return the created/updated dict.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from core.db import supabase
from crud._helpers import _now, _new_id, _one, _many

log = logging.getLogger("farmwise.crud.users")

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 15
OTP_EXPIRE_MINUTES = 10


# ── Users ────────────────────────────────────────────────────────────────

def create_user(full_name: str, email: Optional[str], phone_number: Optional[str], hashed_password: str) -> dict:
    row = {
        "id": _new_id(),
        "full_name": full_name,
        "email": email,
        "phone_number": phone_number,
        "hashed_password": hashed_password,
        "global_role": "user",
        "is_active": True,
        "is_email_verified": False,
        "is_phone_verified": False,
        "failed_login_attempts": 0,
        "created_at": _now(),
        "updated_at": _now(),
    }
    res = supabase.table("users").insert(row).execute()
    return _one(res)


def get_user_by_id(user_id: str) -> Optional[dict]:
    res = supabase.table("users").select("*").eq("id", user_id).limit(1).execute()
    return _one(res)


def get_user_by_email(email: str) -> Optional[dict]:
    res = supabase.table("users").select("*").eq("email", email).limit(1).execute()
    return _one(res)


def get_user_by_phone(phone_number: str) -> Optional[dict]:
    res = supabase.table("users").select("*").eq("phone_number", phone_number).limit(1).execute()
    return _one(res)


def get_user_by_identifier(identifier: str) -> Optional[dict]:
    """identifier may be an email or a phone number — login accepts either."""
    return get_user_by_email(identifier) or get_user_by_phone(identifier)


def update_user(user_id: str, fields: dict) -> Optional[dict]:
    fields = {**fields, "updated_at": _now()}
    res = supabase.table("users").update(fields).eq("id", user_id).execute()
    return _one(res)


def register_failed_login(user: dict) -> dict:
    attempts = (user.get("failed_login_attempts") or 0) + 1
    fields = {"failed_login_attempts": attempts}
    if attempts >= MAX_FAILED_ATTEMPTS:
        fields["locked_until"] = (datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_DURATION_MINUTES)).isoformat()
    return update_user(user["id"], fields)


def clear_failed_logins(user_id: str) -> dict:
    return update_user(user_id, {"failed_login_attempts": 0, "locked_until": None, "last_login_at": _now()})


def is_locked(user: dict) -> bool:
    locked_until = user.get("locked_until")
    if not locked_until:
        return False
    expires = datetime.fromisoformat(locked_until.replace("Z", "+00:00"))
    return expires > datetime.now(timezone.utc)


# ── Refresh tokens ───────────────────────────────────────────────────────
# One row per issued refresh token = one row per logged-in device/session.
# Enables multi-device login, per-device logout, and instant revocation on
# password reset — a bare stateless refresh JWT can't do any of that.

def create_refresh_token_row(user_id: str, jti: str, device_label: str | None, remember_me: bool, expires_at: str) -> dict:
    row = {
        "id": _new_id(),
        "user_id": user_id,
        "jti": jti,
        "device_label": device_label,
        "remember_me": remember_me,
        "revoked": False,
        "expires_at": expires_at,
        "created_at": _now(),
    }
    res = supabase.table("refresh_tokens").insert(row).execute()
    return _one(res)


def get_refresh_token_by_jti(jti: str) -> Optional[dict]:
    res = supabase.table("refresh_tokens").select("*").eq("jti", jti).limit(1).execute()
    return _one(res)


def revoke_refresh_token(jti: str) -> None:
    supabase.table("refresh_tokens").update({"revoked": True}).eq("jti", jti).execute()


def revoke_all_refresh_tokens(user_id: str) -> None:
    supabase.table("refresh_tokens").update({"revoked": True}).eq("user_id", user_id).eq("revoked", False).execute()


# ── OTP codes ────────────────────────────────────────────────────────────

def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def create_otp(destination: str, code: str, purpose: str) -> dict:
    row = {
        "id": _new_id(),
        "destination": destination,
        "code_hash": _hash_code(code),
        "purpose": purpose,
        "attempts": 0,
        "consumed": False,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRE_MINUTES)).isoformat(),
        "created_at": _now(),
    }
    res = supabase.table("otp_codes").insert(row).execute()
    return _one(res)


def _latest_active_otp(destination: str, purpose: str) -> Optional[dict]:
    res = (
        supabase.table("otp_codes")
        .select("*")
        .eq("destination", destination)
        .eq("purpose", purpose)
        .eq("consumed", False)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return _one(res)


def verify_otp(destination: str, code: str, purpose: str) -> bool:
    otp = _latest_active_otp(destination, purpose)
    if otp is None:
        return False

    expires = datetime.fromisoformat(otp["expires_at"].replace("Z", "+00:00"))
    if expires < datetime.now(timezone.utc):
        return False

    attempts = (otp.get("attempts") or 0) + 1
    if attempts > 5:
        supabase.table("otp_codes").update({"attempts": attempts}).eq("id", otp["id"]).execute()
        return False

    if otp["code_hash"] != _hash_code(code):
        supabase.table("otp_codes").update({"attempts": attempts}).eq("id", otp["id"]).execute()
        return False

    supabase.table("otp_codes").update({"attempts": attempts, "consumed": True}).eq("id", otp["id"]).execute()
    return True
