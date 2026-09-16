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

---

# Phase 6 — Frontend and API integration

Scope: every `.js`/`.html` file in `frontend/` (2,407 lines of JS across
8 pages), cross-checked against the real backend routes verified in
Phases 1-5 — not assumed from the file names. Two systemic bugs found,
both affecting every page, plus the frontend gaps left by Phase 4's new
backend features having no UI yet.

### FWA-025 — Hardcoded "$" ignored the farm's actual currency setting
**Severity:** High · **Files:** `dashboard.js`, `animals.js`, `feed.js`, `finance.js`, `workers.js` · **Status:** FIXED

`settings.js` already lets a farmer change their farm's currency to
`ZWL`, `ZAR`, `ZMW`, or `KES` (confirmed the exact option list in
`settings.html` rather than assuming) — but every other page's `money()`
formatter hardcoded `'$' + ...`, ignoring that setting entirely. A
farmer running their farm in ZAR or ZMW would see every dashboard
figure, sale, expense, and payroll amount prefixed with the wrong
currency symbol. The data was already available client-side — every
page already fetches `/farms` on load and gets each farm's `currency`
field back — it just was never used.

**Fix:** each `money()` function now looks up the active farm's
currency in a small symbol map (`USD $`, `ZAR R`, `ZWL Z$`, `ZMW ZK`,
`KES KSh`, plus a few others for future-proofing), falling back to a
plain code prefix (e.g. `"XYZ 12.50"`) for anything not in the map
rather than guessing a symbol. `currentCurrency` is set once in each
page's `init()` right after the active farm is resolved.

**Verification:** `node --check` on all 5 files (Node 22, real syntax
parse, not a guess) after both the `money()` change and the
`currentCurrency` assignment; cross-checked the symbol map against
`settings.html`'s literal `<option>` list to make sure every real
option is covered — caught that `ZMW` was missing from the first pass
of the map and added it before finishing.

---

### FWA-026 — Every page force-logged the user out on ANY error, not just an expired session
**Severity:** High · **Files:** all 8 page JS files · **Status:** FIXED

This is the more serious of the two. Every page's `init()` had:
```js
} catch (err) {
  localStorage.removeItem('farmwise_token'); /* ...clear everything... */
  window.location.href = '/login';
}
```
with no check on *what* the error was — a plain `403` (e.g. a worker
whose role doesn't cover a page — several exist after Phases 2/4's
role restrictions), a `404`, a `500`, or a transient network blip
during page load all triggered the exact same response as an actually
expired token: wipe the session, force a fresh login. A worker with a
completely valid, current session could get silently logged out just
for navigating to a page their role doesn't include, or from an
ordinary flaky connection.

**Fix:** every page's shared `api()`/`apiGet()` helper now attaches the
real HTTP status to the thrown error (`err.status = res.status`). Each
`init()`'s catch block now only does the destructive logout for a real
`401`; everything else calls a new `showLoadError(status)` helper that
renders a friendly in-page message instead — "you don't have access to
this page" for a `403`, a generic "something went wrong, try again"
with a retry button otherwise — without touching the user's session at
all.

**Verification:** `node --check` on all 8 files; manually traced that
`showLoadError` is defined before `init()` calls it in every file, and
that the 401-vs-other branch structure is identical across all 8 (the
same fix, not 8 slightly different ones that could drift).

---

### Phase 4 backend features had no frontend at all
**Severity:** Medium · **Status:** FIXED

Two things Phase 4 added to the backend were completely invisible in
the web app:

- **Per-batch profit** (`GET .../batches/{batch_id}/profit`) — the
  actual headline deliverable of Phase 4 — had no UI anywhere. Added a
  "Profit" tab to the batch detail modal (`animals.html`/`.js`),
  showing revenue, total accumulated cost, cost of goods sold, remaining
  inventory value, mortality loss (only shown if non-zero), and net
  profit — plus the data-completeness note from FWA-006's
  `feed_cost_incomplete`/`medication_records_missing_cost` flags when
  they're set, so an incomplete number isn't shown as if it were
  final. Gated to `farmer`/`farm_manager`/`accountant` (matching the
  backend's `FINANCE_VIEW_ROLES`) — the tab doesn't even render for a
  worker, consistent with FWA-024's principle of not showing UI for
  data the API would block.
- **`medication_records.cost`** and **`expenses.batch_id`** (both added
  in the same migration) had no form fields — meaning every medication
  record logged through the web app would have `cost: null` forever,
  directly undermining the very profit numbers just added a UI for.
  Added an optional "Cost" field to the medication form, and an
  optional "Attribute to batch" dropdown to the expense form (reusing
  the batch list the sale form already loads).

**Verification:** `node --check` on `animals.js`/`finance.js`; HTML
`<div>` open/close tag balance checked across all 14 HTML files (not
just the 2 touched) to catch any accidental markup breakage.

---

### Other checks performed, no issues found
- **Every API call path** in all 8 files cross-referenced against the
  real backend routes verified across Phases 1-5 — no broken/mismatched
  endpoints, no typos.
- **Auth headers** — consistently attached via each page's shared `api()`
  helper; nothing bypasses it.
- **Form validation** — spot-checked `min`/`step`/`required` attributes
  against backend Pydantic constraints (e.g. sale quantity `min="1"`
  matching `Field(gt=0)`) — already correct, no drift found.
- **Dashboard calculations** — `dashboard.js` does no independent
  client-side math on financial figures; every number is a direct
  passthrough of backend-computed values. The expense-by-category bar
  chart's proportional widths and empty-state handling are both correct.
- **AI assistant error handling** — was showing the same generic
  "check your connection" message for a `429` rate-limit response as
  for an actual network failure, which reads as "retry now" when the
  correct action is "wait". Fixed to show the backend's real rate-limit
  message when `err.status === 429`.
- **Inventory/settings empty states** — already handled correctly
  (empty grid states, "no farm yet" states) — no changes needed.

---

## Phase 6 summary

| ID | Severity | Status |
|---|---|---|
| FWA-025 (new) | High | **Fixed** — currency-aware `money()` across 5 files |
| FWA-026 (new) | High | **Fixed** — 401-only logout across all 8 files |
| Batch profit UI | Medium | **Fixed** — new Profit tab, role-gated |
| Medication cost / expense batch_id UI | Medium | **Fixed** — form fields added |
| AI assistant 429 handling | Low | **Fixed** |

**Not done in this pass, stated plainly:** this was a systematic review
of API integration, error handling, and the two features Phase 4 left
without UI — not a full visual/UX redesign pass. Mobile responsiveness
CSS wasn't re-audited (no code changes were made to any `.css` file this
phase, so no regression risk, but also no fresh verification beyond
what an earlier session already fixed). No automated frontend tests
exist (`node --check` verifies syntax, not behavior) — a real browser-
based test suite (Playwright or similar) remains a reasonable follow-up
if this app's frontend keeps growing.

---

# Phase 7 — Render deployment

**Explicitly skipped at the user's request.** No deployment
configuration (`render.yaml`, start command, environment variable
setup) was reviewed or changed in this pass. `.env.example` and the
production-readiness checks added in Phases 1-2 (fail-fast on a weak
`SECRET_KEY` or unset `CORS_ORIGINS` when `APP_ENV=production`) already
exist from earlier phases and were re-verified as still working in
Phase 8 below, but that's incidental — no new deployment-specific work
was done here.

---

# Phase 8 — Testing and verification

Scope: fill the gaps in the original brief's Phase 8 checklist not
already covered by the test suites built in Phases 2/4/5 (65 tests
across `test_farm_authorization.py`, `test_stock_concurrency.py`,
`test_batch_profit.py`, `test_ai_assistant.py`) — specifically
authentication (signup/login/lockout/JWT expiry), remaining financial
validation boundaries, AI usage limits, and a full lint pass across the
codebase. As in every phase, writing the tests is what surfaced most of
the findings below — several are real bugs that reading the code alone
hadn't caught.

### FWA-027 — Signup could create an unreachable "ghost" account
**Severity:** High · **File:** `backend/routes/auth_routes.py` · **Status:** FIXED

Writing `test_signup_requires_email_or_phone` surfaced this: `SignupRequest`'s
"require email or phone" check was a `@field_validator("phone_number")`,
which Pydantic v2 only runs when that field is **explicitly present** in
the request payload (`validate_default` defaults to `False` for field
validators). A signup request omitting both `email` and `phone_number`
entirely skipped the check completely — confirmed empirically with a
throwaway script, not just reasoned about — and would have reached
`crud.create_user` with both fields `None`. The `users` table has no
`CHECK` constraint requiring either (both are plain nullable columns),
so this could have silently created an account with no way to ever log
back in, since login is always by `identifier` (email or phone).

**Fix:** replaced the `@field_validator` with a `@model_validator(mode="after")`,
which always runs against the fully-constructed model regardless of
which fields were explicitly supplied.

**Verification:** confirmed the bug empirically before fixing (a script
constructing `SignupRequest(full_name=..., password=...)` with neither
contact field raised no error), confirmed the fix closes it (same
script now raises `ValidationError`), and confirmed both valid cases
(email-only, phone-only) still work. Formalized as
`test_signup_requires_email_or_phone` in `test_auth.py`.

---

### FWA-028 — The fix above then exposed a systemic bug: any custom validator raising `ValueError` crashed with a 500 instead of a clean 422
**Severity:** High · **File:** `backend/main.py` · **Status:** FIXED

Once FWA-027's `model_validator` correctly raised on invalid input, the
test still failed — with a `500`, not the expected `422`. Traced it to
`main.py`'s `validation_exception_handler`: Pydantic v2 embeds the raw
exception object in `ctx["error"]` for any custom validator that raises
a plain `ValueError`, and the handler was passing `exc.errors()` straight
into a plain `JSONResponse` (which uses stdlib `json.dumps` — no support
for arbitrary Python objects). The result: `TypeError: Object of type
ValueError is not JSON serializable`, caught by the generic exception
handler, and surfaced to the client as an opaque `500` instead of a
useful `422` with the actual validation message.

This wasn't just about the one new validator — **any** custom validator
raising `ValueError` anywhere in the app would have hit the same crash.
It happened to be undiscovered until now because `SignupRequest` was
the only custom validator in the whole codebase, and its check had
never actually fired before FWA-027's fix (see above).

**Fix:** `validation_exception_handler` now wraps `exc.errors()` in
`fastapi.encoders.jsonable_encoder` before returning it — exactly what
FastAPI's own default validation handler does internally for this
reason. Confirmed with a standalone reproduction (a throwaway Pydantic
model with a `model_validator` raising `ValueError`) that
`jsonable_encoder` correctly serializes it (the exception object
degrades to `{}` inside `ctx`, but the human-readable `msg` field —
the part that actually matters — is preserved intact).

**Verification:** `test_signup_requires_email_or_phone` now passes with
a real `422`, not a `500`. Grepped the whole `routes/` tree to confirm
no other custom validator exists that could have been separately
affected — there wasn't one, so this fix's practical impact today is
scoped to signup, but the handler itself is now correct for any
validator added in the future.

---

### FWA-029 — Login's DB-backed account lockout was fully built and never called
**Severity:** High · **File:** `backend/routes/auth_routes.py` · **Status:** FIXED

Writing a lockout test (`test_login_blocked_when_db_account_is_locked_even_from_a_fresh_ip`)
surfaced this: the `users` table has `failed_login_attempts` and
`locked_until` columns, and `crud/users.py` has fully-implemented
`register_failed_login()` / `is_locked()` / `clear_failed_logins()`
functions that use them correctly — but `routes/auth_routes.py`'s login
handler never called `register_failed_login` or `is_locked` at all. The
**only** lockout enforcement was `services.security.is_login_locked`,
an in-memory structure keyed by `(ip, identifier)`. That's bypassable
by an attacker simply rotating source IP between attempts (trivial —
different mobile networks, a proxy, a small botnet) — each new IP
starts the failure count over from zero and never locks out the
account itself.

**Fix:** added `crud.is_locked(user)` as a check (once the user record
is looked up, before password verification) and `crud.register_failed_login(user)`
on a failed attempt, alongside — not instead of — the existing IP-based
check. Kept both: the account-level check is IP-rotation-resistant and
survives redeploys/multiple instances; the IP-based check still catches
a single IP hammering many different existing usernames, which an
account-keyed check alone wouldn't cover.

**Verification:** `test_login_blocked_when_db_account_is_locked_even_from_a_fresh_ip`
mocks the in-memory IP check to return "not locked" and confirms the
DB-backed check still blocks the request (423) before password
verification even runs; `test_failed_login_increments_the_db_backed_counter`
confirms `crud.register_failed_login` is actually called with the right
user on a wrong-password attempt; `test_successful_login_clears_the_db_backed_counter`
confirms a successful login resets it.

---

### FWA-030 — Feed consumption never validated a client-supplied `batch_id` belongs to the farm
**Severity:** Medium · **File:** `backend/routes/feed_routes.py` · **Status:** FIXED

Same principle as `create_sale`/`create_expense` (which both already
check this), missed for `record_feed_consumption`: a client-supplied
`batch_id` was inserted as-is with no check that it belongs to the
requesting farm. The foreign key to `animal_batches` only guarantees
the batch exists *somewhere*, not that it belongs to this farm — so a
member of Farm A could reference a batch UUID from Farm B (if known/
guessed) and have it accepted. Traced the actual blast radius before
overstating it: because `list_feed_consumption`/`feed_cost_summary`
always filter by `farm_id` in addition to `batch_id`, the practical
impact is data-integrity pollution within the inserting farm's own
records (a nonsensical foreign batch reference), not a cross-farm
information leak — Farm B's own queries never see the row, since it's
stored with Farm A's `farm_id`. Still a real gap worth closing, just not
overstated as more severe than it is.

**Fix:** added the same `crud.get_batch(farm_id, data.batch_id)` check
already used elsewhere, `404` if the batch doesn't belong to this farm.

**Verification:** `test_feed_consumption_rejects_batch_id_from_another_farm`
confirms the check fires and the insert is never reached;
`test_feed_consumption_without_batch_id_still_works` confirms the
(optional) field being omitted entirely is unaffected.

---

### Remaining Phase 8 checklist items — filled with real tests
**Status:** DONE

- **Financials boundary validation**: zero/negative amounts and
  quantities rejected for expenses, sales, and feed purchases; mortality
  quantity exceeding a batch's current headcount rejected (`400`, not a
  silent negative count) — `test_financial_validation.py`.
- **AI usage limits**: confirmed the per-user rate limit (20/hour,
  Phase 2) actually returns `429` on the 21st call within the window,
  and that a request under the limit succeeds normally —
  `test_ai_chat_blocked_after_per_user_hourly_limit` /
  `test_ai_chat_allowed_under_the_limit`. (Discovered mid-writing that
  the in-process rate-limiter's state is shared across tests in the
  same run if they reuse a user id — not an app bug, a test-isolation
  detail — fixed by giving the "under the limit" test its own user id.)
- **JWT expiry**: an access token with a past `exp` claim is rejected
  with `401`; a valid one is accepted; a malformed token is rejected; a
  refresh token used where an access token is expected is rejected
  (type confusion) — `test_auth.py`.
- **Linting / import checks**: ran `pyflakes` across the entire backend.
  Found and fixed 12 genuinely unused imports/variables across
  `core/auth.py`, `crud/users.py`, `crud/finance.py`, `crud/dashboard.py`,
  and five route files (`auth_routes.py`, `farm_routes.py`,
  `animal_routes.py`, `dashboard_routes.py`, `field_report_routes.py`).
  All were cosmetic (no runtime bugs from any of them), but Phase 8
  explicitly asks for a lint pass and fixing what it finds — done. The
  only remaining `pyflakes` output is `crud/__init__.py`'s intentional
  barrel re-exports (every `crud.*` submodule function is re-imported
  there so callers can write `crud.create_sale(...)` etc.) — expected,
  not a finding.
- **Deployment checks** (backend imports, health endpoint, Supabase
  config validation): re-verified live rather than assumed — a fresh
  `TestClient` boot returns `200` from `/health` with all 79 routes
  registered; an invalid `SUPABASE_URL` format still correctly refuses
  to start (Phase 1); a weak `SECRET_KEY` in production still correctly
  refuses to start (Phase 1). No regressions from any of Phases 1-8's
  changes.

---

## Phase 8 summary

| ID | Severity | Status |
|---|---|---|
| FWA-027 (new) | High | **Fixed** — signup validator now actually fires |
| FWA-028 (new) | High | **Fixed** — validation-error handler no longer crashes on custom validators |
| FWA-029 (new) | High | **Fixed** — DB-backed account lockout wired in alongside the IP-based one |
| FWA-030 (new) | Medium | **Fixed** — feed consumption batch_id ownership check |
| Financial validation boundaries | — | **Done** — 7 new tests |
| AI usage limits | — | **Done** — 2 new tests |
| JWT expiry / token type confusion | — | **Done** — 4 new tests |
| Linting | — | **Done** — 12 unused imports/variables cleaned up, zero remaining findings |
| Deployment sanity checks | — | **Re-verified**, no regressions |

**New test files this phase:** `test_auth.py` (15 tests),
`test_financial_validation.py` (11 tests). **Total suite: 68 tests, all
passing** — up from 42 at the end of Phase 6. Every fix in this phase
was found by actually writing a test for something the checklist named,
not by re-reading code that had already been read in earlier phases —
consistent with how most of this audit's real findings have surfaced
throughout.

---

# Phase 9 — Final deliverables

## 1. AUDIT.md
This document — findings FWA-001 through FWA-030, each with severity,
file, description, impact, fix, and verification method, organized by
the phase that found or fixed it.

## 2-3. Updated backend and frontend code
Every change across Phases 1-8 is in the delivered zip. No file was
rewritten wholesale; every edit was scoped to the specific finding it
addresses, per the original brief's non-negotiable rules.

## 4. Supabase migration scripts
Three new additive migrations (alongside the two that already existed
— `farmwise_field_reports_migration.sql`, `farmwise_password_reset_migration.sql`):

- `farmwise_indexes_and_constraints_migration.sql` (Phase 3) — indexes
  on every column actually used in a `.eq()` filter across the codebase,
  plus two `UNIQUE` constraints the application code already assumed
  existed (`farm_members(farm_id, user_id)`, `worker_attendance(worker_id, date)`).
- `farmwise_batch_costing_migration.sql` (Phase 4) — `medication_records.cost`
  and `expenses.batch_id`, both nullable, both required for real
  per-batch profit allocation.

**None of these have been run against your actual Supabase project from
here** — I don't have access to it. Each file's header comment explains
what it does and, for the two `UNIQUE` constraints, how to check for
pre-existing duplicate rows first (they'll fail loudly, not silently, if
duplicates exist — but better to check first).

## 5. Updated .env.example
Present in `backend/.env.example`, covering every environment variable
introduced or made required across all phases: `SECRET_KEY` (now
required in production — Phase 1), `GEMINI_API_KEY`/`GEMINI_MODEL`
(Phase 5), `CORS_ORIGINS` (now required in production — Phase 1), and
the original Supabase/email/SMS variables.

## 6. Updated Render configuration
**Not done — Phase 7 was explicitly skipped at the user's request.** No
`render.yaml` exists in this repository (none existed before this audit
either). The deployment-relevant guardrails that do exist (fail-fast on
a weak `SECRET_KEY` or unset `CORS_ORIGINS` in production) were added in
Phase 1 as part of the security audit, not as Phase 7 deployment work,
and were re-verified working in Phase 8.

## 7. Updated README
`backend/README.md` was updated incrementally across every phase that
added something a future developer would need to know: the new test
suite and how to run it, both new migration files and when to run them,
the stricter production environment-variable requirements, and the
current endpoint list including the new batch-profit endpoint.

## 8. Test results
68 tests, all passing, across 6 test files
(`test_farm_authorization.py`, `test_stock_concurrency.py`,
`test_batch_profit.py`, `test_ai_assistant.py`, `test_auth.py`,
`test_financial_validation.py`) plus a shared `conftest.py`. Zero
`pyflakes` findings outside intentional barrel re-exports. All frontend
`.js` files pass `node --check`.

## 9. List of remaining limitations

Carried forward, unresolved, stated plainly rather than buried:

- **FWA-006's Decimal precision question (FWA-023)** — investigated
  concretely in Phase 4 (confirmed `httpx`/`postgrest-py` can't
  round-trip `Decimal` without patching shared library internals),
  documented as a real but practically low-risk category of issue at
  this app's realistic transaction volume. Not fixed.
- **FWA-015 — Supabase credentials in git history.** Cannot be fixed by
  editing files. Requires rotating the `service_role` key in Supabase
  and either rewriting git history or starting a fresh repository. This
  remains the single most important action item outside this codebase.
- **Idempotency protection (FWA-007)** exists on sales only. The same
  helper (`services.security.check_idempotency_key`) is ready to wire
  into expenses, income, and mortality — straightforward, just not done.
- **No persisted audit-log table** — sensitive-action logging (Phase 2,
  FWA-019) is structured `log.info()` calls, not a queryable database
  table. Chose this deliberately to avoid an unreviewed schema addition;
  noted as a reasonable future upgrade.
- **Full agentic AI tool-calling** was not implemented (Phase 5) — the
  assistant answers from a bounded, role-gated context snapshot rather
  than deciding which structured query to run itself. This was a
  scoped decision, explained in Phase 5's write-up, not an oversight.
- **No browser-based frontend test suite** — `node --check` verifies
  syntax only, not behavior. A real test suite (Playwright or similar)
  would need real browser automation infrastructure this pass didn't
  set up.
- **Render deployment (Phase 7) was not reviewed at all** — skipped at
  the user's explicit request. No `render.yaml`, health-check
  configuration, or actual deployment verification was done.
- **The whole-farm finance summary's 30-day window is still fixed**,
  not configurable (Phase 5 only bounded/filtered the AI assistant's
  batch list specifically, not this endpoint).

## 10. Summary of what changed

Across nine phases (eight completed, one explicitly skipped): fixed 30
numbered findings ranging from critical (a password-strength bypass at
signup, a stock-quantity race condition exploitable by concurrent
requests, a stored-XSS vector via SVG upload, an AI assistant that
leaked financial data around an existing role restriction, a
force-logout bug affecting every page of the frontend) to informational
(verifying the farm-authorization architecture was already sound).
Replaced a naive whole-farm profit calculation with real per-batch
cost allocation, matching the exact worked example in the original
brief. Built a test suite from zero to 68 tests. Added 3 new SQL
migrations. The application is meaningfully more secure, more
financially accurate, and better tested than at the start of this
engagement — but is **not** unconditionally "production-ready": the
credential rotation (FWA-015) and the Decimal precision question
(FWA-023) are the two items that most warrant attention before this
handles real money for real farmers at scale, and Phase 7's deployment
review was never done at all.

---

# Post-Phase-9 — Mobile feature parity + AI photo diagnosis

Follow-up work after the nine-phase audit closed: bring the web app up
to parity with the mobile app's feature set, and add AI photo-based
triage for sick/injured animals. Scope was explicitly web-only; the
mobile app itself was reviewed for comparison but not modified.

### What was found already done
- **AI photo diagnosis was already fully built on the backend** —
  `POST /farms/{farm_id}/assistant/diagnose` (`services/ai_service.py`'s
  `diagnose_image()`, `routes/assistant_routes.py`) from earlier work,
  complete with its own tighter rate limit (10/hour/user, 30/hour/farm —
  images use more of Gemini's free-tier quota than text), an explicit
  not-a-vet disclaimer baked into the system prompt, and 8 passing
  tests. Reuses the exact same free-tier `gemini-2.5-flash` model as
  regular chat — no separate paid vision product, matching the "keep it
  low-cost" requirement by construction, not by extra effort.
- `frontend/field-reports.css` already existed, and every page's
  sidebar already linked to `/field-reports` — but the actual
  `.html`/`.js` were never built, so that link had been dead this whole
  time.

### FWA-031 — Field report edit/delete: mobile's client code called endpoints the backend never implemented
**Severity:** Medium · **Files:** `backend/crud/field_reports.py`, `backend/routes/field_report_routes.py` · **Status:** FIXED

The mobile app's `services/endpoints/index.ts` already has `update()`
(PATCH) and `remove()` (DELETE) functions for field reports, with a
code comment admitting the author never actually verified these routes
existed server-side ("NOT independently verified against backend
source"). They didn't. Any mobile user tapping "edit" or "delete" on
their own pending report would get a 404 in production.

**Fix:** added both, scoped correctly — only the report's own author,
and only while `status == 'pending'` (editing/deleting after a manager
has already left feedback would misrepresent what they reviewed). This
makes real server-side what was previously only a client-side
assumption; a direct API call could have bypassed it entirely before
this fix, regardless of what the mobile UI restricted.

**Verification:** `pytest tests/test_field_reports.py` — 7 tests
covering author-allowed, non-author-denied, and already-reviewed-denied
for both PATCH and DELETE, plus an empty-PATCH-body rejection. 83/83
tests pass overall.

### New web pages — closing the actual mobile/web feature gap
- **`/field-reports`** (`field-reports.html`/`.js`) — the one page mobile
  had that web genuinely lacked. Full CRUD: create (with a media
  picker — photo/video upload reusing the existing
  `POST .../field-reports/media` endpoint, immediate thumbnail preview,
  remove-before-submit), list with status filtering, detail view with
  manager feedback, and author-only edit/delete using FWA-031's new
  endpoints. Role-gated identically to the backend: workers see only
  their own reports and cannot give feedback; farmer/farm_manager see
  everyone's and can.
- **`/export`** (`export.html`/`.js`) — CSV/PDF export of all nine
  report types mobile already offers (P&L summary, sales, expenses,
  income, animal batches, inventory, feed purchases, feed consumption,
  field reports), ported column-for-column from the mobile app's
  `lib/exportReport.ts` + `app/export.tsx`. No new backend endpoints
  needed — every report is built from data the app already fetches.
  Generation mechanics are the natural web equivalents of mobile's
  `expo-print`/`expo-sharing`: CSV via a `Blob` + temporary
  `<a download>`; PDF via the same HTML mobile renders, printed through
  a hidden `<iframe>` and `window.print()` (every modern browser's print
  dialog offers "Save as PDF" natively — no PDF library needed). Gated
  to farmer/farm_manager, matching mobile's `managerOnly: true` on this
  feature exactly.
- Added the `/export` nav link to all 8 existing pages' sidebars (it
  only existed in the two new pages until this pass), and the
  `GET /export` FastAPI route to serve it.

### AI photo diagnosis — web UI
`assistant.js`/`.html`: a 📷 attach button next to the chat input,
client-side type/size validation mirroring the backend's exactly
(JPEG/PNG/WEBP/HEIC, 8 MB cap — checked before the request goes out, so
a bad file gets an instant answer instead of a round trip), a preview
strip with a remove option, and a `sendPhotoDiagnosis()` path that
posts `multipart/form-data` (`photo` + optional `note`) to the existing
`/assistant/diagnose` endpoint. The photo renders inline in the user's
own chat bubble for context; the reply renders as a normal assistant
message, and both are preserved in conversation history exactly like
regular chat (the backend already handles this — it stores a text
marker for the photo turn since `ai_messages.content` is text-only, not
a binary/image store, keeping this at zero storage cost beyond what
already existed).

### Explicitly not done, and why
- **Team member management** (invite/remove/change role beyond the
  farm-creation-time owner) — checked both the backend
  (`routes/farm_routes.py` has only `GET /members`, no `PATCH`/`DELETE`/
  invite endpoint) and mobile's endpoint layer (same gap). This is a
  real, general product gap, but it isn't a mobile-vs-web parity gap —
  mobile doesn't have this either, so porting a mobile feature can't
  fix it. Flagged, not built, to stay scoped to the actual request.
- **The mobile app's own `diagnose()` client function is stale.** Its
  code comment explicitly says the endpoint "DOES NOT EXIST yet" and
  proposes a different request shape (`{image_url, context}` JSON) than
  what the real backend implements (multipart `photo` file + `note`
  form field). This means mobile's photo-diagnosis feature is currently
  broken in production — calling it would either 404 or send the wrong
  request shape. Not fixed, since this pass was explicitly scoped to
  web; flagged clearly since it's a real, currently-broken feature on
  the other platform.

### Verification summary
`pytest tests/` — 83 passed (up from 76 before this round: +7 from
`test_field_reports.py`). `pyflakes` — zero findings outside
`crud/__init__.py`'s intentional re-exports. `node --check` — all 10
frontend JS files (8 existing + 2 new) pass. Live boot test confirms
both new routes (`/export`, `/field-reports`) return `200` and serve
the correct HTML file. HTML `<div>` tag balance checked across all 16
frontend HTML files (14 existing + 2 new) — all balanced.

---

# Post-Phase-9, continued — Team member management + a systemic role-badge bug

### Team member management — the real gap named in the previous round, now built
**Status:** FIXED

`GET /farms/{farm_id}/members` existed; nothing else did, on either web
or mobile. Added the missing operations:

- **`POST /farms/{farm_id}/members`** — add an existing FarmWise user to
  the farm by their email or phone number. Deliberately no separate
  pending-invite/accept flow, no new table, no email-sending
  integration: if nobody with that identifier has an account yet, the
  caller is told to have that person sign up first and try again. This
  keeps the feature to what the "add crud operations" ask actually
  needed, using the `crud.add_member`/`get_user_by_identifier` functions
  that already existed.
- **`PATCH /farms/{farm_id}/members/{member_id}`** — change a member's
  role among the four valid roles.
- **`DELETE /farms/{farm_id}/members/{member_id}`** — remove a member.

**Safeguard, load-bearing:** none of the three can ever target the
farm's schema-level owner (`farms.owner_id`) — checked before any
write, regardless of who's asking. Demoting or removing the owner
through this endpoint would orphan the farm with no
ownership-transfer flow to recover from it; that's a deliberately
separate, bigger decision than "manage my team" and wasn't built here.
All three restricted to the `farmer` role only — a `farm_manager` can
run daily operations but not reshape who's on the team.

**Duplicate-invite handling:** `crud.add_member` now catches the
`UNIQUE(farm_id, user_id)` constraint (added in Phase 3's migration
specifically anticipating this future endpoint — see that migration's
own comment) and returns a clean `409`, not a raw postgrest error.

**Frontend:** `settings.html`/`.js`'s existing (previously read-only)
Team Members panel now shows, for the farm's owner viewing it: an
inline role `<select>` and a remove button on every row except the
owner's own (which shows a plain "Owner" badge, no controls — matching
the backend's rule, not just hiding it), plus an "Add a team member"
form. Everyone else still sees the same read-only list as before.

**Verification:** `pytest tests/test_member_management.py` — 12 tests:
owner can invite/change-role/remove; a `farm_manager` and a `worker`
are both rejected (`403`) from all three; an unknown identifier, an
already-a-member conflict, and an invalid role are each rejected with
the right status; and — the two tests worth the most here — attempting
to change the owner's own role or remove the owner is rejected with
`400` before any write happens, verified by asserting the underlying
`crud` function was never called, not just by checking the response
code. 95/95 tests pass overall.

### FWA-032 — Every page hardcoded the role badge to "owner"
**Severity:** Low (display only, not a permission check) · **Files:** all 8 pre-existing page JS files · **Status:** FIXED

Found while wiring up `settings.js`'s new owner-only UI, which needed
to know the caller's real role — checked how the topbar's role badge
got its value and found `document.getElementById('roleBadge').textContent = 'owner';`,
a literal string, in every one of the 8 pages that existed before this
round. A worker or accountant would see "owner" in their own topbar.
Not a security issue (nothing was actually granted based on this label
— every real permission check happens server-side, verified extensively
throughout this whole audit), but a real, visible correctness bug.

**Fix:** changed to `activeFarm.my_role || ''` in all 8 files, matching
what `field-reports.js`/`export.js` (built in the previous round)
already did correctly.

**A second bug found while fixing the first:** in every one of those 8
files, the corrected line was placed *before* `const activeFarm = ...`
in the same function — a `const` temporal-dead-zone violation that
would throw `ReferenceError: Cannot access 'activeFarm' before
initialization` at runtime on every page load, silently caught by each
page's own top-level `try/catch` and surfaced as a generic "something
went wrong" error screen instead of the dashboard ever rendering.
`node --check` (a syntax check) does not catch this class of bug — it's
a runtime error, not a parse error. Caught by manually diffing each
file's line numbers for the two statements after the mechanical
find-and-replace, not by any automated tool; moved the corrected line
to after `renderFarmSwitcher(farms, activeFarm)` in all 8 files and
re-verified the ordering by line number, not just by re-running
`node --check` (which would have passed either way).

**Verification:** confirmed line-number ordering (`activeFarm` declared
before the `roleBadge` line) in all 8 files by direct inspection after
the fix; `node --check` re-run on all 10 frontend JS files as a
baseline syntax check (necessary but not sufficient, as just noted).

### Explicitly not built
**Ownership transfer** — deliberately excluded from this round, as
noted above. If the farm's actual owner needs to hand off the farm
entirely (e.g. genuinely leaving the business), that's a distinct,
higher-stakes operation deserving its own explicit confirmation flow,
not a side effect of the team-management PATCH.

---

# Post-Phase-9, continued — Closing the real CRUD gap

The user reported "no CRUD operations... no UI CRUD operation visible."
Investigated properly rather than assuming the report was mistaken:
built a real jsdom-based runtime harness (not just `node --check`, which
only catches syntax errors — the exact class of bug that slipped through
in the previous round) that loads each page's actual HTML and executes
its actual JS against a simulated backend, and confirmed all 10 pages
render correctly with zero runtime errors under a working connection.
That ruled out "the app is silently broken" — but it also led straight
to the real, valid version of the complaint.

### FWA-033 — Nearly every transactional entity had Create + List but no Update or Delete
**Severity:** High · **Status:** FIXED

Confirmed by reading the actual route files, not assumption: `sales`,
`expenses`, `income`, `feed_purchases`, `feed_consumption`,
`mortality_records`, `medication_records`, and `animal_batches`
themselves all had `POST` and `GET` only. A user who made a typo on any
sale, expense, feed purchase, or the batch's own name/purchase cost had
no way to fix it — not a misunderstanding, a real and significant gap
matching the report exactly.

**Fix — full backend CRUD for all eight entities**, 16 new endpoints:

- **Sales** — the one genuinely tricky case, since a sale decrements a
  batch's live stock at creation time. `PATCH` recomputes `total_amount`
  when quantity/price/discount change and reconciles the batch's stock
  by the *delta* (not the raw new value) if quantity changes; `DELETE`
  restores the full quantity sold. `batch_id` is deliberately not
  editable — moving a sale to a different batch means reconciling stock
  on two batches, a bigger operation than fixing a typo; the documented
  correct fix is delete-and-recreate, since each already reconciles its
  own batch correctly.
- **Mortality records** — same pattern: `quantity` isn't editable
  (deleting and recreating handles a wrong number correctly); `DELETE`
  restores the stock the record removed. Date/cause/notes are freely
  editable since they don't touch stock.
- **Animal batches** — `PATCH` for name/breed/purchase cost/supplier/
  dates/notes; `species` and `quantity_initial`/`quantity_current`
  are deliberately excluded (species defines what the batch fundamentally
  is; quantity only ever changes through the stock-reconciling paths).
  `DELETE` **soft**-deletes using the schema's `deleted_at` column,
  which already existed for exactly this but had never been wired to a
  route (flagged as a known gap back in Phase 3) — history
  (sales/mortality/medication/feed-consumption rows) is left alone and
  stays queryable by id.
- **Expenses, income, feed purchases, feed consumption, medication
  records** — straightforward `PATCH`/`DELETE`, no stock side effects.
  Feed purchases' `PATCH` recomputes `total_cost` when quantity/unit
  cost change, same principle as sales.

**A genuine correctness bug found and fixed while building this, not a
new one introduced:** `decrement_batch_quantity` (built in Phase 1) has
been subtly wrong since it was written. Its WHERE-clause guard
(`quantity_current >= amount`) correctly stopped stock from going
negative under concurrent writes, but did **not** stop a *lost update*:
if two concurrent decrements each individually passed their own
sufficiency check, the second to commit would still write its own
stale pre-read value, silently discarding the first decrement's effect
— e.g. a batch at 10 with two concurrent sales of 3 each could both
"succeed" but leave the batch at 7 instead of the correct 4. This was
never caught before because nothing had previously reused the function
in a way that exposed it; it surfaced while wiring sale/mortality
deletion to restore stock via the same function with a negative amount.
Rewrote it using the same optimistic-concurrency pattern already
proven correct in `crud/inventory.py`'s `adjust_stock` (guard on
`updated_at`, retry on a lost race), preserving the exact same function
signature and external behavior. A batch that auto-closed at zero and
then has stock restored (a deleted sale/mortality record) now correctly
reopens — a "closed" batch with a positive headcount was an
inconsistent state that literally could not happen before this pass
added stock-restoring callers.

**A second, unrelated bug found and fixed in the process:** a genuinely
obscure Python gotcha. Two new `PATCH` schemas needed a field literally
named `date` (matching its own type, `datetime.date`) with a default
value — `date: date | None = None` — which crashes at class-definition
time with `TypeError: unsupported operand type(s) for |: 'NoneType' and
'NoneType'`. Reproduced it in isolation to confirm the cause before
fixing: Python's bytecode for an annotated assignment with a default
evaluates the default assignment *before* the annotation expression, so
by the time `date | None` is evaluated, the name `date` has already
been rebound to `None` in the class's own namespace, self-shadowing the
imported type. Fixed with a type-only import alias
(`from datetime import date as _date`) used just for the annotation —
confirmed the actual field name (and JSON key) stays `"date"`,
unaffected.

**Verification:** `tests/test_crud_completeness.py` (24 tests) covers
every new endpoint, with the heaviest scrutiny on the stock-reconciling
paths — quantity decrease gives back the right delta, quantity increase
takes the right delta and can still fail cleanly on insufficient stock,
`batch_id` is confirmed unreachable through the update schema, and
delete restores the full original quantity. `tests/test_stock_concurrency.py`
was updated for the rewritten `decrement_batch_quantity` (3 existing
tests adapted, 4 new ones added) — including a test that specifically
reproduces the lost-update scenario from the docstring and asserts the
*correct* final quantity, not just that some update happened. **123
tests passing overall**, up from 99 at the end of the previous round.

### Frontend: edit/delete UI added for the three highest-traffic pages
**Status:** FIXED for finance.js, feed.js, animals.js — see note below on remaining pages

- **`finance.js`** — sales, expenses, and income tables now have ✏️/🗑️
  actions per row, reusing the existing create modals in an edit mode
  (pre-filled, `PATCH` instead of `POST`, `batch_id` locked on sales).
- **`feed.js`** — same pattern for purchases and consumption.
- **`animals.js`** — batch info edit/delete (manager-role gated,
  matching the backend), plus delete buttons on mortality and
  medication records in the batch detail view. Mortality/medication
  edit forms weren't built this pass (delete-and-recreate covers the
  "I made a mistake" case; a dedicated edit form for cause/notes or
  dosage/cost is a smaller, lower-priority follow-up).

**Verification methodology, upgraded this round:** installed `jsdom`
and built a real runtime test harness (`frontend_test/run_page.js`)
that loads each page's actual HTML, mocks a working backend, and
executes the actual JS in a simulated browser — not just a syntax
check. This is a direct response to the previous round's lesson: a
`const` temporal-dead-zone bug passed `node --check` cleanly while
crashing every single page load in a real browser. Ran this against
all 10 pages with realistic non-empty data (not just empty-list
happy paths) and zero runtime errors were found. For `animals.js`
specifically, went one step further and scripted an actual interaction
(open a batch's detail view, click Edit, confirm the form pre-fills
and `species` locks) rather than only checking that the page loads.

### Explicitly not done this pass, stated plainly
- **Inventory items, workers, and field reports already had full CRUD**
  before this pass (built in earlier rounds) — confirmed, not touched
  again unnecessarily.
- **Mortality and medication records only got Delete, not a dedicated
  Edit form** in the UI (the backend supports both). Delete-and-recreate
  is a reasonable interim path; a proper edit form is a quick, low-risk
  follow-up using the same pattern already established three times over
  in this pass.
- **No code was deleted or rewritten beyond what was needed** — every
  addition in this pass is new functions/routes/UI alongside what
  already existed; the one exception (`decrement_batch_quantity`'s
  internals) was a correctness fix to existing code that kept its exact
  external signature and contract, not a design change.

---

# Post-Phase-9, continued — Manual stock adjustment, worker record CRUD, UI polish

Follow-up to the previous round's CRUD pass. Three concrete asks:
owner-editable stock, closing any remaining CRUD gaps, and a UI polish
pass.

### New — manual batch stock adjustment
**Status:** FIXED

Inventory items already had a working "Adjust stock" feature
(confirmed before building anything, not assumed). Animal batches
didn't — the previous round deliberately locked `quantity_current` from
direct editing (only sales/mortality/delete-restore could change it) to
protect data integrity, but that left no path for a manual correction:
a miscount at creation, a physical recount, an animal that wandered
back and was never logged as a sale or a death.

**Fix:** `POST /farms/{farm_id}/animals/batches/{batch_id}/adjust` — a
signed delta (positive adds, negative removes) plus a **required**
reason. Reuses the exact same atomic, race-safe adjustment
(`crud.decrement_batch_quantity`) already proven correct for
sale/mortality stock restoration — a positive delta here is a negative
amount to that function, the same inversion already used by
`delete_sale`. Manager-role only, logged via the existing `audit()`
helper (`batch_stock_adjusted`, with the delta and reason) for the same
reason sensitive actions have been logged throughout this engagement:
an unexplained quantity change in a farm's history is exactly the kind
of thing worth being able to trace later.

**Frontend:** new "Adjust stock" button in the batch detail view,
alongside Edit and Delete. The reason field is required in both the
Pydantic model and the HTML form — a zero delta or an empty reason is
rejected before the request is even sent.

**Verification:** 6 new tests — adjustment up and down call the
underlying function with the correctly-signed and correctly-negated
amount; a zero delta and an empty reason are both rejected (`422`);
insufficient stock surfaces as a clean `409`, not a raw error; a worker
role is rejected (`403`).

### New — worker attendance and payment records had the same Create+List-only gap
**Status:** FIXED

Checked systematically for the same pattern found repeatedly last
round, in the one domain not yet re-checked: `routes/worker_routes.py`
had full CRUD for the worker profile itself (`PATCH`/`DELETE` already
existed), but `worker_attendance` and `worker_payments` only had `POST`
and `GET` — identical shape to the sales/expenses/batches gap from the
previous round, just not yet found in this domain.

**Fix:** `PATCH`/`DELETE` for both. Attendance's `update` reuses the
same `UNIQUE(worker_id, date)` conflict handling `record_attendance`
already has (catching the Postgres `23505` and surfacing a clean `409`
rather than a raw error) — editing a record's date into a collision
with an existing one is caught the same way a duplicate create is.
Payments stay manager-role only for both edit and delete, matching
their existing create restriction (attendance stays open to any
`_RECORD_ROLES` member, also matching its create restriction) — workers
logging their own attendance shouldn't be able to edit payroll.

**A second instance of the `date`-field self-shadowing bug (see the
previous round's writeup) found and fixed pre-emptively**: `AttendanceUpdate`
needed an optional `date` field with a default, the exact shape that
crashes at class-definition time. Applied the same type-alias fix
(`from datetime import date as _date`) before it could ever surface as
a runtime error, rather than discovering it the same way as last time.

**Frontend:** delete buttons on both the attendance and payment record
lists in the worker detail view — matching the same "delete and re-log"
pattern already established for mortality/medication records rather
than building two more full edit forms for what are simple day-stamped
entries.

**Verification:** 6 new tests covering update/delete for both, the
duplicate-date conflict handling, and the worker-role restriction on
payment edits.

### UI polish — replaced blocking alert() popups with toast notifications
**Status:** FIXED

All 11 remaining `alert(err.message)` calls — used for delete-error
feedback across the CRUD UI added in the last two rounds
(`finance.js`, `feed.js`, `animals.js`, `workers.js`) — replaced with a
small, auto-dismissing toast notification styled to match each page's
existing theme, instead of a blocking native browser dialog. Added
consistently across all four files with the same helper function and
matching CSS (`.toast-stack`/`.toast`/`.toast--error`).

### Verification summary
`pytest tests/` — **135 passed** (up from 123 at the end of the
previous round; two new test files, `test_stock_adjust_and_worker_crud.py`,
12 tests). `pyflakes` — zero findings outside intentional barrel
re-exports. `node --check` — all frontend `.js` files clean. The jsdom
runtime harness (built last round specifically because a syntax check
alone had missed a real bug) re-run against all 10 pages with
realistic data — zero runtime errors. HTML `<div>` balance re-checked
across all pages after the `animals.html` additions. Live boot test
confirms the app starts cleanly with the new routes registered.

### Explicitly not done this pass
- **No dedicated edit forms for mortality, medication, attendance, or
  payment records** — delete-and-recreate remains the path for fixing
  a mistake on these simpler, day-stamped entries. A real gap in
  convenience, not in capability; flagged consistently rather than
  silently left out.
- **The UI polish was scoped to the toast-notification change** — a
  concrete, low-risk, verifiable improvement — rather than a broader
  visual redesign, which risks the "don't touch working UI design" part
  of the request if done without very specific direction on what to
  change.

---

# Post-Phase-9, continued — Matching the mobile app's batch CRUD UI exactly

The user shared 10 screenshots of the mobile app's actual UI and asked
for the web version to match it. Real, concrete differences were found
by comparing screenshot-by-screenshot against the web code, not assumed.

### The core mismatch: mobile edits a batch through a single "Edit" tab; web had three separate controls
**Status:** FIXED

Mobile's batch detail view has three tabs — **Mortality | Medication |
Edit** — where "Edit" is a single form covering batch name, species,
breed, current count (labeled "Current count (was X of Y initial)"),
supplier, status, and notes, with one "Save changes" button. Web
instead had a separate row of "Edit batch info" / "Adjust stock" /
"Delete batch" buttons sitting outside the tabs, opening a reused
create-batch modal for edits and a second modal for stock adjustment —
three different affordances doing what mobile does in one screen.

**Fix — consolidated to match:**
- Batch detail modal tabs are now **Mortality | Medication | Edit**
  (plus **Profit**, a genuine web-only addition from Phase 4/5 that
  mobile's screenshots don't show — kept as an extra tab, not a
  deviation, since it's real functionality mobile doesn't have rather
  than something web was doing differently).
- The Edit tab is one form: batch name, species, breed, current count
  (with the exact "(was X of Y initial)" phrasing), supplier, status,
  notes, "Save changes." A "Delete batch" control sits at the bottom of
  the same tab (mobile's screenshots don't show a delete option at all,
  but removing a already-working, safe, soft-delete feature to match
  that absence would be a regression, not parity — kept it, just
  relocated to fit the new single-tab layout naturally).
- The standalone "Adjust stock" modal (built in the previous round) is
  gone as a separate UI element — its capability now lives in the Edit
  tab's "Current count" field instead, so there's exactly one way to
  do this, matching mobile.

**Backend change required to support this:** `AnimalBatchUpdate`
previously excluded `species` and `quantity_current` from `PATCH`
(a deliberate web-only design choice from the previous round, not a
data-integrity requirement — species doesn't cascade into any other
table's constraints). Both are now accepted. Critically, a
`quantity_current` change is **not** written as a raw overwrite: the
route computes the signed delta from the batch's current value and
routes it through the same atomic, race-safe
`crud.decrement_batch_quantity` used everywhere else a batch's count
changes (sales, mortality, the previous round's `/adjust` endpoint) —
so the correctness guarantees built up over the last several rounds
(no lost updates under concurrent writes, auto reopen/close on the
zero-quantity transition) are preserved even though the count is now
reachable from this one consolidated form. An explicit `status` choice
in the same save correctly overrides `decrement_batch_quantity`'s own
auto-close-at-zero logic, rather than being silently clobbered by it —
verified with a dedicated test for that exact ordering.

`quantity_initial` remains excluded — that's the fixed denominator
`batch_profit_summary`'s whole weighted-average costing model (Phase 4)
divides by; changing it retroactively would corrupt that model, and
nothing in the mobile screenshots suggests it should be editable either.

The standalone `POST .../adjust` endpoint from the previous round is
left in place, tested, and functional — just no longer exposed via its
own web UI element, since the Edit tab now covers the same need in the
way mobile does it.

**Verification:** 4 new/updated backend tests — species is now actually
applied (not silently dropped); a quantity change is confirmed routed
through `decrement_batch_quantity` with the correctly-signed delta, not
a direct write; leaving the count unchanged in a save that only touches
other fields correctly skips the adjustment path entirely (no spurious
audit-log entry or race-condition exposure for edits that never touched
stock); and the explicit-status-wins-over-auto-close ordering. **138
tests passing** overall.

### A second real gap: mobile lets you edit a mortality record, not just delete it
**Status:** FIXED

Screenshot 8 clearly shows an existing mortality record ("4 lost — Cold
weather") with inline **Edit** (green) and **Delete** (red) text links.
Web only had a delete icon — editing wasn't exposed at all, even though
the backend has supported it (`PATCH .../mortality/{record_id}`) since
the CRUD-completion round two sessions ago.

**Fix:** the mortality (and, for consistency, medication) record list
now renders "Edit"/"Delete" as colored text links matching mobile's
exact visual style, not icon buttons. Clicking Edit switches the
existing create-form into an edit-in-place mode: pre-filled, the
quantity field hidden (still not editable, for the same reason as
before — see `crud.update_mortality_record`'s docstring — delete and
re-record if the number itself was wrong), a Cancel button to back out,
and the submit button becomes `PATCH` instead of `POST`. Medication
records get the identical treatment.

**A related, smaller gap fixed in the same pass:** the mobile
screenshots (6, 7, 10) show the medication form has **Dosage** and
**Administered by** fields — web's form was missing both entirely (only
had Type, Name, Date administered, Next due date, Cost, Notes... and on
closer inspection, was missing Notes as a visible field too). Both
fields already existed on the backend (`medication_records.dosage`,
`administered_by`) and were simply never exposed in the web form. Added
both, plus the Notes field, bringing the form to full parity with
mobile's fields (excluding evidence photo/video attachments — see
"explicitly not done" below).

**Verification:** ran the actual page through a scripted interaction in
the jsdom harness — opened a batch's detail view, confirmed the Edit
tab's fields pre-fill correctly including the exact "(was 19 of 23
initial)" wording, clicked a mortality record's Edit link, and
confirmed the form correctly switches into edit mode with the quantity
field hidden and cause pre-filled. Zero runtime errors.

### Smaller parity fix: team-invite role descriptions
**Status:** FIXED

Mobile's "Add to team" role dropdown uses descriptive labels like
"Worker — field reports only" instead of a bare role name. Web's
dropdown just said "Worker". Rather than copying mobile's text
verbatim — which is actually inaccurate for this app's real permission
model (a worker here can log sales, expenses, feed, mortality, and
medication too, not just field reports, per every role check built
across this whole engagement) — wrote accurate descriptors in the same
style: "Worker — logs day-to-day records", "Farm manager — full
operations, no team changes", "Accountant — views finances only",
"Farmer — full access".

### Explicitly not done this pass, and why
- **Evidence photo/video attachments on mortality and medication
  records** — mobile's screenshots show Photo/Video/Library buttons on
  both forms. The database schema has no `media` column on
  `mortality_records` or `medication_records` at all (only
  `field_reports` supports media) — this would need a new migration and
  storage-upload wiring, not just frontend work. Flagged as a real gap,
  not built this pass given the scope already covered.
- **Dashboard "Quick actions" shortcut buttons** (Log sale, Log expense,
  New batch, Mortality) shown on mobile's dashboard — web's dashboard
  doesn't have equivalent shortcuts. Noted, not built this pass; the
  same actions are all one click away via each page's own "+" button
  already.
- **Three-way Dark/Light/System appearance setting** — mobile offers
  "System" (follow OS preference); web's toggle is binary (dark/light
  only). Noted, not built this pass.
- **Per-member role dropdown in the existing team list** (as opposed to
  the invite form) intentionally kept as plain role names, not the
  longer descriptive labels — that control sits inline in a compact
  per-row layout where the longer text would crowd the row; the invite
  form has the room for it.

### Verification summary
`pytest tests/` — 138 passed. `pyflakes` — zero findings outside
intentional barrel re-exports. `node --check` — all frontend files
clean. HTML `<div>` balance re-checked across every page. The jsdom
runtime harness re-run against all 10 pages, plus a dedicated scripted
interaction test specifically exercising the new consolidated Edit tab
and the mortality record edit-in-place flow — zero runtime errors.
Live boot test confirms the app starts cleanly with all 108 routes
registered (route count unchanged from the previous round — this pass
restructured existing endpoints' behavior and added no new routes,
only changed what one existing route, `PATCH .../batches/{batch_id}`,
accepts).
