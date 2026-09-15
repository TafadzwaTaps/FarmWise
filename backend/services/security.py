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


# ── Idempotency (duplicate-submission protection) ─────────────────────────
# AUDIT.md FWA-007: a farmer double-tapping "Record Sale" on a slow mobile
# connection (or a client's own retry-on-timeout logic) can otherwise
# create two identical records for one real-world event. Client sends an
# Idempotency-Key header (any client-generated string, typically a UUID
# per form submission); reusing that key within the window is rejected as
# a duplicate rather than creating a second record.
#
# Same in-process, no-Redis trade-off as the rate limiter above (resets on
# redeploy, not shared across multiple server instances) — good enough to
# catch the common case, not a cross-instance guarantee. Rejects the
# duplicate rather than transparently replaying the original response;
# simpler and still solves the actual problem (no duplicate record
# created), at the cost of the retry needing to treat 409 as "already
# done" rather than getting the original data back.

class DuplicateSubmission(Exception):
    pass


_idempotency_lock = threading.Lock()
_idempotency_seen: dict[str, float] = {}
_IDEMPOTENCY_DEFAULT_TTL = 300  # 5 minutes — long enough to catch a retry, short enough not to leak memory


def check_idempotency_key(scope: str, key: str | None, ttl_seconds: int = _IDEMPOTENCY_DEFAULT_TTL) -> None:
    """No-op if key is None — the header is optional, so callers who don't
    send one get today's existing behavior exactly. Raises
    DuplicateSubmission if (scope, key) was already seen within ttl_seconds."""
    if not key:
        return
    full_key = f"{scope}:{key}"
    now = time.monotonic()
    with _idempotency_lock:
        # Opportunistic cleanup so this dict doesn't grow unbounded — cheap
        # relative to how rarely this function is called (once per write).
        if len(_idempotency_seen) > 10_000:
            cutoff = now - ttl_seconds
            for k in [k for k, t in _idempotency_seen.items() if t < cutoff]:
                del _idempotency_seen[k]
        seen_at = _idempotency_seen.get(full_key)
        if seen_at is not None and (now - seen_at) < ttl_seconds:
            raise DuplicateSubmission(f"Duplicate submission for key {key!r}")
        _idempotency_seen[full_key] = now


def clear_failed_logins(ip: str, username: str) -> None:
    _failed_logins.pop(_key(ip, username), None)


# ── Password strength ─────────────────────────────────────────────────────

def check_password_strength(password: str) -> tuple[bool, str]:
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return False, "Password should include both letters and numbers."
    # bcrypt (core/auth.py hash_password/verify_password) only looks at the
    # first 72 BYTES of a password and silently ignores the rest. Without
    # this check, two different passwords sharing the same 72-byte prefix
    # would both work, and a user typing/pasting a long passphrase would
    # have no idea part of it is being ignored. Reject up front instead —
    # loud and clear beats silently accepting less security than the user
    # thinks they set.
    if len(password.encode("utf-8")) > 72:
        return False, "Password is too long (max 72 bytes — roughly 72 characters for plain text)."
    return True, ""
