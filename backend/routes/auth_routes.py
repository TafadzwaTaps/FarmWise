"""
routes/auth_routes.py — Authentication endpoints.

Routes: POST /auth/signup, POST /auth/login, POST /auth/refresh,
        POST /auth/logout, POST /auth/logout-all, GET /auth/me,
        POST /auth/password-reset/request, POST /auth/password-reset/confirm
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import jwt
from pydantic import BaseModel, field_validator

import crud
from core.auth import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
)
from services.security import check as _rate_check, RateLimitExceeded
from services.notification_service import send_otp
from routes._deps import log

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ── Request/response models ────────────────────────────────────────────────

class SignupRequest(BaseModel):
    full_name: str
    email: str | None = None
    phone_number: str | None = None
    password: str

    @field_validator("phone_number")
    @classmethod
    def require_email_or_phone(cls, v, info):
        if not v and not info.data.get("email"):
            raise ValueError("Either email or phone_number is required")
        return v


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
    if user is None or not verify_password(data.password, user["hashed_password"]):
        record_failed_login(ip, data.identifier)
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
    code = f"{__import__('secrets').randbelow(1_000_000):06d}"
    crud.create_otp(data.destination, code, purpose="password_reset")
    send_otp(data.destination, code)
    # Always 204 regardless of whether the account exists — avoids
    # leaking which emails/phones are registered.


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def password_reset_confirm(data: PasswordResetConfirm):
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
