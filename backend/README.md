# FarmWise AI — Backend + Frontend (WaziBot-style, single deployment)

This backend has been restructured to match the [[wazibot]] backend's
structure and coding style: synchronous route handlers, a `crud/` layer
that talks to Supabase directly via `supabase-py` (no ORM), no Alembic,
and the same `core/` / `routes/` / `services/` folder layout.

**The frontend now lives inside this backend, at `static/`, and is served
by this same FastAPI app** — again matching WaziBot, which mounts
`StaticFiles` at `/static` and registers explicit page routes
(`@app.get("/")`, `@app.get("/dashboard")`, etc.) rather than deploying the
frontend as a separate site. This is a deliberate fix: a separately
deployed static site needs its own Render service, its own URL, a correct
Publish Directory, an `index.html` fallback, and `CORS_ORIGINS` kept in
sync with wherever it ends up — every one of those was a real point of
failure that came up while getting this deployed. One app, one URL, one
Render service removes all of it.

### Page routes (clean URLs, served from `static/`)

```
GET /                  → static/landing.html
GET /login             → static/login.html
GET /signup            → static/signup.html
GET /forgot-password    → static/forgot-password.html
GET /reset-password      → static/reset-password.html
GET /dashboard             → static/dashboard.html
GET /static/<file>            → any file in static/ directly (css, js, or the html files themselves)
```

Every internal link and `window.location.href` redirect in the frontend
now points at these clean paths (e.g. `/login`, `/dashboard`) instead of
relative filenames (`login.html`) — relative filenames break the moment
a page isn't served from the directory root, which clean routes sidestep
entirely. The one exception is `dashboard.html`'s own `<link>`/`<script>`
tags, which point at `/static/dashboard.css` and `/static/dashboard.js`
directly, same as WaziBot's `dashboard.html` does.

### API base URL

Every page's inline script now sets `const API = '/api/v1';` — a relative,
same-origin path. No hostname branching, no CORS headers required for the
frontend to talk to the API, because they're no longer different origins.

## What changed from the previous version

| | Before | Now (this version) |
|---|---|---|
| DB access | Async SQLAlchemy 2 + repository pattern | Sync `supabase-py` client, dict in/out, `crud/*.py` |
| Migrations | Alembic (`alembic upgrade head`) | None — schema is managed directly in Supabase, same as WaziBot |
| Route handlers | `async def` | `def` (sync — FastAPI runs these in a threadpool) |
| Folder layout | `app/{core,models,schemas,repositories,services,api/v1}` | `{core,crud,routes,services,utils}` at the repo root |
| Env vars | `DATABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | `SUPABASE_URL`, `SUPABASE_KEY` (matches WaziBot's `core/db.py`) |
| Tests | `pytest` + async test client (12 tests) | `pytest` suite reintroduced in `tests/` (14 tests) — see "Running the tests" below. |

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
main.py                 # bootstrap: static mount + page routes, CORS, security headers, exception handlers, API routers
static/                  # the entire frontend — landing/login/signup/forgot-password/reset-password/dashboard + dashboard.css/.js
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

Then run `farmwise_indexes_and_constraints_migration.sql` and
`farmwise_batch_costing_migration.sql` (project root) once each in the
SQL Editor — additive indexes/constraints, and the columns needed for
real per-batch profit calculation, respectively. See the comment at the
top of each file and `AUDIT.md` for what they do and why.

### Run locally

```bash
uvicorn main:app --reload
# → http://localhost:8000            (the app itself — landing page)
# → http://localhost:8000/dashboard
# → http://localhost:8000/docs
```

One process now serves everything — no second `python -m http.server` for
the frontend needed.

### Deploying

**One Render Web Service. No Static Site.** If you previously created a
separate Static Site for the frontend, you can delete it — it's no longer
used.

- Root Directory: `backend`
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Environment: `SUPABASE_URL`, `SUPABASE_KEY`, `SECRET_KEY`, `CORS_ORIGINS`,
  `APP_ENV=production` — **all five are now required in production.**
  As of the security audit pass, the app deliberately refuses to start in
  production with a weak/missing `SECRET_KEY` or an unset `CORS_ORIGINS`
  (see `AUDIT.md`, findings FWA-001 and FWA-013) rather than silently
  booting insecurely. Set `CORS_ORIGINS` to your actual frontend origin,
  e.g. `https://your-service.onrender.com` — it can no longer stay empty.

After deploying, your Render URL *is* the app — `https://your-service.onrender.com/`
loads the landing page directly, no separate frontend URL to keep straight.

## Running the tests

```bash
pip install -r requirements.txt   # includes pytest
pytest tests/ -v
```

No live Supabase project is needed — every test either mocks the specific
`crud.*` function it depends on, or (for `tests/test_farm_authorization.py`)
fakes only the `crud.farms.get_membership` DB read while exercising the
real `require_farm_role` dependency and real routing end to end with
actual signed JWTs. Current coverage:

- `test_farm_authorization.py` — cross-farm access attempts (a token valid
  for one farm must not read/write another farm's data via any router),
  role-insufficient rejection, and a request-body `farm_id` spoofing
  attempt to confirm the path parameter is always authoritative.
- `test_stock_concurrency.py` — regression tests for the two
  check-then-act race conditions fixed in `crud/animals.py` and
  `crud/inventory.py` (see `AUDIT.md` FWA-005): both the success path and
  the "lost the race, must not silently corrupt data" path.

This is deliberately not full coverage of every endpoint yet — see
`AUDIT.md`'s "Not done in this pass" note for what's still missing
(notably: financial-accuracy tests once the batch cost-allocation rework
lands, and auth edge cases like token expiry/refresh rotation).

## Endpoints

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
GET      /api/v1/farms/{farm_id}/animals/batches/{batch_id}/profit    (added — AUDIT.md FWA-006)

POST/GET /api/v1/farms/{farm_id}/feed/purchases
POST/GET /api/v1/farms/{farm_id}/feed/consumption
GET      /api/v1/farms/{farm_id}/feed/cost-summary

POST/GET /api/v1/farms/{farm_id}/sales      (POST accepts an optional Idempotency-Key header — AUDIT.md FWA-007)
POST/GET /api/v1/farms/{farm_id}/expenses   (POST accepts an optional batch_id to attribute the expense to a batch)
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
