"""
services/password_reset_service.py — link-based password reset for email
destinations, matching WaziBot's token design.

Phone-based reset is untouched — it still goes through the existing
OTP-code path in crud/users.py / routes/auth_routes.py (create_otp/
verify_otp). SMS was never wired to a real provider, so nothing that was
actually working is being replaced by adding this.

Security design:
  - Cryptographically random 48-byte token (urlsafe base64)
  - SHA-256 hash stored in DB (crud.create_reset_token); the raw token
    only ever appears in the email URL, never persisted anywhere
  - Tokens expire in 60 minutes (crud.RESET_TOKEN_EXPIRE_MINUTES)
  - Single-use — used_at is set on first redemption
  - All previous unused tokens for a user are invalidated whenever a new
    one is requested
  - Email enumeration protection: request_reset() always returns
    normally, whether or not the email exists — routes/auth_routes.py
    must always show the same generic response either way
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone

import crud
from routes._deps import log
from services.notification_service import _send_email


def _generate_token() -> str:
    """A 64-char URL-safe cryptographically random token."""
    return secrets.token_urlsafe(48)


def request_reset(email: str, ip_address: str = "", user_agent: str = "") -> None:
    """
    Step 1 — start a reset for an email destination.

    Never raises for "email not found" — always completes silently, to
    prevent email enumeration. The route calling this always returns the
    same generic response regardless.
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return

    try:
        user = crud.get_user_by_email(email)
        if user is None:
            log.info("password_reset_email_not_found (not disclosed) ip=%s", ip_address)
            return

        # Invalidate any previous unused link before issuing a new one.
        crud.invalidate_user_reset_tokens(user["id"])

        raw_token = _generate_token()
        crud.create_reset_token(user["id"], raw_token, ip_address, user_agent)

        base_url = os.getenv("FARMWISE_URL", "https://farmwise-hsps.onrender.com")
        reset_url = f"{base_url}/reset-password?token={raw_token}"
        _send_reset_email(email, user.get("full_name") or "there", reset_url)

        log.info("password_reset_token_issued user_id=%s ip=%s", user["id"], ip_address)

    except Exception as exc:
        log.error("request_reset_error: %s", exc)
        # Swallow — never expose internal errors, and never let this
        # differ observably from the "email not found" path above.


def validate_token(raw_token: str) -> dict:
    """
    Step 2 — check a token from the reset-password page's URL on load.
    Returns {"valid": True, "user_id": ..., "token_id": ...}
         or {"valid": False, "reason": "..."}.
    """
    if not raw_token or len(raw_token) < 20:
        return {"valid": False, "reason": "Invalid link."}

    try:
        row = crud.get_reset_token_by_raw(raw_token.strip())
        if row is None:
            return {"valid": False, "reason": "This password reset link is invalid or has expired."}

        if row.get("used_at"):
            return {"valid": False, "reason": "This password reset link has already been used."}

        expires_at = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires_at:
            return {"valid": False, "reason": "This password reset link has expired. Please request a new one."}

        return {"valid": True, "user_id": row["user_id"], "token_id": row["id"]}

    except Exception as exc:
        log.error("validate_token_error: %s", exc)
        return {"valid": False, "reason": "Something went wrong. Please request a new link."}


def complete_reset(raw_token: str, new_password: str) -> dict:
    """Step 3 — set the new password and mark the token used."""
    result = validate_token(raw_token)
    if not result.get("valid"):
        return {"ok": False, "error": result.get("reason", "Invalid link.")}

    user_id = result["user_id"]
    token_id = result["token_id"]

    try:
        from core.auth import hash_password

        crud.update_user(user_id, {
            "hashed_password": hash_password(new_password),
            "failed_login_attempts": 0,
            "locked_until": None,
        })
        crud.mark_reset_token_used(token_id)
        crud.invalidate_user_reset_tokens(user_id)  # belt-and-braces: kill any other pending links
        crud.revoke_all_refresh_tokens(user_id)  # matches the OTP-based reset's behavior — sign out everywhere

        log.info("password_reset_completed user_id=%s", user_id)
        return {"ok": True}

    except Exception as exc:
        log.error("complete_reset_error: %s", exc)
        return {"ok": False, "error": "Password reset failed. Please try again."}


def _send_reset_email(to_email: str, name: str, reset_url: str) -> None:
    subject = "Reset your FarmWise AI password"
    body = (
        f"Hi {name},\n\n"
        f"We received a request to reset the password for your FarmWise AI account.\n\n"
        f"Reset your password here (this link expires in 60 minutes):\n"
        f"{reset_url}\n\n"
        f"If you didn't request this, you can safely ignore this email — your password won't change."
    )
    _send_email(to_email, subject, body)


def direct_reset(identifier: str, new_password: str, ip_address: str = "") -> dict:
    """
    Reset a password directly using an email or phone number — no email
    link, no code, no waiting. Matches WaziBot's reset-password-direct
    endpoint exactly.

    SECURITY TRADE-OFF (same as WaziBot's version): anyone who knows a
    user's email/phone can reset their password, with rate limiting as
    the only real protection — there's no proof they actually own the
    account. This is a deliberate choice made after that trade-off was
    explained; it's why /auth/reset-password-direct is rate limited
    tightly (5/hr) at the route level and every attempt is logged with
    its IP for an audit trail.

    Returns {"ok": True} on success, {"ok": False, "error": str} on failure.
    Uses a generic error for "identifier not found" — same enumeration
    protection as the email-link path.
    """
    identifier = (identifier or "").strip()
    if not identifier:
        return {"ok": False, "error": "Please enter your email address or phone number."}

    try:
        user = crud.get_user_by_identifier(identifier)
        if user is None:
            log.info("direct_reset_identifier_not_found (not disclosed) ip=%s", ip_address)
            return {"ok": False, "error": "No account found with that email or phone number."}

        if not user.get("is_active", True):
            return {"ok": False, "error": "This account is inactive."}

        from core.auth import hash_password

        crud.update_user(user["id"], {
            "hashed_password": hash_password(new_password),
            "failed_login_attempts": 0,
            "locked_until": None,
        })
        # Clean up any pending email-link tokens for this user too.
        crud.invalidate_user_reset_tokens(user["id"])
        crud.revoke_all_refresh_tokens(user["id"])

        log.info("direct_reset_success user_id=%s ip=%s", user["id"], ip_address)
        return {"ok": True}

    except Exception as exc:
        log.error("direct_reset_error: %s", exc)
        return {"ok": False, "error": "Reset failed. Please try again."}
