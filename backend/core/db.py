"""
core/db.py — Supabase client singleton.

All queries go through supabase-py (postgrest under the hood) — no ORM,
no Alembic. Tables were created once via the verified farmwise_schema.sql
(originally generated from Alembic migrations); from here on, schema
changes are made directly in Supabase (SQL editor or dashboard), the
same way WaziBot manages its schema.

Required environment variables (Render → Environment, or .env locally):
    SUPABASE_URL   https://<project-ref>.supabase.co
    SUPABASE_KEY   service_role key  (NOT the anon key — bypasses RLS)

Find them in:
    Supabase Dashboard → Project Settings → API
"""

import os
import re
import logging
import sys

from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("farmwise.db")


# ── Startup helpers ────────────────────────────────────────────────────────

def _fatal(msg: str) -> None:
    log.critical("❌  STARTUP FAILURE — core/db.py\n%s", msg)
    sys.exit(1)


def _validate_supabase_url(url: str) -> None:
    if not url:
        _fatal(
            "SUPABASE_URL is not set.\n"
            "\n"
            "  ➜  Add it in Render → Your Service → Environment → Add Variable\n"
            "       Key  : SUPABASE_URL\n"
            "       Value: https://<your-project-ref>.supabase.co\n"
            "\n"
            "  ➜  Find your URL in:\n"
            "       Supabase Dashboard → Project Settings → API → Project URL"
        )
    if "xxxx" in url.lower():
        _fatal(f"SUPABASE_URL still contains the placeholder value: {url!r}")
    if not url.startswith("https://"):
        _fatal(f"SUPABASE_URL must start with 'https://'. Got: {url!r}")
    if not re.search(r"https://[a-z0-9]+\.supabase\.co", url.rstrip("/")):
        _fatal(
            f"SUPABASE_URL doesn't look like a valid Supabase URL: {url!r}\n"
            "  ➜  Expected format: https://<project-ref>.supabase.co"
        )


def _validate_supabase_key(key: str) -> None:
    if not key:
        _fatal(
            "SUPABASE_KEY is not set.\n"
            "\n"
            "  ➜  Add it in Render → Your Service → Environment → Add Variable\n"
            "       Key  : SUPABASE_KEY\n"
            "       Value: <your service_role JWT>\n"
            "\n"
            "  ⚠   Use the service_role key, NOT the anon key.\n"
            "      The service_role key bypasses Row Level Security."
        )
    if not key.startswith("eyJ"):
        _fatal(
            f"SUPABASE_KEY does not look like a valid JWT.\n"
            f"  Got prefix: {key[:12]!r}  (length: {len(key)})\n"
            "  ➜  A valid Supabase key always starts with 'eyJ'"
        )


# ── Initialisation ───────────────────────────────────────────────────────

def _init() -> Client:
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()

    _validate_supabase_url(url)
    _validate_supabase_key(key)

    try:
        client = create_client(url, key)
    except Exception as exc:
        _fatal(
            f"Supabase client creation failed: {exc}\n"
            f"\n"
            f"  URL : {url[:60]}\n"
            f"  Key : {key[:12]}… (length {len(key)})\n"
            "\n"
            "  ➜  Verify both values in Render → Environment are correct and saved."
        )

    log.info("🟢 Supabase client initialised  url=%s…", url[:50])
    return client


# One client for the whole process — thread-safe for reads.
supabase: Client = _init()
