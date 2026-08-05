# Tradexa Trading Bot — Landing + Authentication

A premium, dark-luxury marketing site and complete authentication experience for
the Tradexa Trading Bot. Design language: **Bloomberg Terminal precision ×
Apple restraint × Linear polish** — institutional-grade, minimal, fast.

> Tagline: **Automated Trading. Human Intelligence.**

## Stack

- **React 18 + TypeScript + Vite** (fast build, code-split routes)
- **TailwindCSS** with a bespoke dark-luxury token set (black `#08080A`, gold
  `#C8A94B`, emerald, soft red)
- **Framer Motion** — page transitions, scroll reveals, count-ups, toasts
- **React Hook Form + Zod** — typed forms with live inline validation
- **Supabase** — production authentication (email/password, verification,
  reset, session refresh, Google/Apple when enabled, and MFA support)
- **Lucide** icons · shadcn-style UI primitives (hand-owned in `components/ui`)

## What's inside

**Landing** (`/`): Navbar · Hero with an animated dashboard preview · Features ·
How it works · Product screenshots · Performance metrics · Security · Footer.

**Auth** (`/auth/*`): `login`, `register`, `forgot-password`, `reset-password`,
`verify-email`, `two-factor`, `session-expired`.

## Getting started

```bash
npm install
cp .env.example .env      # optional — add Supabase keys to go live
npm run dev               # http://localhost:5175
npm run build             # type-check + production build
npm run preview
```

### Authentication

There is no demo account or local password fallback. The Docker deployment
injects the public Supabase URL/anon key at runtime; credentials and the
service-role key never enter the frontend. See the repository-level
`DEPLOYMENT_VPS.md` for the required Supabase SQL migration and setup:

```env
VITE_SUPABASE_URL=https://YOUR-PROJECT.supabase.co
VITE_SUPABASE_ANON_KEY=your-anon-key      # safe to expose in the browser
VITE_APP_URL=/app
```

The `anon` key is designed to be public; keep the `service_role` key server-side
only. Enable Google and/or Apple in Supabase, then set the corresponding
`HUB_AUTH_GOOGLE_ENABLED` / `HUB_AUTH_APPLE_ENABLED` server flag to show a button.

## Design & integrity notes

- **No fabricated metrics.** The dashboard/screenshot visuals are clearly
  labelled **preview · demo data** and never presented as a live account or a
  real track record. The performance figures are stated as engineering targets,
  not return guarantees.
- **Accessible & responsive:** keyboard-navigable, focus-visible rings, reduced-
  motion support, semantic labels, mobile-first layouts.
- **Fast:** auth pages are lazy-loaded; the landing ships the minimal bundle.

## Deploy

**Single-origin deployment.** The repository `Dockerfile` bundles this site
and the trading dashboard behind the backend, exposed by the Compose Nginx proxy:

- this landing/auth/settings SPA is served at **`/`**, `/auth/*`, `/settings/*`
- the session-gated dashboard is served at **`/app`** (built with `DASHBOARD_BASE=/app/`)

`VITE_APP_URL` defaults to `/app` and the dashboard's brand links back to `/`, so
`Launch Bot` and the "Automation Hub" logo move between the two with no extra
config. Just deploy the image and everything is on the bot's URL.

Deploy with `docker compose up -d --build` on the Ubuntu VPS. Do not deploy the
auth UI separately: same-origin HttpOnly session cookies are part of the
security model.
