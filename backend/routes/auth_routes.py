"""
routes/auth_routes.py — Authentication endpoints.

Routes: POST /auth/signup, POST /auth/login, POST /auth/refresh,
        POST /auth/logout, POST /auth/logout-all, GET /auth/me,
        POST /auth/password-reset/request, POST /auth/password-reset/confirm
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import jwt
from pydantic import BaseModel, model_validator

import crud
from core.auth import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
)
from services.security import check as _rate_check, RateLimitExceeded, check_password_strength
from services.notification_service import send_otp
from services import password_reset_service
from routes._deps import audit

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ── Request/response models ────────────────────────────────────────────────

class SignupRequest(BaseModel):
    full_name: str
    email: str | None = None
    phone_number: str | None = None
    password: str

    @model_validator(mode="after")
    def require_email_or_phone(self):
        # AUDIT.md — this was a @field_validator("phone_number"), which
        # Pydantic v2 only runs when phone_number is *explicitly* present
        # in the request payload (validate_default defaults to False for
        # field validators). Omitting BOTH email and phone_number entirely
        # skipped this check completely and sailed through to
        # crud.create_user with both as None — the users table has no
        # CHECK constraint requiring either, so this could silently create
        # an account with no way to ever log back in (login is by
        # identifier = email or phone). A model_validator(mode="after")
        # always runs against the fully-constructed model, regardless of
        # which fields were explicitly supplied.
        if not self.email and not self.phone_number:
            raise ValueError("Either email or phone_number is required")
        return self


class LoginRequest(BaseModel):
    identifier: str  # email or phone number
    password: str
    remember_me: bool = False
    device_label: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordResetRequest(BaseModel):
    destination: str


class PasswordResetConfirm(BaseModel):
    destination: str
    code: str
    new_password: str


class ValidateResetTokenRequest(BaseModel):
    token: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class DirectResetRequest(BaseModel):
    """Reset directly with your identifier — no email/link/code needed.
    See services/password_reset_service.direct_reset() for the security
    trade-off this accepts in exchange for not needing email delivery."""
    identifier: str
    new_password: str
    confirm_password: str


def _token_pair(user: dict, remember_me: bool = False, device_label: str | None = None) -> dict:
    claims = {"sub": user["id"], "role": user.get("global_role", "user")}

    access_token = create_access_token(claims)
    refresh_token = create_refresh_token(claims, remember_me=remember_me)

    decoded = jwt.get_unverified_claims(refresh_token)
    expires_at = datetime.fromtimestamp(decoded["exp"], tz=timezone.utc)

    crud.create_refresh_token_row(
        user_id=user["id"], jti=decoded["jti"], device_label=device_label,
        remember_me=remember_me, expires_at=expires_at.isoformat(),
    )
    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}


# ── Routes ───────────────────────────────────────────────────────────────

@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(data: SignupRequest, request: Request):
    _rate_check("signup", request, max_calls=10, window_seconds=3600)

    ok, reason = check_password_strength(data.password)
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, reason)

    if data.email and crud.get_user_by_email(data.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    if data.phone_number and crud.get_user_by_phone(data.phone_number):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this phone number already exists")

    user = crud.create_user(
        full_name=data.full_name,
        email=data.email,
        phone_number=data.phone_number,
        hashed_password=hash_password(data.password),
    )
    return {
        "id": user["id"], "full_name": user["full_name"], "email": user["email"],
        "phone_number": user["phone_number"], "is_email_verified": user["is_email_verified"],
        "is_phone_verified": user["is_phone_verified"], "created_at": user["created_at"],
    }


@router.post("/login")
def login(data: LoginRequest, request: Request):
    try:
        _rate_check("login", request, max_calls=20, window_seconds=300)
    except RateLimitExceeded:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts. Try again shortly.")

    ip = request.client.host if request.client else "unknown"

    from services.security import is_login_locked, record_failed_login, clear_failed_logins
    if is_login_locked(ip, data.identifier):
        raise HTTPException(status.HTTP_423_LOCKED, "Account temporarily locked due to repeated failed logins.")

    user = crud.get_user_by_identifier(data.identifier)

    # AUDIT.md: crud.is_locked/register_failed_login (DB-persisted, keyed by
    # account only) were fully built — matching the users.failed_login_attempts
    # / locked_until columns in the schema — but never actually called here.
    # The only lockout enforcement was the in-memory check above, which is
    # keyed by (ip, identifier): an attacker rotating source IPs (trivial —
    # different mobile networks, a botnet, a proxy) resets that counter on
    # every new IP and never gets locked out at all. The DB check below is
    # per-account regardless of IP, survives redeploys, and works across
    # multiple server instances if this app ever scales beyond one — kept
    # alongside the IP-based check rather than replacing it, since the
    # IP-based one still catches a single IP hammering many different
    # existing usernames, which an account-keyed check doesn't cover.
    if user is not None and crud.is_locked(user):
        raise HTTPException(status.HTTP_423_LOCKED, "Account temporarily locked due to repeated failed logins.")

    if user is None or not verify_password(data.password, user["hashed_password"]):
        record_failed_login(ip, data.identifier)
        if user is not None:
            crud.register_failed_login(user)
        if is_login_locked(ip, data.identifier):
            audit("login_locked", identifier=data.identifier, ip=ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    if not user.get("is_active", True):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is inactive")

    clear_failed_logins(ip, data.identifier)
    crud.clear_failed_logins(user["id"])

    return _token_pair(user, remember_me=data.remember_me, device_label=data.device_label)


@router.post("/refresh")
def refresh_token_endpoint(data: RefreshRequest):
    try:
        payload = decode_token(data.refresh_token)
    except HTTPException:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    if payload.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token type")

    jti = payload.get("jti")
    stored = crud.get_refresh_token_by_jti(jti) if jti else None
    if stored is None or stored["revoked"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token expired or revoked")

    expires_at = datetime.fromisoformat(stored["expires_at"].replace("Z", "+00:00"))
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token expired or revoked")

    user = crud.get_user_by_id(payload["sub"])
    if user is None or not user.get("is_active", True):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")

    # Rotate: revoke the used refresh token, issue a new pair.
    crud.revoke_refresh_token(jti)
    return _token_pair(user, remember_me=stored["remember_me"], device_label=stored["device_label"])


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(data: RefreshRequest):
    try:
        payload = decode_token(data.refresh_token)
    except HTTPException:
        return  # already invalid — logout is idempotent
    jti = payload.get("jti")
    if jti:
        crud.revoke_refresh_token(jti)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(user: dict = Depends(get_current_user)):
    crud.revoke_all_refresh_tokens(user["user_id"])


@router.post("/password-reset/request", status_code=status.HTTP_204_NO_CONTENT)
def password_reset_request(data: PasswordResetRequest, request: Request):
    _rate_check("password-reset", request, max_calls=5, window_seconds=3600)

    destination = data.destination.strip()
    if "@" in destination:
        # Email — send a reset LINK, not a code (see services/password_reset_service.py).
        ip = request.client.host if request.client else ""
        ua = request.headers.get("user-agent", "")[:250]
        password_reset_service.request_reset(destination, ip_address=ip, user_agent=ua)
    else:
        # Phone — unchanged OTP-code path.
        code = f"{__import__('secrets').randbelow(1_000_000):06d}"
        crud.create_otp(destination, code, purpose="password_reset")
        send_otp(destination, code)
    # Always 204 regardless of destination type or whether the account
    # exists — avoids leaking which emails/phones are registered.


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def password_reset_confirm(data: PasswordResetConfirm):
    ok, reason = check_password_strength(data.new_password)
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, reason)

    if not crud.verify_otp(data.destination, data.code, purpose="password_reset"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired code")
    user = crud.get_user_by_identifier(data.destination)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    crud.update_user(user["id"], {
        "hashed_password": hash_password(data.new_password),
        "failed_login_attempts": 0,
        "locked_until": None,
    })
    crud.revoke_all_refresh_tokens(user["id"])  # invalidate existing sessions on password change


@router.post("/validate-reset-token")
def validate_reset_token(data: ValidateResetTokenRequest):
    """Called by reset-password.html on load, to show a valid/expired
    state before the user even types a new password."""
    result = password_reset_service.validate_token(data.token)
    return {"valid": result.get("valid", False), "reason": result.get("reason", "")}


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(data: ResetPasswordRequest, request: Request):
    """Completes a link-based (email) reset — what reset-password.html
    calls on submit. Separate from /password-reset/confirm above, which
    stays exactly as it was for the phone/OTP path."""
    _rate_check("reset-password", request, max_calls=10, window_seconds=3600)

    ok, reason = check_password_strength(data.new_password)
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, reason)

    result = password_reset_service.complete_reset(data.token, data.new_password)
    if not result.get("ok"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, result.get("error", "Password reset failed."))


@router.post("/reset-password-direct", status_code=status.HTTP_204_NO_CONTENT)
def reset_password_direct(data: DirectResetRequest, request: Request):
    """Reset directly using an email or phone number — no email/link/code
    required. Matches WaziBot's reset-password-direct endpoint. Tightly
    rate limited since this is the only real protection against someone
    resetting an account they don't own."""
    _rate_check("direct-reset", request, max_calls=5, window_seconds=3600)

    if data.new_password != data.confirm_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Passwords do not match.")

    ok, reason = check_password_strength(data.new_password)
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, reason)

    ip = request.client.host if request.client else ""
    result = password_reset_service.direct_reset(data.identifier, data.new_password, ip_address=ip)
    if not result.get("ok"):
        audit("direct_reset_failed", identifier=data.identifier, ip=ip, reason=result.get("error"))
        raise HTTPException(status.HTTP_400_BAD_REQUEST, result.get("error", "Reset failed."))
    audit("direct_reset_succeeded", identifier=data.identifier, ip=ip)


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    full_user = crud.get_user_by_id(user["user_id"])
    if full_user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return {
        "id": full_user["id"], "full_name": full_user["full_name"], "email": full_user["email"],
        "phone_number": full_user["phone_number"], "is_email_verified": full_user["is_email_verified"],
        "is_phone_verified": full_user["is_phone_verified"], "created_at": full_user["created_at"],
    }
