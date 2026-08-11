"""
main.py — FarmWise AI API bootstrap.

This file:
  1. Sets up sys.path
  2. Creates the FastAPI app + middleware
  3. Registers all routers
  4. Registers global exception handlers

All route logic lives in routes/. All data access lives in crud/
(direct Supabase queries — no ORM, no Alembic).
"""

import logging
import os

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

# ── sys.path setup ──────────────────────────────────────────────────────
import sys as _sys, os as _os
_BACKEND = _os.path.dirname(_os.path.abspath(__file__))
if _BACKEND not in _sys.path:
    _sys.path.insert(0, _BACKEND)

load_dotenv()

# ── Static dir ───────────────────────────────────────────────────────────
# The frontend lives as a sibling of backend/ — project_root/frontend/ —
# not nested inside backend/. It's still served by this same app (one
# Render Web Service, one URL), just from a folder named "frontend"
# rather than "static". The URL path stays /static regardless of what
# the folder is actually called on disk — app.mount() below controls
# that, not the directory name.
_STATIC_CANDIDATES = [
    _os.path.join(_BACKEND, "..", "frontend"),   # project_root/frontend  (your actual layout)
    _os.path.join(_BACKEND, "frontend"),          # backend/frontend
    _os.path.join(_BACKEND, "static"),             # backend/static
    _os.path.join(_BACKEND, "..", "static"),        # project_root/static
]
STATIC_DIR = next(
    (_os.path.abspath(p) for p in _STATIC_CANDIDATES if _os.path.isdir(p)),
    _os.path.abspath(_os.path.join(_BACKEND, "..", "frontend")),
)

# ── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("farmwise")
log.info("📁 Static dir: %s", STATIC_DIR)

from routes.auth_routes import router as auth_router
from routes.farm_routes import router as farm_router
from routes.animal_routes import router as animal_router
from routes.feed_routes import router as feed_router
from routes.finance_routes import router as finance_router
from routes.inventory_routes import router as inventory_router
from routes.dashboard_routes import router as dashboard_router
from routes.worker_routes import router as worker_router
from routes.assistant_routes import router as assistant_router
from services.security import RateLimitExceeded

APP_NAME = os.getenv("APP_NAME", "FarmWise AI")
APP_ENV = os.getenv("APP_ENV", "development")
API_V1_PREFIX = os.getenv("API_V1_PREFIX", "/api/v1")
IS_PRODUCTION = APP_ENV == "production"

CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

app = FastAPI(
    title=APP_NAME,
    version="2.0.0",
    docs_url="/docs" if not IS_PRODUCTION else None,
    redoc_url="/redoc" if not IS_PRODUCTION else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


app.add_middleware(SecurityHeadersMiddleware)


# ── Frontend (served from the same app — no separate deployment) ─────────

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _html(name: str) -> FileResponse:
    path = _os.path.join(STATIC_DIR, name)
    if not _os.path.exists(path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{name} not found")
    return FileResponse(path)


@app.get("/", include_in_schema=False)
def landing_page():
    return _html("landing.html")


@app.get("/login", include_in_schema=False)
def login_page():
    return _html("login.html")


@app.get("/signup", include_in_schema=False)
def signup_page():
    return _html("signup.html")


@app.get("/forgot-password", include_in_schema=False)
def forgot_password_page():
    return _html("forgot-password.html")


@app.get("/reset-password", include_in_schema=False)
def reset_password_page():
    return _html("reset-password.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard_page():
    return _html("dashboard.html")


@app.get("/animals", include_in_schema=False)
def animals_page():
    return _html("animals.html")


@app.get("/finance", include_in_schema=False)
def finance_page():
    return _html("finance.html")


@app.get("/feed", include_in_schema=False)
def feed_page():
    return _html("feed.html")


@app.get("/inventory", include_in_schema=False)
def inventory_page():
    return _html("inventory.html")


@app.get("/workers", include_in_schema=False)
def workers_page():
    return _html("workers.html")


@app.get("/assistant", include_in_schema=False)
def assistant_page():
    return _html("assistant.html")


@app.get("/settings", include_in_schema=False)
def settings_page():
    return _html("settings.html")


# ── Exception handlers ──────────────────────────────────────────────────

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"message": exc.detail, "code": exc.status_code}},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": {"message": "Validation failed", "details": exc.errors()}},
    )


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exception_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"error": {"message": f"Too many requests for {exc.limit_name}. Try again shortly."}},
        headers={"Retry-After": str(exc.retry_after)},
    )


@app.exception_handler(httpx.TransportError)
async def transport_error_handler(request: Request, exc: httpx.TransportError):
    # A Supabase connection drop that survived the transport-level retry
    # (see core/db.py) — rare, but worth a specific 503 rather than a
    # generic 500, since the honest answer is "try again", not "something's
    # broken".
    log.warning("supabase_transport_error path=%s error=%s", request.url, exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"error": {"message": "Temporarily unable to reach the database. Please try again."}},
        headers={"Retry-After": "2"},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak internals (stack traces, SQL) to the client.
    log.error("unhandled_exception path=%s error=%s", request.url, exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": {"message": "An unexpected error occurred"}},
    )


# ── Routes ───────────────────────────────────────────────────────────────

app.include_router(auth_router, prefix=API_V1_PREFIX)
app.include_router(farm_router, prefix=API_V1_PREFIX)
app.include_router(animal_router, prefix=API_V1_PREFIX)
app.include_router(feed_router, prefix=API_V1_PREFIX)
app.include_router(finance_router, prefix=API_V1_PREFIX)
app.include_router(inventory_router, prefix=API_V1_PREFIX)
app.include_router(dashboard_router, prefix=API_V1_PREFIX)
app.include_router(worker_router, prefix=API_V1_PREFIX)
app.include_router(assistant_router, prefix=API_V1_PREFIX)
# Next up (not yet built): calendar, reports, admin —
# follow the same routes/<domain>_routes.py + crud/<domain>.py pattern.


@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "ok", "app": APP_NAME, "env": APP_ENV}


@app.on_event("startup")
async def on_startup():
    log.info("🚀 %s starting  env=%s", APP_NAME, APP_ENV)
