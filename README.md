# FarmWise AI — Web Frontend (Phase 1)

Phase 1 of the web build: project scaffold, marketing landing page, and the
auth screens (login, register, forgot password, reset password). This is
static HTML/CSS/JS designed to sit in front of your existing FastAPI +
Supabase backend — no backend code was touched or regenerated.

## Structure

```
frontend/
├── index.html              Landing page (hero, features, benefits, pricing,
│                            testimonials, FAQ, contact, CTA, footer)
├── assets/
│   ├── css/
│   │   ├── main.css         Design tokens + landing page styles
│   │   └── auth.css         Auth screen layout (split brand/form panel)
│   ├── js/
│   │   ├── config.js         API_BASE_URL — point this at your Render backend
│   │   ├── auth.js            Auth API client (register/login/forgot/reset/
│   │   │                      refresh/logout), token storage, remember-me
│   │   └── main.js            Landing page interactions (nav, FAQ, scroll reveal)
│   └── images/
├── pages/
│   ├── login.html
│   ├── register.html
│   ├── forgot-password.html
│   └── reset-password.html
├── partials/                (reserved — shared header/sidebar for dashboard, Phase 2)
└── dashboard/                (empty — Phase 2)
```

## Design

Agricultural palette without the generic cream+terracotta AI look: deep
canopy green, warm maize gold, soil brown, pasture green, warm parchment
background. Fraunces for display type, Inter for body/UI, JetBrains Mono for
stat/data readouts. Signature motif: a "furrow" divider (angled parallel
lines, like plowed rows) used between sections instead of a plain rule.

## What `auth.js` expects from the backend

Update `assets/js/config.js` with your real Render URL. The client calls:

| Endpoint                     | Method | Body                              |
|-------------------------------|--------|-----------------------------------|
| `/auth/register`              | POST   | `full_name, email, password, farm_name, role` |
| `/auth/login`                 | POST   | `email, password` → `access_token, refresh_token, user` |
| `/auth/forgot-password`       | POST   | `email`                           |
| `/auth/reset-password`        | POST   | `token, new_password`             |
| `/auth/refresh`               | POST   | `refresh_token`                   |
| `/auth/logout`                | POST   | (Bearer token)                    |

If your existing backend's routes or field names differ, adjust `auth.js`
rather than the backend — it's a thin client.

"Remember me" stores tokens in `localStorage`; unchecked, it uses
`sessionStorage` (cleared when the tab closes).

## Next steps (not built yet)

- `dashboard/` shell + sidebar/topbar partial, wired to a real JWT session
- Executive dashboard (animals, stock, income, expenses, profit, charts)
- Farm / animal / feed / sales / expense / inventory module screens
- AI assistant chat UI
- Reports & analytics screens with PDF/Excel/CSV export

## Running locally

No build step — open `frontend/index.html` directly, or serve the folder
with any static server, e.g.:

```
cd frontend
python3 -m http.server 5500
```
