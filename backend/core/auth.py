"""
core/auth.py — JWT authentication helpers.

Access tokens carry role + farm claims directly (sub=user_id, farm_ids=[...]),
so get_current_user() trusts the token and doesn't hit the database on
every request — the same trade-off WaziBot makes. Anything that needs the
full user row (e.g. /auth/me) calls crud.get_user_by_id() explicitly.
"""

import os
import uuid
from datetime import datetime, timedelta

from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "change_this_in_production_use_env_file")
_WEAK_KEYS = {"change_this_in_production_use_env_file", "secret", "dev", "", "mysecret"}
if SECRET_KEY in _WEAK_KEYS:
    import logging as _kal
    _kal.getLogger("farmwise.security").critical(
        "SECRET_KEY is unset or uses an insecure default value. "
        "Set SECRET_KEY in Render env vars. "
        'Generate: python -c "import secrets; print(secrets.token_hex(32))"'
    )
ALGORITHM = "HS256"

ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_MINUTES = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30")) * 24 * 60
REMEMBER_ME_REFRESH_TOKEN_EXPIRE_MINUTES = int(os.getenv("REMEMBER_ME_REFRESH_TOKEN_EXPIRE_DAYS", "90")) * 24 * 60


class _LoggingOAuth2PasswordBearer(OAuth2PasswordBearer):
    """Logs *why* a request was rejected before raising 401 — makes auth
    failures debuggable from Render logs instead of a bare 'Not authenticated'."""

    async def __call__(self, request: Request):
        auth_header = request.headers.get("Authorization")
        try:
            return await super().__call__(request)
        except HTTPException as exc:
            import logging as _al
            _al.getLogger("farmwise.auth").warning(
                "AUTH FAILED: reason=missing_or_malformed_auth_header  path=%s  method=%s  header_present=%s",
                request.url.path, request.method, bool(auth_header),
            )
            raise


oauth2_scheme = _LoggingOAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


# ── Passwords ──────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """Hash a password with bcrypt directly — no passlib."""
    import bcrypt as _bcrypt
    pw_bytes = plain.encode("utf-8")[:72]  # bcrypt max is 72 bytes
    return _bcrypt.hashpw(pw_bytes, _bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, stored: str) -> bool:
    if not stored:
        return False
    try:
        import bcrypt as _bcrypt
        return _bcrypt.checkpw(plain.encode("utf-8")[:72], stored.encode("utf-8"))
    except Exception:
        return False


# ── Tokens ─────────────────────────────────────────────────────────────────

def create_access_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload["type"] = "access"
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict, remember_me: bool = False) -> str:
    """jti is embedded in the token AND expected to be persisted server-side
    (see crud.create_refresh_token_row) — that's what makes multi-device
    login, per-device logout, and instant revocation possible."""
    minutes = REMEMBER_ME_REFRESH_TOKEN_EXPIRE_MINUTES if remember_me else REFRESH_TOKEN_EXPIRE_MINUTES
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(minutes=minutes)
    payload["type"] = "refresh"
    payload["jti"] = str(uuid.uuid4())
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalid or expired. Please log in again.",
        )


# ── Dependencies ───────────────────────────────────────────────────────────

def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """Returns the decoded access-token payload: {user_id, role, farm_ids, jti, ...}."""
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token type")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token payload")
    return {
        "user_id": user_id,
        "role": payload.get("role", "user"),
        "farm_roles": payload.get("farm_roles", {}),  # {farm_id: role}
    }


def require_farm_role(*allowed_roles: str):
    """
    Dependency factory: the caller must belong to the farm given as the
    `farm_id` path param, optionally restricted to specific roles.
    Usage: Depends(require_farm_role("farmer", "farm_manager"))
    Usage (any role): Depends(require_farm_role())
    """

    def _check(farm_id: str, user: dict = Depends(get_current_user)) -> dict:
        from crud.farms import get_membership

        membership = get_membership(farm_id, user["user_id"])
        if membership is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of this farm")
        if allowed_roles and membership["role"] not in allowed_roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role for this action")
        return membership

    return _check
