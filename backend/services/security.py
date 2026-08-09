"""
services/security.py — Rate limiting and password strength checks.

Pure Python, in-process, no Redis — same trade-off WaziBot makes:
resets on redeploy (acceptable for Render free/starter tiers), never
crashes the request path.

Usage:
    from services.security import check, RateLimitExceeded
    check("login", request, max_calls=5, window_seconds=60)
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict, deque

log = logging.getLogger("farmwise.security")


class RateLimitExceeded(Exception):
    def __init__(self, limit_name: str, retry_after: int = 60):
        self.limit_name = limit_name
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded for {limit_name}")


# ── Rate limiter — sliding window, in-process ─────────────────────────────

_rate_lock = threading.Lock()
_rate_store: dict[str, deque] = defaultdict(deque)


def _client_ip(request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return getattr(request.client, "host", "unknown")


def check(name: str, request, max_calls: int = 60, window_seconds: int = 60) -> None:
    """Raises RateLimitExceeded if this IP has exceeded max_calls within window_seconds."""
    key = f"{name}:{_client_ip(request)}"
    now = time.monotonic()
    with _rate_lock:
        bucket = _rate_store[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= max_calls:
            raise RateLimitExceeded(name, retry_after=window_seconds)
        bucket.append(now)


# ── Login lockout tracking (per IP + username) ────────────────────────────

_failed_logins: dict[str, list[float]] = defaultdict(list)
_LOCKOUT_WINDOW_SECONDS = 15 * 60
_LOCKOUT_THRESHOLD = 5


def _key(ip: str, username: str) -> str:
    return f"{ip}:{username.lower()}"


def record_failed_login(ip: str, username: str) -> None:
    now = time.monotonic()
    bucket = _failed_logins[_key(ip, username)]
    bucket.append(now)
    _failed_logins[_key(ip, username)] = [t for t in bucket if now - t < _LOCKOUT_WINDOW_SECONDS]


def is_login_locked(ip: str, username: str) -> bool:
    now = time.monotonic()
    bucket = _failed_logins.get(_key(ip, username), [])
    recent = [t for t in bucket if now - t < _LOCKOUT_WINDOW_SECONDS]
    return len(recent) >= _LOCKOUT_THRESHOLD


def clear_failed_logins(ip: str, username: str) -> None:
    _failed_logins.pop(_key(ip, username), None)


# ── Password strength ─────────────────────────────────────────────────────

def check_password_strength(password: str) -> tuple[bool, str]:
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return False, "Password should include both letters and numbers."
    return True, ""
