"""
tests/conftest.py — shared fixtures for the backend test suite.

Sets required env vars *before* importing `main` (core/db.py and
core/auth.py validate/fail-fast at import time — see AUDIT.md FWA-001).
Uses a syntactically-valid but fake Supabase service-role key; no test
here ever touches the real `supabase` client — every test that needs
data mocks the specific `crud.*` function it depends on directly, which
is what makes these fast, offline, and safe to run anywhere (no live
Supabase project required).
"""

import base64
import json
import os

import pytest


def _fake_jwt() -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64({'role': 'service_role'})}.fakesig"


os.environ.setdefault("SUPABASE_URL", "https://fakeprojectref.supabase.co")
os.environ.setdefault("SUPABASE_KEY", _fake_jwt())
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use-only-" * 2)
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("CORS_ORIGINS", "")

import main  # noqa: E402  (must come after the env vars above are set)
from fastapi.testclient import TestClient  # noqa: E402
from core.auth import create_access_token  # noqa: E402
import crud.farms as farms_crud  # noqa: E402


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def make_token():
    """make_token(user_id, global_role='user') -> a real, validly-signed
    access token for that user, exactly as /auth/login would issue one."""
    def _make(user_id: str, global_role: str = "user") -> str:
        return create_access_token({"sub": user_id, "role": global_role})
    return _make


@pytest.fixture
def membership_store(monkeypatch):
    """An in-memory stand-in for the farm_members table, keyed by
    (farm_id, user_id). Tests populate it directly; require_farm_role()
    (core/auth.py) calls crud.farms.get_membership(), which this patches
    to read from here instead of hitting Supabase — so these tests
    exercise the REAL authorization dependency, only faking the DB read
    underneath it."""

    class _Store(dict):
        def add(self, farm_id: str, user_id: str, role: str):
            self[(farm_id, user_id)] = {"farm_id": farm_id, "user_id": user_id, "role": role}

    store = _Store()

    def _fake_get_membership(farm_id: str, user_id: str):
        return store.get((farm_id, user_id))

    monkeypatch.setattr(farms_crud, "get_membership", _fake_get_membership)
    # require_farm_role does `from crud.farms import get_membership` INSIDE
    # the function body on every call, so patching the module attribute
    # above is sufficient — no separate patch of core.auth needed.
    return store
