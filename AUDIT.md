# FarmWise AI — Security & Financial Accuracy Audit

**Scope of this pass:** full read-through of `backend/` (3,296 lines across
`core/`, `crud/`, `routes/`, `services/`), `main.py`, `.env` structure, and
`.gitignore`/git history. Every finding below was verified against the
*actual* current source — not assumed from the seed list — per the
instruction to trust the real code over prior assumptions.

**Tests:** no automated test suite exists in this repository (`pytest` is
listed in `requirements.txt` but there are zero test files). This blocks
"run the existing tests" as literally stated — there are none to run.
Verification for this pass instead used: `python -m py_compile` on every
backend file, a live `FastAPI TestClient` boot (including forcing the two
new production fail-fast paths to actually trip), and a mocked unit test
of the race condition fix showing the exact Supabase query issued and both
the success and lost-race code paths. See "Verification method" per
finding. A real pytest suite covering auth, farm isolation, and the
financial paths is a recommended follow-up (Phase 8 of the original brief)
and was not built in this pass — flagged as a remaining limitation below.

Status legend: **FIXED** (changed in this pass, verified), **OPEN**
(confirmed, not fixed — reason given), **REFUTED** (seed finding did not
match current code), **INFORMATIONAL** (true but low-risk / by design).

---

## Critical / High

### FWA-011 — Signup accepted any password, including a single character
**Severity:** Critical · **File:** `backend/routes/auth_routes.py` · **Status:** FIXED

`check_password_strength` is imported at the top of the file and used by
`/auth/reset-password` and `/auth/reset-password-direct`, but `/auth/signup`
never called it — `hash_password(data.password)` ran directly on
unvalidated input. A password of `"a"` was accepted and hashed.

**Impact:** the strength policy only applied to accounts *after* a
password reset, not at account creation — the point where the vast
majority of passwords are actually set.

**Fix:** added the same `check_password_strength()` call, in the same
place it's used elsewhere, before any user is created.

**Verification:** live `TestClient` call to `POST /api/v1/auth/signup`
with `password="a"` now returns `400 {"message": "Password must be at
least 8 characters."}` (previously would have returned `201`).

---

### FWA-002 / FWA-012 — Passwords silently truncated at 72 bytes; OTP reset path skipped strength check entirely
**Severity:** High · **File:** `backend/core/auth.py`, `backend/services/security.py`, `backend/routes/auth_routes.py` · **Status:** FIXED

Two related issues:

1. `hash_password`/`verify_password` (`core/auth.py`) both do
   `plain.encode("utf-8")[:72]` — bcrypt's real limit — with no
   validation anywhere that a password fits. Two different passwords
   sharing the same first 72 bytes hash identically and both work; a
   user pasting a long passphrase has no idea the tail of it is ignored.
2. `/auth/password-reset/confirm` (the phone/OTP reset path) calls
   `hash_password()` directly and never calls `check_password_strength()`
   — only the newer link-based and direct-reset endpoints did.

**Fix:** `check_password_strength()` now also rejects passwords over 72
UTF-8 bytes with a clear message, and is now called on **all four** paths
that set a password: signup, OTP confirm, link-based reset, and direct
reset.

**Verification:** `py_compile` + live `TestClient` signup call above;
manually traced all four `hash_password(...)` call sites in
`routes/auth_routes.py` to confirm each is now preceded by the check.

---

### FWA-005 / FWA-007 — Batch stock decrement was a check-then-act race, not atomic
**Severity:** High · **File:** `backend/crud/animals.py`, `backend/routes/finance_routes.py`, `backend/routes/animal_routes.py` · **Status:** FIXED

`decrement_batch_quantity()` computed `new_quantity = batch["quantity_current"] - amount`
from a Python dict fetched moments earlier by the caller, then ran an
unconditional `UPDATE`. Both `create_sale` (finance_routes.py) and
`record_mortality` (animal_routes.py) call it after their own
"is there enough stock?" check — but that check reads the *same* stale
value. Two concurrent requests against the same batch (two sales, or a
sale and a mortality record, submitted within the same moment — plausible
on a shared farm device or flaky mobile connection retrying a request)
can both pass their check against the same starting quantity and both be
applied, oversubtracting the batch below its real remaining count, or
below zero.

**Impact:** silently wrong livestock counts and, downstream, wrong
feed-per-animal and cost-per-animal figures that depend on
`quantity_current`.

**Fix:** `decrement_batch_quantity` now issues
`UPDATE animal_batches SET quantity_current = ... WHERE id = ... AND quantity_current >= amount`
via the Supabase query builder — the sufficiency check moved into the
`WHERE` clause so Postgres evaluates it atomically against the live row,
not a Python-side copy. If a concurrent write already changed the row,
zero rows match, `_one(res)` returns `None`, and a `ValueError` is raised.
Both call sites now catch that and return a `409 Conflict` asking the
user to refresh, instead of silently corrupting the count.

**Verification:** mocked `supabase.table(...).update(...).eq(...).gte(...)`
chain and confirmed (a) the exact filter arguments issued —
`.eq("id", "b1")` and `.gte("quantity_current", 5)` — match the intended
compare-and-swap, (b) a successful match returns the updated row, and
(c) an empty result (simulating a lost race) raises `ValueError` rather
than proceeding.

---

### FWA-006 — Profit figure is period income-minus-expenses, not per-batch cost-allocated profit
**Severity:** High (functional, not security) · **File:** `backend/crud/finance.py` (`profit_loss_summary`) · **Status:** OPEN — scoped out of this pass

Confirmed exactly as the brief's worked example describes: `profit_loss_summary()`
sums all `sales` revenue and all `expenses` rows inside a date window and
subtracts. There is no batch-level ledger of opening quantity, purchase
cost, feed consumed, mortality, cost of animals sold, or remaining
inventory value. If a farmer buys 500 birds and an expense/purchase-cost
entry is logged on the purchase date, that full cost hits `net_profit`
for that period regardless of how many of the 500 have actually been
sold — the remaining 400 birds' value isn't held back as inventory.

**Why not fixed in this pass:** this needs new schema (a batch cost
ledger table — opening qty, allocated purchase/feed/medication cost,
qty sold, qty remaining, weighted-average cost per head) plus an explicit
decision on costing method, a migration with rollback plan, and its own
test coverage — real design work, not a safe find-and-patch change to
make unreviewed. Recommend as the next dedicated phase; happy to design
and build it next specifically.

---

## Medium

### FWA-013 — CORS silently fell back to `"*"` (including in production) with credentials enabled
**Severity:** Medium · **File:** `backend/main.py` · **Status:** FIXED

`allow_origins=CORS_ORIGINS or ["*"]` combined with `allow_credentials=True`
is not a safe default: it's invalid per the CORS spec for credentialed
requests in a correctly-behaving browser, and relying on that rather than
an explicit origin list is fragile. If `CORS_ORIGINS` was ever left unset
on Render, the app would boot into this state silently.

**Fix:** if `APP_ENV=production` and `CORS_ORIGINS` is empty, the app now
refuses to start with a clear message, mirroring the existing
`SUPABASE_URL`/`SUPABASE_KEY` fail-fast pattern in `core/db.py`. Local
development (`APP_ENV != production`) keeps the `"*"` fallback with a
warning, so nothing changes for local dev.

**Verification:** live boot test with `APP_ENV=production, CORS_ORIGINS=""`
→ exits 1 with the intended message; same env with `CORS_ORIGINS` set →
boots normally (75 routes registered).

---

### FWA-014 — AI assistant endpoint had no rate limiting
**Severity:** Medium · **File:** `backend/routes/assistant_routes.py` · **Status:** FIXED

Every other sensitive endpoint (login, signup, all three password-reset
variants) uses the existing `services/security.py` rate limiter.
`/farms/{farm_id}/assistant/chat` did not, despite each call spending real
Gemini API quota/cost and being reachable by any authenticated member of
any farm.

**Fix:** added a 20-messages-per-hour-per-user limit (keyed by user ID,
not IP — IPs are commonly shared behind carrier NAT in this app's target
regions) using the same `services.security.check()` helper already used
elsewhere, returning `429` on the same pattern as the other endpoints.

**Verification:** `py_compile` + traced the call against `services/security.py`'s
existing sliding-window implementation (same helper, same exception
handler already registered in `main.py`).

---

### FWA-001 — Weak/missing `SECRET_KEY` only logged a warning, never blocked startup
**Severity:** Medium (High if it reached production undetected) · **File:** `backend/core/auth.py` · **Status:** FIXED

The check existed (`_WEAK_KEYS` set) but only logged `critical` and kept
running — every token in the app would be signed with a guessable key.
`core/db.py` already has the right pattern (`_fatal()` → `sys.exit(1)`)
for `SUPABASE_URL`/`SUPABASE_KEY`; `SECRET_KEY` didn't get the same
treatment.

**Fix:** mirrors `core/db.py`'s pattern — in production, a missing, weak,
or under-32-character `SECRET_KEY` now exits the process at import time
instead of booting insecurely. Non-production keeps the warning-only
behavior so local dev isn't forced to set one.

**Verification:** live boot test, `APP_ENV=production, SECRET_KEY="dev"`
→ exits 1; same env with a real 64-char hex key → boots normally.

---

### FWA-009 — Gemini API key sent twice (header + URL query string)
**Severity:** Low–Medium · **File:** `backend/services/ai_service.py` · **Status:** FIXED

The key was sent both as `x-goog-api-key` (header) and `?key=...` (URL
query param), "to be safe." Query-string secrets get written into
Render's own request logs and any intermediate proxy's access logs —
a real, if minor, exposure path headers don't have.

**Fix:** removed the query-string copy; Gemini's API accepts
header-based auth, which is what's kept.

**Verification:** `py_compile`; visually confirmed no other code path
references the removed `params=` kwarg.

---

## Low / Informational

### FWA-003 / FWA-004 — Farm authorization architecture
**Status:** REFUTED as originally worded, verified sound

Checked every route file (`animal_routes.py`, `farm_routes.py`,
`feed_routes.py`, `finance_routes.py`, `inventory_routes.py`,
`dashboard_routes.py`, `worker_routes.py`, `assistant_routes.py`,
`field_report_routes.py`) line by line: **every** farm-scoped endpoint
depends on `require_farm_role(...)`, which re-checks membership against
the database on every request using the `farm_id` **path parameter**
(never a client-supplied body/query value) — not from any cached JWT
claim. This is real-time, consistently applied, and I found no route
that skips it or trusts a browser-submitted `farm_id` directly.

One loose end: `get_current_user()` does still decode and return a
`farm_roles` claim from the access token, but nothing actually reads it —
it's dead data, not a live vulnerability. Recommend dropping it from the
token payload in a future pass to remove the temptation to use it later
without the same DB check `require_farm_role` does.

### FWA-008 — AI context bounded to a 30-day window
**Status:** OPEN, low priority — by-design tradeoff

Confirmed: `_build_farm_context()` pulls a 30-day dashboard summary plus
the full (unbounded) batch list and 10 most recent chat turns. This is a
reasonable default, not a bug, but means questions like "how did last
quarter compare" can't be answered accurately. Recommend making the
window configurable/seasonal in a future AI-assistant pass rather than
changing it opportunistically here.

### FWA-010 — AI requests use synchronous `httpx.post`
**Status:** OPEN, lower priority than originally scoped

Confirmed the call is synchronous, but `send_message` is a plain `def`
route (not `async def`), which FastAPI already runs in its worker thread
pool — so this does **not** block the event loop the way it would in an
`async def` route. Under heavy concurrent AI usage it could still exhaust
the thread pool faster than an `httpx.AsyncClient` would. Worth doing for
efficiency, not urgent for correctness.

### FWA-015 — Supabase credentials in git history
**Status:** OPEN — cannot be fixed by editing files

`git log -- backend/.env` shows a prior commit `ee21a3b` added the real
`.env` (with a live Supabase URL and `service_role` key) and a later
commit `1e0d983` deleted it. `.gitignore` now correctly excludes `.env`
going forward, but the credentials are still retrievable from git
history by anyone with repo access. This was already flagged previously
and remains open.

**Required action (outside this codebase):** rotate the Supabase
`service_role` key (and anon key, to be safe) in Supabase → Project
Settings → API, update Render's environment variables with the new key,
then either rewrite history (`git filter-repo` / BFG) to purge the old
commit or — simpler and safer — start a fresh repository from the current
tree. The zip delivered with this audit excludes `.git/` entirely so the
old history isn't redistributed further, but the *original* repo still
has it until you act on this.

### FWA-007 (remainder) — No idempotency protection on financial writes
**Status:** OPEN

Beyond the race condition fixed above, there's still no protection
against a genuine duplicate submit (e.g., a farmer double-taps "Record
Sale" on a slow connection and the client retries) — two identical sales
would both be recorded as separate rows. Recommend an idempotency-key
header pattern (client generates a UUID per form submission, server
dedupes on it for a short window) as a follow-up; not implemented here
since it touches the frontend request pattern too and deserves its own
pass across all the "record X" forms, not just sales.

---

## Summary

| ID | Severity | Status |
|---|---|---|
| FWA-011 (new) | Critical | **Fixed** |
| FWA-002 / FWA-012 | High | **Fixed** |
| FWA-005 / FWA-007 (race) | High | **Fixed** |
| FWA-006 | High | Open — needs a dedicated schema/design phase |
| FWA-013 (new) | Medium | **Fixed** |
| FWA-014 (new) | Medium | **Fixed** |
| FWA-001 | Medium | **Fixed** |
| FWA-009 | Low–Medium | **Fixed** |
| FWA-003 / FWA-004 | — | Refuted — verified sound as built |
| FWA-008 | Low | Open — by-design tradeoff, documented |
| FWA-010 | Low | Open — not urgent, event loop not actually blocked |
| FWA-015 | High | Open — requires credential rotation, not a code fix |
| FWA-007 (idempotency) | Medium | Open — recommended follow-up |

**Not done in this pass, stated plainly:** no automated test suite exists
or was built (Phase 8), and the batch-level cost-allocation rework
(FWA-006) — the single most consequential remaining item, since it
affects whether the profit numbers farmers see are trustworthy — was
deliberately left for a dedicated follow-up rather than rushed. The app
is **not** yet fully "production-ready" by the brief's own bar until
those two items and the git-history credential rotation (FWA-015) are
addressed.

---

# Phase 2 — Authentication, farm authorization, and security controls

Scope for this pass: the remaining Phase 2 checklist items not already
covered above — defense-in-depth on farm-scoped mutations, secure file
upload validation, audit logging, and automated cross-farm-access tests.
Same standard as Phase 1: every finding below was verified against the
actual current source and, where fixed, actually exercised (either a
live boot test or a real `pytest` run — see "Verification method" per
item).

### FWA-016 — Mutating crud functions for inventory/workers/field-reports didn't re-check `farm_id` in the query itself
**Severity:** Medium (defense-in-depth, not a live exploit) · **Files:** `backend/crud/inventory.py`, `backend/crud/workers.py`, `backend/crud/field_reports.py` · **Status:** FIXED

`update_item`, `delete_item`, `update_worker`, `delete_worker`, and
`add_feedback` all took only the resource's own id (`item_id`,
`worker_id`, `report_id`) with no `farm_id` in their `WHERE` clause. In
every current call site the route layer already validates the resource
belongs to the farm first (`_get_item_or_404`, `_get_worker_or_404`,
`crud.get_report`), so this was **not** currently exploitable — but it
meant correctness depended entirely on every future route remembering to
call that check first, with nothing in the data layer itself enforcing
it. This is exactly the shape of bug that's invisible in a diff and only
surfaces months later when someone adds a new call site under time
pressure.

**Fix:** all five functions now take `farm_id` and filter on it directly
in the Supabase query (`.eq("id", x).eq("farm_id", farm_id)`), matching
the pattern already used for `get_item`/`get_worker`/`get_batch`. Updated
the one call site each has accordingly.

**Verification:** `py_compile`; traced each of the 5 changed call sites
in `routes/inventory_routes.py`, `routes/worker_routes.py`,
`routes/field_report_routes.py` to confirm the new `farm_id` argument is
passed through.

---

### FWA-017 — Same check-then-act race as FWA-005, in inventory stock adjustment
**Severity:** High · **File:** `backend/crud/inventory.py` (`adjust_stock`) · **Status:** FIXED

Found while hardening the functions above: `adjust_stock` had the
identical bug fixed in Phase 1 for `animal_batches` — it read
`item["quantity_on_hand"]` from a Python dict fetched moments earlier,
computed the new total, and wrote it back unconditionally. Two
concurrent adjustments against the same item (e.g. two workers logging
feed usage at the same time) could silently lose one of the two deltas.

**Fix:** rather than guard on exact float equality of the quantity
itself (risky to get precision-exact for arbitrary decimal values
without a live DB to verify against), the fix uses `updated_at` as an
optimistic-concurrency version check: `UPDATE ... WHERE id = ... AND
farm_id = ... AND updated_at = <value just read>`. A concurrent writer
changes `updated_at`, so a losing writer gets zero matched rows and
retries against a freshly re-read row (up to 5 attempts) instead of
silently dropping its delta. `routes/inventory_routes.py` now catches
the exhausted-retries case and returns `409 Conflict`.

**Verification:** `pytest tests/test_stock_concurrency.py` — 6 tests
covering the batch-quantity fix from Phase 1 and this one: success on
first try, a single lost race with successful retry, and exhausting all
retries under sustained contention. All pass.

---

### FWA-018 — Field-report media upload accepted `image/svg+xml` (stored XSS vector)
**Severity:** High · **File:** `backend/routes/field_report_routes.py`, `backend/crud/field_reports.py` · **Status:** FIXED

The upload validation checked `content_type.startswith(("image/", "video/"))`
— a prefix match, not an allowlist. `image/svg+xml` passes that check,
and SVG files can embed `<script>` tags; since the uploaded file is
later served back from a public Supabase Storage URL, this is a
textbook stored-XSS vector (the classic "profile picture upload" SVG
attack, here via field-report photos).

A related, smaller issue in the same function: the storage file
extension was derived from the client-supplied `filename` field
(`filename.rsplit(".", 1)[-1]`), which is attacker-controlled and could
inject extra path segments into the generated storage key (e.g. a
filename like `a.png/../evil`).

**Fix:** replaced the prefix check with an explicit allowlist of 8 real
image/video MIME types (`ALLOWED_MEDIA_TYPES` in
`field_report_routes.py`). Also changed `crud.upload_media`'s extension
derivation to a fixed `content_type → extension` map instead of trusting
the filename at all — the filename is no longer used for anything
security-relevant.

**Verification:** `py_compile`; manually confirmed `image/svg+xml` is
absent from `ALLOWED_MEDIA_TYPES` and would now be rejected with `400`
before reaching `crud.upload_media`.

---

### FWA-019 — No audit trail for sensitive actions
**Severity:** Medium · **Files:** `backend/routes/_deps.py`, `backend/routes/farm_routes.py`, `backend/routes/worker_routes.py`, `backend/routes/auth_routes.py` · **Status:** FIXED (partial — see note)

Confirmed no audit logging existed anywhere for destructive or
security-sensitive actions (farm deletion, worker deletion, password
resets outside the normal flow, login lockouts) — only ad-hoc `log.error`
calls on failures, nothing structured or consistently applied on
success.

**Fix:** added a small `audit(event, **fields)` helper
(`routes/_deps.py`) that emits a structured `AUDIT event=... key=value...`
log line, and wired it into: farm deletion, farm settings updates,
worker deletion, account lockout after repeated failed logins, and both
outcomes of the direct (identifier-only, no-email-confirmation) password
reset — the single riskiest auth endpoint in the app by its own code
comment.

**Why "partial":** this is log-based, not a persisted `audit_log`
database table. A real table would support querying "show me everything
user X did" after the fact, with retention and export — this pass's
version relies on Render's log retention instead. Chose this
deliberately over an unreviewed schema addition; the function is
isolated so upgrading to a table later is a one-function change, not a
call-site rewrite. Flagged as a recommended follow-up, not claimed as
complete audit infrastructure.

**Verification:** `py_compile`; traced each call site.

---

### FWA-020 — No automated tests for cross-farm access (explicitly requested by Phase 2)
**Severity:** N/A (process gap) · **Status:** FIXED

Built a `pytest` suite from scratch — `backend/tests/` — since none
existed at all (Phase 1 already flagged this). `tests/conftest.py` sets
the env vars needed for `main.py`'s now-stricter startup checks to pass,
and provides fixtures for a real signed JWT (`make_token`) and an
in-memory fake of the `farm_members` table
(`membership_store`, monkeypatching only `crud.farms.get_membership` —
everything above that, including the real `require_farm_role`
dependency and real FastAPI routing, runs unmocked).

`tests/test_farm_authorization.py` (8 tests) covers: no-token rejection;
a token valid for farm A rejected on farm B via two different routers
(animals, finance) with the underlying `crud` function asserted as
*never called* for the rejected case, not just a non-200 status;
non-member rejection on every farm; role-insufficient rejection
(worker attempting a manager-only action) alongside the positive case
(manager succeeding); a destructive-action check (farm deletion);
and a request-body `farm_id` spoofing attempt to confirm the URL path
parameter is what's actually authoritative.

`tests/test_stock_concurrency.py` (6 tests) covers the FWA-005/FWA-017
race-condition fixes, as described above.

**Verification:** `pytest tests/ -v` → **14 passed**, 0 failed. Re-run
against the final state of the codebase after all Phase 2 edits, not
just once mid-way through.

---

## Phase 2 summary

| ID | Severity | Status |
|---|---|---|
| FWA-016 (new) | Medium | **Fixed** |
| FWA-017 (new) | High | **Fixed** |
| FWA-018 (new) | High | **Fixed** |
| FWA-019 (new) | Medium | **Fixed** (log-based; table upgrade recommended) |
| FWA-020 (new) | — | **Fixed** — 14-test suite added and passing |

**Still open after Phase 2:** rate limiting on `/auth/refresh` and
`/auth/logout-all` (lower risk — refresh tokens aren't guessable — so
deprioritized rather than skipped by oversight); a persisted
`audit_log` table (see FWA-019); and everything already carried over
from Phase 1 (FWA-006 batch cost allocation, FWA-015 credential
rotation, FWA-007's idempotency-key gap, FWA-008/FWA-010). The route
modules not touched this pass (`feed_routes.py`,
`dashboard_routes.py`) haven't been re-checked for the FWA-016 pattern
specifically — worth a quick pass before calling farm-authorization
hardening fully complete.

---

# Phase 3 — Supabase database integrity

Scope: the actual schema (provided directly, not inferred) checked
against every query pattern in `crud/*.py`. Verified with `grep` across
every `.eq(...)` filter call site to build the real list of columns that
matter, not a guessed one.

### FWA-021 — Two constraints the application code already assumes exist, but the schema doesn't have
**Severity:** High · **File:** schema (`farm_members`, `worker_attendance`) · **Status:** FIXED (migration + code)

1. `routes/worker_routes.py`'s `record_attendance()` carried a comment
   stating *"The DB has a UNIQUE(worker_id, date) constraint"* as the
   real backstop behind its duplicate-attendance check. Checked the
   actual schema: **no such constraint exists.** The route's own
   pre-check (read all attendance rows, filter for the same date in
   Python, reject if found) is a plain check-then-act race — the exact
   bug class fixed twice already in this audit (FWA-005, FWA-017) — and
   without the DB constraint, nothing catches it when the race is lost.
2. `farm_members` has no `UNIQUE(farm_id, user_id)`. `crud.add_member`
   doesn't guard against it either. Currently unreachable in practice
   (the only call site is farm creation, adding the owner to a
   brand-new farm that by definition has no existing members yet), but
   nothing stops a future "invite a member" endpoint from creating
   duplicate membership rows — possibly with two different roles for
   the same person on the same farm, which `get_membership()`'s
   `.limit(1)` would then resolve arbitrarily.

**Fix:**
- New migration `farmwise_indexes_and_constraints_migration.sql` adds
  both constraints, with verification `SELECT`s to run first (a
  `UNIQUE` add fails loudly against pre-existing duplicate rows rather
  than silently corrupting anything — the migration explains how to
  check and resolve that before altering).
- `crud/workers.py`'s `record_attendance()` now also catches the
  Postgres unique-violation (SQLSTATE `23505`) via `postgrest.exceptions.APIError`
  and raises a clean `ValueError`, which `routes/worker_routes.py` turns
  into `409`. The pre-check stays (fast, friendly message for the
  common case); the DB constraint plus this catch is what actually
  closes the race.

**Verification:** `pytest tests/test_stock_concurrency.py` — 3 new
tests: normal insert succeeds; a simulated `23505` from the DB is
turned into a clean `ValueError` (not a raw 500); and a *different*
DB error code is confirmed to propagate normally rather than being
misreported as a duplicate. 17/17 tests pass overall.

**Also confirmed, positively:** the schema's existing
`CHECK (quantity_current >= 0)` on `animal_batches` and
`CHECK (quantity_on_hand >= 0)` on `inventory_items` mean the atomic
decrement/adjustment fixes from Phase 1/2 (FWA-005, FWA-017) have a
database-level backstop even if the application logic had a bug — the
DB itself would reject a negative write. Good defense in depth already
in place; no action needed, just verified it's real.

---

### FWA-022 — No indexes anywhere except what PRIMARY KEY/UNIQUE columns create automatically
**Severity:** Medium (performance, not correctness — grows into correctness-adjacent as login/lookup queries slow) · **File:** schema (all tables) · **Status:** FIXED

The schema has zero `CREATE INDEX` statements. Every farm-scoped query
(23 call sites across `crud/*.py`) filters on `farm_id` with a full
sequential scan; the same is true for `worker_id`, `batch_id`,
`user_id`, and — on **every single login attempt** —
`users.email`/`users.phone_number` via `get_user_by_identifier()`. Not
a problem at today's data volume; becomes a real, user-visible slowdown
(and eventually a source of login timeouts) as each farm's transaction
history and the overall user base grow, with no code change needed to
trigger it — just time and usage.

**Fix:** the same migration adds `CREATE INDEX IF NOT EXISTS` for every
column actually used in a `.eq()` filter in the codebase (enumerated by
grepping, not guessed) — `farm_id` on 11 tables, `user_id` on 3,
`worker_id` on 2, `batch_id` on 4, plus `users.email`/`phone_number`
(partial indexes, `WHERE ... IS NOT NULL`, since both are optional) and
an `otp_codes` lookup index matching `verify_otp`'s actual query shape.
All `IF NOT EXISTS` — safe to re-run, safe against a populated table.

**Verification:** cross-checked the migration's index list against the
`grep -rhoE '\.eq\("[a-z_]+"' crud/*.py` output line by line — every
column that appears is covered; nothing in the migration is
speculative. Can't execute this against your real Supabase project from
here — verification of the actual `CREATE INDEX` runs is on you when
you apply it; the SQL Editor will report any failure immediately
(there shouldn't be any — every statement is additive and
`IF NOT EXISTS`-guarded).

---

### FWA-023 — Monetary values are `numeric` (exact) in the database but converted to Python `float` everywhere they're read
**Severity:** Medium · **Files:** `backend/crud/finance.py`, `backend/crud/inventory.py` · **Status:** OPEN — scoped out of this pass, documented

The schema stores money correctly — `numeric` columns (arbitrary
precision, no silent rounding) for `total_amount`, `amount`,
`total_cost`, `unit_price`, `quantity_on_hand`, etc. But every place the
app *reads* one of these back, it immediately does `float(...)`:
`profit_loss_summary`, `feed_cost_summary`, `adjust_stock`'s quantity
math, and the sale/expense/income creation functions that compute
`total_amount`/`total_cost` before insert. This discards the exact-decimal
guarantee the schema provides — Python `float` is IEEE-754 binary
floating point, and summing many `float()`-converted currency values
(as `profit_loss_summary` does across a farm's whole sales history) can
accumulate small rounding errors. In practice, for realistic currency
amounts (well under float64's ~15-17 significant digits), any single
error is far below a cent and would be very hard to actually observe —
but it's exactly the class of bug the master brief calls out
explicitly ("Use Decimal or PostgreSQL numeric types for monetary
values. Avoid floating-point arithmetic for money"), and "very hard to
observe" is a bad property for a bug in a farmer's profit numbers to
have.

**Why not fixed in this pass:** converting this properly means
switching every money computation in `crud/finance.py` and
`crud/inventory.py` (and the values they hand back through
`routes/finance_routes.py` and `services/ai_service.py`'s
`:.2f`-formatted context) to Python's `Decimal`, and verifying
`supabase-py`/`postgrest` actually round-trips `Decimal` through its
JSON serialization correctly on both insert and read — I haven't
confirmed that behavior and don't want to ship an unverified,
wide-reaching change to every money computation in the app without
testing it properly. This also overlaps directly with FWA-006's
batch-cost-allocation rework (same files, same functions) — doing both
together in one dedicated pass, with real test coverage for the
`Decimal` round-trip specifically, is the safer path than patching
this in isolation right now.

**Recommendation for that follow-up:** confirm `postgrest-py`'s JSON
encoder handles `Decimal` (may need a custom encoder or explicit
`str()` conversion at the insert boundary), convert `crud/finance.py`
and `crud/inventory.py`'s arithmetic to `Decimal`, and add tests
asserting no precision is lost across an insert-then-read round trip
for a value with an awkward decimal expansion (e.g. summing three
`0.1`-style amounts and asserting the exact result, which is the
classic case `float` gets wrong).

---

## Phase 3 summary

| ID | Severity | Status |
|---|---|---|
| FWA-021 (new) | High | **Fixed** — migration + `record_attendance` DB-error handling |
| FWA-022 (new) | Medium | **Fixed** — full index migration, grep-verified against actual query patterns |
| FWA-023 (new) | Medium | Open — scoped into the same follow-up as FWA-006 (both touch `crud/finance.py`) |

**New deliverable this phase:**
`farmwise_indexes_and_constraints_migration.sql` — run it in the
Supabase SQL Editor. It's additive-only and safe to run against your
live, populated database, with the one caveat spelled out inside it:
run the two duplicate-detection `SELECT`s before the two `ALTER TABLE
... ADD CONSTRAINT` statements at the bottom, since (unlike everything
else in the file) those two will fail loudly — not silently — if
pre-existing duplicate rows violate the new constraint.

**Still open overall, carried forward:** FWA-006 (batch cost allocation)
and FWA-023 (Decimal precision) are now explicitly linked as one
follow-up phase, since they share the same code. FWA-015 (credential
rotation) remains the other outstanding item from Phase 1 that isn't a
code change at all.

---

# Phase 4 — Financial accuracy

This phase closes FWA-006 (the single biggest item flagged since Phase
1) — real per-batch cost-allocated profit, replacing (alongside, not
instead of) the whole-farm period P&L that already existed.

### FWA-006 — Batch cost allocation, resolved
**Status:** FIXED

Implemented the weighted-average costing method the original brief asked
for, in `crud/finance.py`'s new `batch_profit_summary(farm_id, batch_id)`.
The model, in one paragraph: every unit ever placed in a batch
(`quantity_initial`) is assumed to carry an equal share of everything
spent on that batch — purchase price + feed consumed + medication +
any expense a farmer explicitly attributes to it — giving a single
cost-per-unit. That figure is then applied to whatever happened to each
unit: sold → cost of goods sold, still alive → remaining inventory
value (not yet a profit or a loss), died → mortality loss (spent, never
recoverable). By construction, those three always sum to the batch's
total accumulated cost, and quantity sold + current + mortality always
sum to quantity_initial — this is the exact worked example from the
original brief (500 bought, 100 sold, 400 remain): the fix means the
100 sold birds are charged 100/500 of the purchase cost, not all of it.

**New migration required:** `farmwise_batch_costing_migration.sql` —
adds two nullable columns the model needs that the schema didn't have:
`medication_records.cost` (didn't exist at all — medication cost
couldn't be tracked per-record before this) and `expenses.batch_id`
(optional; lets a farmer attribute a specific expense to a batch without
forcing every expense to pick one — omitted, an expense stays farm-level
overhead exactly as before).

**New endpoint:** `GET /farms/{farm_id}/animals/batches/{batch_id}/profit`
— restricted to finance-viewing roles (farmer, farm_manager, accountant),
matching the existing `/finance-summary` restriction; a worker who can
log a mortality event doesn't automatically see cost/profit figures.

**Data-completeness handling, not silent wrong numbers:** two real gaps
are surfaced as explicit flags rather than hidden behind a confident-looking
zero:
- `feed_cost_incomplete`: true when a batch consumed feed that was never
  purchased/priced anywhere on the farm — feed_cost would otherwise
  silently read `0.0`, which looks like "free" rather than "unknown".
- `medication_records_missing_cost`: count of medication records for
  this batch with no `cost` value — medication_cost only sums what's
  actually priced, so this tells the caller how much of the true cost
  isn't captured.
- `quantity_reconciles`: false if `quantity_sold + quantity_current +
  quantity_mortality != quantity_initial` for the batch — would indicate
  a data inconsistency predating this feature (or a manual DB edit), not
  something to silently paper over.

**Verification:** `pytest tests/test_batch_profit.py` — 14 tests,
including the brief's exact worked example asserting `cost_of_goods_sold
== 200.0` (not `1000.0`) and `gross_profit == 100.0` (not `-700.0`) for
the 500/100/400 scenario; a separate test proving mortality loss is
recognized as its own line item rather than silently folded into either
the survivors' cost basis or the sold units' COGS; all four cost
components (purchase + feed + medication + allocated expense) combining
correctly; both data-completeness flags in both the flagged and
not-flagged case; the unknown-batch error path; the reconciliation
sanity check catching an inconsistent history; and three endpoint-level
tests (worker denied, farmer allowed, cross-farm denied — reusing the
same pattern as `test_farm_authorization.py`).

---

### FWA-007 — Idempotency protection (duplicate submissions)
**Status:** FIXED for sales; recommended follow-up for expenses/income/mortality

Added optional `Idempotency-Key` header support: `services/security.py`
gained `check_idempotency_key(scope, key, ttl_seconds)`, an in-process
TTL-based duplicate-detector (same architecture and same disclosed
trade-off as the existing rate limiter — resets on redeploy, not shared
across multiple server instances, good enough to catch the common case
of a double-tap or a client's own retry-on-timeout logic). Wired into
`POST /farms/{farm_id}/sales`: a client sends the same key on a retry
and gets a clean `409` instead of a second sale being recorded. The
header is optional — omitting it is byte-for-byte today's existing
behavior, so no existing client breaks.

**Why sales specifically and not also expenses/income/mortality in this
pass:** sales is both the highest-value case (it also decrements batch
stock, so a duplicate is a compounding error) and the one the original
brief explicitly lists as a required test ("Add tests for: ... Duplicate
sale requests"). The same `check_idempotency_key` helper is generic and
ready to wire into the other write endpoints — recommended as a quick
follow-up, not a design gap.

**Verification:** `pytest` — 3 new tests: same key submitted twice
rejects the second with `409` and the underlying `crud.create_sale` is
confirmed called only once (not twice-then-rolled-back); omitting the
header entirely preserves today's behavior (both requests succeed,
`crud.create_sale` called twice); two different keys both succeed. 31/31
tests pass overall.

---

### FWA-023 revisited — Decimal precision: investigated, not fixed, honestly
**Status:** OPEN — investigated further, confirmed not safely fixable without deeper library changes

Committed in Phase 3 to revisit this alongside FWA-006 since they touch
the same code. Did — and confirmed empirically (not just reasoned about)
that there's no safe path to it from `crud/*.py` alone:

```
>>> httpx.Request('POST', 'http://x', json={'amount': Decimal('12.50')})
TypeError: Object of type Decimal is not JSON serializable
```

`supabase-py`'s `.insert()`/`.update()` calls go through `postgrest-py`,
which hands the row dict to `httpx`'s `json=` parameter — that uses
stdlib `json.dumps` with no `Decimal` support, so passing a raw
`Decimal` anywhere in an insert/update payload would crash the request
immediately. On the read side, Supabase returns `numeric` columns as
unquoted JSON numbers, which `httpx`'s response parsing turns into
Python `float` before `crud/finance.py` ever sees the value — there's no
hook at the `crud` layer to intercept that and parse as `Decimal`
instead without patching `postgrest-py`/`httpx` internals, which is far
more invasive and far less verifiable from here than anything else in
this audit.

**What would actually fix it:** a custom JSON encoder/decoder wired into
the shared `httpx.Client` `core/db.py` constructs (encode `Decimal` as a
string on the way out — Postgres numeric columns accept numeric strings
fine; decode numeric-looking JSON numbers as `Decimal` with a custom
`parse_float` on the way in). That's a real, scoped, doable fix — just
not one to ship in the same pass as a major financial-logic change
without dedicated test coverage for the round-trip itself (insert a
value with an awkward decimal expansion, read it back, assert nothing
shifted), which is more verification surface than this pass had time
for.

**Practical risk assessment, stated plainly rather than hand-waved:**
IEEE-754 `float64` has ~15-17 significant decimal digits. A single
multiply-then-subtract (e.g. `quantity * unit_price - discount`) on
realistic currency values has no observable precision loss — the
existing `create_sale`/`create_expense` computations are not meaningfully
at risk despite using `float`. The real exposure is accumulation:
`profit_loss_summary` and the new `batch_profit_summary` both sum many
`float()`-converted values. For this app's realistic transaction volumes
(a smallholder farm — tens to low thousands of records, not millions),
any accumulated error would be many orders of magnitude below a single
cent and not something a farmer would ever actually see in their
reports. This is a real category of risk worth fixing properly, not a
currently-observable bug — stated honestly rather than either ignored
or oversold.

---

## Phase 4 summary

| ID | Severity | Status |
|---|---|---|
| FWA-006 | High | **Fixed** — weighted-average per-batch cost allocation, 14 tests including the brief's own worked example |
| FWA-007 (sales) | Medium | **Fixed** — optional Idempotency-Key header, 3 tests |
| FWA-007 (expenses/income/mortality) | Medium | Open — same helper, recommended quick follow-up |
| FWA-023 | Medium | Open — investigated and confirmed genuinely hard to fix safely; practical risk quantified as very low at this app's scale |

**New deliverables this phase:** `farmwise_batch_costing_migration.sql`
(run in Supabase SQL Editor — additive, two nullable columns, no
existing behavior changes); `GET .../batches/{batch_id}/profit`
endpoint; 17 new tests (`test_batch_profit.py`).

**What's genuinely done vs. still open, stated plainly:** the profit
numbers a farmer sees for an individual batch are now real —
cost-allocated, not "everything spent minus everything earned this
month" — and are honest about what they don't know yet (unpriced feed,
unpriced medication) rather than confidently wrong. What's still open:
wiring the same idempotency protection into expenses/income/mortality
(quick, same pattern, just not done yet); the Decimal precision question
(real, low practical risk today, needs its own dedicated pass with
round-trip test coverage); and everything already carried from Phases
1-3 (FWA-015 credential rotation being the one that isn't a code
change at all).

---

# Phase 5 — AI Farm Assistant

### FWA-024 — The AI assistant leaked financial data to roles blocked from it via the direct API
**Severity:** High · **File:** `backend/services/ai_service.py`, `backend/routes/assistant_routes.py` · **Status:** FIXED

This is the standout Phase 5 finding, and it's a real permission bypass,
not a theoretical one: `GET /finance-summary` and `GET
.../batches/{batch_id}/profit` are both restricted to
`("farmer", "farm_manager", "accountant")` — a worker gets `403`. But
`_build_farm_context()` (fed into every AI chat request) included the
farm's full finance section — total income, expenses by category, net
profit — for **every role**, with no gating at all. A worker blocked
from the finance dashboard could simply ask the AI assistant "what were
my expenses last month?" and get the exact figures the direct API
withholds from them. The assistant was an unguarded backdoor around a
restriction the rest of the app enforces carefully.

**Fix:** `_build_farm_context()` now takes `include_financials: bool`,
computed from the caller's actual farm membership role (matching
`FINANCE_VIEW_ROLES`, the same tuple `finance_routes.py` and
`animal_routes.py`'s profit endpoint already use). `assistant_routes.py`
now passes `member["role"]` through to `ai_chat()` — previously
discarded entirely (`_member: dict = Depends(...)`, prefixed to signal
"unused"). Without financial access, the system prompt explicitly tells
the model it doesn't have the user's financial figures and to say so if
asked, rather than silently having no numbers to draw on and possibly
improvising. Operational data (batch headcounts, feed, mortality,
inventory, active worker count) stays visible either way — this isn't
"block workers from the assistant", it's "the assistant can't show
someone something the API already wouldn't."

**Verification:** `pytest tests/test_ai_assistant.py` — a worker-role
context is asserted to contain none of the specific dollar figures from
a mocked farm summary and no "Finance"/"profit" text at all, while still
containing the operational lines; a finance-role context is asserted to
contain both; and a route-level test confirms `assistant_routes.py`
actually passes the real role through (catches the regression where the
service-layer fix lands but the route still discards the role, which is
exactly how this bug happened the first time).

---

### Context now includes real per-batch profit — "which batch performed best?" is answerable
**Status:** IMPROVED

The context snapshot previously listed batches with only species/quantity/
status — no cost or profit figures — meaning the AI genuinely could not
have answered "which batch performed best" with real numbers no matter
how it was prompted; it could only guess. Now, for finance-viewing
roles, the context includes each active batch's cost-allocated net
profit from Phase 4's `batch_profit_summary()` (revenue, total cost, net
profit), so that specific question — one of the examples in the
original Phase 5 brief — has real data behind it.

**Bounded, not unlimited (AUDIT.md FWA-008):** capped to
`MAX_BATCHES_IN_CONTEXT = 10` active batches (most recently created
first), closing part of FWA-008's "context isn't bounded" concern — the
batch list specifically was previously unbounded (`crud.list_batches(farm_id)`
with no limit or status filter at all); it's now filtered to `active`
and capped. The 30-day window for the whole-farm finance/mortality
summary is unchanged — still open, as noted in FWA-008 originally, and
not addressed in this pass.

**Data-completeness carried through:** if a batch's `feed_cost_incomplete`
or `medication_records_missing_cost` flags are set (Phase 4), the
context now includes a "(some costs not yet priced)" note next to that
batch's profit line, so the model doesn't present an incomplete number
as if it were the whole picture.

**Verification:** `pytest` — asserts the bounded batch list actually
stops at `MAX_BATCHES_IN_CONTEXT` (constructs 15 batches, confirms
`Batch 9` appears and `Batch 10` doesn't, 0-indexed), and that the
"not yet priced" note appears when the underlying flag is set.

**Honest scope note:** this is enriched *context*, not the "safe
read-only tool layer" (agentic function-calling, where the model itself
decides which structured query to run) the original Phase 5 brief
describes. I considered implementing real Gemini function-calling in
this pass and decided against it — it's a materially larger, differently-
shaped change (multi-turn tool-call round trips, per-tool argument
validation, a new failure-mode surface) than fits safely alongside
everything else in this pass without dedicated test coverage for the
tool-calling protocol itself. What's shipped here achieves the same
underlying goal the brief cares about — grounded answers, no invented
numbers, bounded queries — for the specific example questions listed,
without the added complexity. Full tool-calling remains a reasonable
future enhancement if the assistant's question range needs to grow
beyond what a periodic context snapshot can cover.

---

### AI security checklist — verified item by item
**Status:** Reviewed; one gap found and fixed (FWA-024 above), rest confirmed sound

- **Never expose system prompts** — added an explicit instruction in the
  system prompt itself telling the model to refuse requests to repeat/reveal
  its instructions, including "developer mode"-style framing. This is
  defense in depth, not a guarantee — no prompt-based instruction can
  100% prevent a sufficiently creative extraction attempt — but it's a
  real, standard mitigation, verified present via
  `test_system_prompt_refuses_to_repeat_itself_instruction_present`.
- **Never expose API keys** — confirmed: the API key value is never
  interpolated into any client-facing message (error messages reference
  "the API key" or a status code, never the value itself); logging calls
  were checked and also never include the key.
- **Never allow the model to choose an arbitrary farm ID** — confirmed:
  `farm_id` always comes from the URL path via `require_farm_role`,
  never from model output; there's no code path where a model response
  could influence which farm's data gets queried.
- **Never allow AI-generated SQL to execute directly** — confirmed: no
  SQL generation or execution path exists anywhere in `ai_service.py`;
  it only reads pre-built context strings assembled by the existing
  `crud` layer.
- **Restrict tool access to the authenticated user's farm** — confirmed
  via the above; extended this phase to also restrict by *role*, not
  just farm (FWA-024).
- **Add per-user and per-farm AI usage limits** — was per-user only
  (Phase 2). Added a per-farm limit alongside it (60/hour) so several
  different members of one farm independently staying under their own
  per-user limit can't still add up to disproportionate load against
  that farm specifically.
- **Track AI usage and errors** — was not done at all before this phase
  (only warnings on failure, nothing on success). Added structured
  logging via the existing `audit()` helper (`routes/_deps.py`,
  introduced in Phase 2) for both outcomes — `ai_chat_completed` and
  `ai_chat_failed` — recording `farm_id`, `user_id`, `role`, and (on
  failure) a truncated reason, but never the message text or the
  model's reply content.
- **Handle Gemini timeouts and unavailable service gracefully** — already
  true before this phase; unchanged in spirit, improved in mechanism (see
  next section).

---

### AI performance — async client with bounded retry
**Status:** FIXED

`services/ai_service.chat()` is now `async def` using `httpx.AsyncClient`
instead of a blocking `httpx.post()`, with up to `MAX_RETRIES = 2` retries
on transient failures (`httpx.HTTPError` network errors, or a `5xx`
status) with a short linear backoff, before giving up.
`routes/assistant_routes.py`'s `send_message` is now `async def` and
`await`s the call.

This was previously deprioritized (Phase 1, FWA-010) since the route was
a plain sync `def`, which FastAPI already runs in its worker thread pool
— not actually blocking the event loop. That reasoning was correct as
far as it went, but Phase 5 explicitly calls for the async conversion
regardless (thread-pool exhaustion under concurrent AI load is a real,
separate concern from event-loop blocking), so it's done now rather than
left as a "technically fine" compromise.

**Auth errors (401/403) are explicitly excluded from retry** — retrying
a bad API key wastes quota and time for an error a retry can never fix;
confirmed via `test_chat_does_not_retry_on_401` that exactly one call is
made, not `MAX_RETRIES + 1`.

**Verification:** `pytest` — three tests: a transient `503` followed by
a successful `200` results in the retry actually happening and the
final reply being returned (both mocked responses are confirmed
consumed, not just the first); a `401` makes exactly one call, no
retries; a persistently failing `503` makes exactly `MAX_RETRIES + 1`
calls total, then raises, rather than retrying forever or silently
returning nothing.

---

## Phase 5 summary

| ID | Severity | Status |
|---|---|---|
| FWA-024 (new) | High | **Fixed** — role-gated AI context, verified with a route-level regression test |
| Batch profit in AI context | Medium (improvement) | **Fixed** — bounded to 10 batches, "which batch performed best" now answerable |
| AI security checklist | — | Reviewed item by item; only FWA-024 was a real gap, rest confirmed sound |
| Per-farm AI rate limit | Medium | **Fixed** — added alongside the existing per-user limit |
| AI usage/error tracking | Medium | **Fixed** — structured logging via the existing `audit()` helper |
| Async httpx + retry | Medium | **Fixed** — bounded retry on transient failures, auth errors excluded |
| Full agentic tool-calling | — | Not implemented — scoped decision, explained above, not a gap I missed |

**New test file:** `tests/test_ai_assistant.py` — 11 tests. 42/42 tests
pass across the whole suite after this phase.

**Still open overall:** everything carried from Phases 1-4 that wasn't
touched this phase — FWA-015 (credential rotation, not a code change),
FWA-023 (Decimal precision, investigated and documented as low practical
risk), idempotency on expenses/income/mortality, and the whole-farm
finance summary's fixed 30-day window (only the AI context's batch list
was bounded/filtered this phase, not the finance summary itself).
