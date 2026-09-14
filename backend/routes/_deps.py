"""
routes/_deps.py — Shared dependencies for all route modules.

Every router imports the shared logger from here rather than each
creating its own, matching WaziBot's convention.
"""

import logging

log = logging.getLogger("farmwise")
_audit_log = logging.getLogger("farmwise.audit")


def audit(event: str, **fields) -> None:
    """Structured audit trail for sensitive actions (farm/worker deletion,
    password resets, login lockouts, etc).

    Deliberately log-based rather than a new database table: a persisted
    audit_log table is the more complete answer long-term, but it needs
    its own schema migration + rollback plan + retention policy, which is
    real design work (see AUDIT.md FWA-006 for the same reasoning applied
    to the profit model). Render captures and retains stdout logs, so
    this gives real, searchable "who did what, when" coverage today
    without an unreviewed schema change — upgrade to a table later by
    swapping this function's body, without touching any call site.
    """
    kv = " ".join(f"{k}={v}" for k, v in fields.items())
    _audit_log.info("AUDIT event=%s %s", event, kv)
