# Production audit

## Architecture

The repository contains a Python trading core (`bot` and `tradexa`), the
FastAPI Automation Hub (`automation-hub`), a React operator dashboard, and a
React landing/settings application. The FastAPI process hosts both built SPAs,
the paper-trading engine, persistence stores, and versioned API routes. Live
execution remains hard-locked by the existing application logic.

## Findings addressed

| Finding | Resolution |
|---|---|
| Render/Vercel deployment assumptions | Added Compose/Nginx VPS topology and VPS runbook; no provider configuration is required at runtime. |
| Root container and ephemeral app storage | Docker image now runs as a dedicated non-root user and writes state to `/var/lib/tradexa`, mounted as a named volume. |
| No service orchestration/health/restart policy | Added `compose.yaml` with health checks, dependency ordering, restart policies, and bounded container logs. |
| Public app directly exposed | Nginx is the sole exposed service; FastAPI is internal to the Compose network. |
| Control credential embedded in browser HTML | Removed credential injection from runtime React config. Signed session authentication now supplies the internal control credential only server-side for authenticated requests. |
| Production detection only recognised cloud vendors | `HUB_ENV=production` now activates the existing fail-closed default-secret checks on a VPS. |

## Residual operational requirements

- Configure unique secrets in `.env`; the application deliberately refuses its
  insecure default session secret in production.
- Use a TLS terminator for a public deployment and set the real HTTPS origin in
  `HUB_CORS_ORIGINS`.
- Docker was unavailable in this development environment, so image build,
  Compose startup, restart behavior, and browser acceptance tests must be run
  on the target Ubuntu host using the commands in `DEPLOYMENT_VPS.md`.
- Node dependencies were not installed locally, so React type/build validation
  requires `npm ci && npm run build:check` in each frontend (the Docker build
  performs this reproducibly).

## Files modified

- `Dockerfile`: non-root runtime, persistent data location, proxy-aware Uvicorn.
- `automation-hub/app.py`: VPS production guard and no browser secret exposure.
- `automation-hub-dashboard/src/lib/api.ts`: cookie-authenticated browser calls.
- `.gitignore`: allows the safe `.env.example` template to be versioned.
- `compose.yaml`, `nginx/nginx.conf`, `.env.example`, `scripts/*`, and
  `DEPLOYMENT_VPS.md`: VPS deployment assets.
