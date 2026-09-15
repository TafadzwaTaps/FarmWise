"""
tests/test_auth.py — Phase 8: authentication coverage the original brief's
Phase 8 checklist calls out explicitly: signup, login, invalid passwords,
JWT expiry, and account lockout — including a regression test for the
lockout fix made in this pass (see AUDIT.md, Phase 8 write-up).
"""

from datetime import datetime, timedelta
from unittest.mock import patch

from jose import jwt

import core.auth as auth_module


def _make_user(**overrides):
    user = {
        "id": "u1", "user_id": "u1", "full_name": "Test User", "email": "test@example.com",
        "phone_number": None, "hashed_password": "irrelevant-hash",
        "is_active": True, "failed_login_attempts": 0, "locked_until": None,
        "global_role": "user", "is_email_verified": False, "is_phone_verified": False,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    user.update(overrides)
    return user


# ── Signup ────────────────────────────────────────────────────────────────

def test_signup_success(client):
    with patch("routes.auth_routes.crud.get_user_by_email", return_value=None), \
         patch("routes.auth_routes.crud.create_user", return_value=_make_user(created_at="t", is_email_verified=False, is_phone_verified=False)):
        r = client.post("/api/v1/auth/signup", json={
            "full_name": "Test User", "email": "test@example.com", "password": "Str0ngPass1",
        })
    assert r.status_code == 201
    assert r.json()["email"] == "test@example.com"


def test_signup_rejects_weak_password(client):
    r = client.post("/api/v1/auth/signup", json={
        "full_name": "Test User", "email": "weak@example.com", "password": "a",
    })
    assert r.status_code == 400


def test_signup_rejects_duplicate_email(client):
    with patch("routes.auth_routes.crud.get_user_by_email", return_value=_make_user()):
        r = client.post("/api/v1/auth/signup", json={
            "full_name": "Test User", "email": "test@example.com", "password": "Str0ngPass1",
        })
    assert r.status_code == 409


def test_signup_requires_email_or_phone(client):
    r = client.post("/api/v1/auth/signup", json={"full_name": "Test User", "password": "Str0ngPass1"})
    assert r.status_code == 422  # Pydantic validator rejects it before the route body even runs


# ── Login ─────────────────────────────────────────────────────────────────

def test_login_success(client):
    user = _make_user()
    with patch("routes.auth_routes.crud.get_user_by_identifier", return_value=user), \
         patch("routes.auth_routes.verify_password", return_value=True), \
         patch("routes.auth_routes.crud.clear_failed_logins"), \
         patch("routes.auth_routes.crud.create_refresh_token_row", return_value={"id": "rt1"}):
        r = client.post("/api/v1/auth/login", json={"identifier": "test@example.com", "password": "correct"})
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body and "refresh_token" in body


def test_login_wrong_password_rejected(client):
    user = _make_user()
    with patch("routes.auth_routes.crud.get_user_by_identifier", return_value=user), \
         patch("routes.auth_routes.verify_password", return_value=False), \
         patch("routes.auth_routes.crud.register_failed_login") as mocked_register:
        r = client.post("/api/v1/auth/login", json={"identifier": "test@example.com", "password": "wrong"})
    assert r.status_code == 401
    mocked_register.assert_called_once()  # the DB-backed counter must actually increment


def test_login_unknown_identifier_rejected(client):
    with patch("routes.auth_routes.crud.get_user_by_identifier", return_value=None):
        r = client.post("/api/v1/auth/login", json={"identifier": "nobody@example.com", "password": "x"})
    assert r.status_code == 401


def test_login_inactive_account_rejected(client):
    user = _make_user(is_active=False)
    with patch("routes.auth_routes.crud.get_user_by_identifier", return_value=user), \
         patch("routes.auth_routes.verify_password", return_value=True):
        r = client.post("/api/v1/auth/login", json={"identifier": "test@example.com", "password": "correct"})
    assert r.status_code == 403


# ── Account lockout — the DB-backed fix made in Phase 8 ──────────────────

def test_login_blocked_when_db_account_is_locked_even_from_a_fresh_ip(client):
    """This is the actual regression test for the fix: an account that's
    locked at the DB level (crud.is_locked) must be rejected even though
    the in-memory, IP-keyed lockout (services.security.is_login_locked)
    has never seen this IP before and would otherwise allow the attempt
    through — simulating an attacker who rotated to a new source IP."""
    user = _make_user(locked_until="2099-01-01T00:00:00+00:00")
    with patch("routes.auth_routes.crud.get_user_by_identifier", return_value=user), \
         patch("services.security.is_login_locked", return_value=False), \
         patch("routes.auth_routes.verify_password") as mocked_verify:
        r = client.post("/api/v1/auth/login", json={"identifier": "test@example.com", "password": "correct"})
    assert r.status_code == 423
    mocked_verify.assert_not_called()  # locked out before password is even checked


def test_failed_login_increments_the_db_backed_counter(client):
    """Without this, crud.register_failed_login (matching the schema's
    failed_login_attempts/locked_until columns) is dead code and the only
    real lockout is IP-rotation-bypassable — see AUDIT.md."""
    user = _make_user()
    with patch("routes.auth_routes.crud.get_user_by_identifier", return_value=user), \
         patch("routes.auth_routes.verify_password", return_value=False), \
         patch("routes.auth_routes.crud.register_failed_login") as mocked:
        client.post("/api/v1/auth/login", json={"identifier": "test@example.com", "password": "wrong"})
    mocked.assert_called_once_with(user)


def test_successful_login_clears_the_db_backed_counter(client):
    user = _make_user()
    with patch("routes.auth_routes.crud.get_user_by_identifier", return_value=user), \
         patch("routes.auth_routes.verify_password", return_value=True), \
         patch("routes.auth_routes.crud.clear_failed_logins") as mocked_clear, \
         patch("routes.auth_routes.crud.create_refresh_token_row", return_value={"id": "rt1"}):
        r = client.post("/api/v1/auth/login", json={"identifier": "test@example.com", "password": "correct"})
    assert r.status_code == 200
    mocked_clear.assert_called_once_with("u1")


# ── JWT expiry ────────────────────────────────────────────────────────────

def test_expired_access_token_rejected(client):
    expired_payload = {
        "sub": "u1", "role": "user", "type": "access",
        "exp": datetime.utcnow() - timedelta(minutes=5),  # already expired
    }
    expired_token = jwt.encode(expired_payload, auth_module.SECRET_KEY, algorithm=auth_module.ALGORITHM)

    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert r.status_code == 401


def test_valid_access_token_accepted(client):
    valid_token = auth_module.create_access_token({"sub": "u1", "role": "user"})
    with patch("routes.auth_routes.crud.get_user_by_id", return_value=_make_user()):
        r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {valid_token}"})
    assert r.status_code == 200


def test_malformed_token_rejected(client):
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_refresh_token_used_as_access_token_rejected(client):
    """type confusion — a refresh token must not work where an access
    token is expected, even though both are valid JWTs signed with the
    same key."""
    refresh_like = auth_module.create_access_token({"sub": "u1", "role": "user"})
    # Force it to look like a refresh token the way create_refresh_token does.
    import jose.jwt as _jwt
    payload = auth_module.decode_token(refresh_like)
    payload["type"] = "refresh"
    forged = _jwt.encode(payload, auth_module.SECRET_KEY, algorithm=auth_module.ALGORITHM)

    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401
