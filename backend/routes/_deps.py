"""
routes/_deps.py — Shared dependencies for all route modules.

Every router imports the shared logger from here rather than each
creating its own, matching WaziBot's convention.
"""

import logging

log = logging.getLogger("farmwise")
