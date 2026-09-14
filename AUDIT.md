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
