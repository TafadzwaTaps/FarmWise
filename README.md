# FarmWise AI

A merged deployment: FastAPI backend + frontend served from one app, one
Render service, matching WaziBot's approach — see `backend/README.md` for
full details.

## What changed from your last upload

Your frontend was previously deployed as a separate Render Static Site,
which is where the "not found" / blank-page issues came from (wrong URL,
missing `index.html`, and `CORS_ORIGINS` needing to track a second URL).

This version merges them:

- `frontend/` is gone — its files now live in `backend/static/`
- `backend/main.py` mounts `static/` and serves each page at a clean URL
  (`/`, `/login`, `/signup`, `/forgot-password`, `/reset-password`,
  `/dashboard`) via `FileResponse`, the same pattern WaziBot's `main.py` uses
- Every internal link and JS redirect was rewritten from relative filenames
  (`login.html`) to absolute clean paths (`/login`)
- Every page's `const API` now points at `/api/v1` (same-origin) instead of
  branching on hostname to guess a cross-origin backend URL

**Deploy just the one Render Web Service now** (Root Directory: `backend`).
If you still have the separate Static Site from before, it's no longer
needed — you can delete it once this is live.

## Verified before packaging

- `main.py` compiles cleanly
- All 6 HTML pages still have balanced tags after the link rewrite
- All inline/external JS passes `node --check`
- Full integration test (FastAPI `TestClient`, fake Supabase backend):
  every page route (`/`, `/login`, `/signup`, `/forgot-password`,
  `/reset-password`, `/dashboard`) returns 200 with the expected content,
  `/static/dashboard.css` and `/static/dashboard.js` serve correctly,
  and the full API flow (signup → login → `/auth/me` → create farm) still
  works — all on one origin, no CORS involved
