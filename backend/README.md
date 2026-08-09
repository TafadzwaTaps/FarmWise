# FarmWise AI — Backend (WaziBot-style rewrite)

This backend has been restructured to match the [[wazibot]] backend's
structure and coding style: synchronous route handlers, a `crud/` layer
that talks to Supabase directly via `supabase-py` (no ORM), no Alembic,
and the same `core/` / `routes/` / `services/` folder layout.

## What changed from the previous version

| | Before | Now (this version) |
|---|---|---|
| DB access | Async SQLAlchemy 2 + repository pattern | Sync `supabase-py` client, dict in/out, `crud/*.py` |
| Migrations | Alembic (`alembic upgrade head`) | None — schema is managed directly in Supabase, same as WaziBot |
| Route handlers | `async def` | `def` (sync — FastAPI runs these in a threadpool) |
| Folder layout | `app/{core,models,schemas,repositories,services,api/v1}` | `{core,crud,routes,services,utils}` at the repo root |
| Env vars | `DATABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | `SUPABASE_URL`, `SUPABASE_KEY` (matches WaziBot's `core/db.py`) |
| Tests | `pytest` + async test client (12 tests) | Not ported — WaziBot's backend has no test suite either. Reintroducing tests here would mean mocking the Supabase client; flag if you want that added back. |

**What did NOT change:** every endpoint path, request/response field name,
and business rule (account lockout, refresh-token rotation + revocation,
OTP-based password reset, atomic mortality/sale decrements, delta-based
inventory adjustment, feed-cost and profit/loss aggregation) — the
`farmwise-web-phase1` frontend package works against this backend
unmodified. Refresh-token revocation was kept (via the `refresh_tokens`
table) rather than switched to WaziBot's fully-stateless refresh tokens,
since that's a real security feature worth keeping.

## Structure

```
main.py                 # bootstrap: CORS, security headers, exception handlers, routers
core/
  db.py                  # Supabase client singleton (with startup validation)
  auth.py                 # password hashing, JWT, get_current_user, require_farm_role
crud/
  __init__.py              # re-export layer — `import crud; crud.create_user(...)`
  _helpers.py
  users.py                  # users, refresh_tokens, otp_codes
  farms.py                   # farms, farm_members
  animals.py                  # animal_batches, mortality_records, medication_records
  finance.py                   # feed_purchases/consumption, sales, expenses, income
  inventory.py                  # inventory_items
routes/
  _deps.py                # shared logger
  auth_routes.py
  farm_routes.py
  animal_routes.py
  feed_routes.py
  finance_routes.py
  inventory_routes.py
services/
  security.py             # rate limiting, login lockout, password strength
  notification_service.py  # OTP dispatch (email/SMS) — stub, wire a real provider
utils/                    # reserved for shared helpers as the codebase grows
```

## Setup

```bash
cp .env.example .env
pip install -r requirements.txt
```

### Database

Tables already exist in Supabase if you ran the previous version's
`alembic upgrade head` against it — this version reads/writes the exact
same tables and columns, just through `supabase-py` instead of the ORM.

Starting from a blank Supabase project instead? Run the SQL from the
previously-generated `farmwise_schema.sql` once in the Supabase SQL editor
(Project → SQL Editor) to create all tables. There's no migration tool
going forward — schema changes from here are made directly in Supabase,
matching how WaziBot manages its schema.

Get your Supabase URL and **service_role** key from
Project Settings → API, and set `SUPABASE_URL` / `SUPABASE_KEY` in `.env`.

### Run locally

```bash
uvicorn main:app --reload
# → http://localhost:8000/docs
```

### Deploying

Same as before: Render, with `SUPABASE_URL`, `SUPABASE_KEY`, `SECRET_KEY`,
and `CORS_ORIGINS` set in the environment. No `Dockerfile` or `render.yaml`
changes are needed beyond removing any Alembic migration step from the
build/start command, since there isn't one anymore.

## Endpoints (unchanged paths)

```
POST /api/v1/auth/signup
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
POST /api/v1/auth/logout-all
GET  /api/v1/auth/me
POST /api/v1/auth/password-reset/request
POST /api/v1/auth/password-reset/confirm

POST/GET/PATCH/DELETE /api/v1/farms[/{farm_id}]
GET  /api/v1/farms/{farm_id}/members

POST/GET /api/v1/farms/{farm_id}/animals/batches[/{batch_id}]
POST/GET /api/v1/farms/{farm_id}/animals/batches/{batch_id}/mortality
POST/GET /api/v1/farms/{farm_id}/animals/batches/{batch_id}/medication

POST/GET /api/v1/farms/{farm_id}/feed/purchases
POST/GET /api/v1/farms/{farm_id}/feed/consumption
GET      /api/v1/farms/{farm_id}/feed/cost-summary

POST/GET /api/v1/farms/{farm_id}/sales
POST/GET /api/v1/farms/{farm_id}/expenses
POST/GET /api/v1/farms/{farm_id}/income
GET      /api/v1/farms/{farm_id}/finance-summary

POST/GET/PATCH/DELETE /api/v1/farms/{farm_id}/inventory[/{item_id}]
POST /api/v1/farms/{farm_id}/inventory/{item_id}/adjust
```

## What's next

Same as before — worker management, farm calendar/reminders, AI assistant
endpoint, reports export, admin panel. Follow the pattern already
established: one `crud/<domain>.py` + one `routes/<domain>_routes.py` per
new domain, registered in `main.py`.
