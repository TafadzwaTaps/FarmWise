# FarmWise AI — Frontend (WaziBot-style restructure)

Restructured to match the WaziBot frontend's actual convention: a **flat**
folder of self-contained pages, not `assets/css` + `assets/js` + `pages/`
subfolders. Simple pages (landing, login, signup, forgot-password) embed
their own `<style>` and `<script>` inline, one file, nothing to wire up.
Only the complex app page (`dashboard`) gets matching external `.css`/`.js`
files, same as WaziBot's `dashboard.html` + `dashboard.css` + `dashboard.js`.

## Structure

```
landing.html          — marketing page (was index.html)
signup.html            — was pages/register.html
login.html
forgot-password.html    — two-step OTP flow (request code → confirm)
reset-password.html      — thin redirect into forgot-password.html
dashboard.html             — app shell (Phase 2 seed)
dashboard.css
dashboard.js
```

No `assets/`, `pages/`, or `partials/` folders — every file sits at the
root, exactly like WaziBot's `static/`.

## Theme

Vibrant, not the muted agricultural palette from the previous version:
- `--green` (#39e75f) — primary, grass-vivid rather than muted sage
- `--mango` (#ff8a00) — CTA/accent, harvest-orange, high-contrast against the dark bg
- `--sky` (#00c2ff) — reserved for future info/links
- Dark theme by default (`--bg:#0a1a10`), with a light-mode toggle
  (`data-theme="light"` on `<html>`, persisted to `localStorage` under
  `farmwise_theme`) — same toggle mechanism WaziBot uses.
- Fonts: Space Grotesk (display/body) + Space Mono (data/labels/tags) —
  a different pairing from WaziBot's Syne + DM Mono, so FarmWise doesn't
  look like a reskin, while following the same "bold display + mono
  accent" formula.

Each page duplicates its own `:root` token block rather than importing a
shared stylesheet — matching WaziBot's per-file convention (no shared
`main.css`). If you'd rather centralize the tokens, that's a deliberate
trade-off to revisit, not an oversight.

## Backend contract (unchanged)

Still wired to the WaziBot-style FastAPI backend from before: `/auth/signup`,
`/auth/login` (identifier + remember_me), `/auth/me`, OTP-based
`/auth/password-reset/*`, and `/farms`. Update the inline `const API = ...`
near the bottom of each page's `<script>` (or `dashboard.js`) to point at
your deployed backend — there's no separate `config.js`, matching WaziBot's
per-page `const API = '...'` convention.

localStorage keys: `farmwise_token`, `farmwise_refresh`, `farmwise_user`,
`farmwise_theme` (WaziBot's equivalents are `wazi_token` etc.).

## Verified

- All internal links (`login.html`, `signup.html`, etc.) resolve to files
  that exist in this package.
- Every HTML file has balanced tags (checked programmatically).
- Every inline `<script>` block and `dashboard.js` pass `node --check`
  (syntax-valid).
- Not yet tested against a live backend/browser — do a manual click-through
  of signup → login → dashboard before shipping.

## Running locally

```bash
python3 -m http.server 5500
# → http://localhost:5500/landing.html
```
