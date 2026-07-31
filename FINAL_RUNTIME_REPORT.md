# Final runtime report

## Execution environment

This macOS Codex environment has no Docker, Podman, Colima, nerdctl, or Lima
runtime. Docker Desktop was downloaded but macOS refused to mount its disk image
inside the sandbox (`hdiutil: attach failed - Device not configured`), so a
Docker daemon cannot be installed or started here.

## Results

| Subsystem | Status | Evidence |
|---|---|---|
| Docker image build | BLOCKED | No accessible Docker daemon/CLI. |
| `docker compose up -d` | BLOCKED | No accessible Docker daemon/CLI. |
| Container health | BLOCKED | Containers cannot be created locally. |
| FastAPI startup | BLOCKED | Must be exercised inside the production image. |
| Nginx proxy / frontend serving | BLOCKED | Must be exercised with Compose network. |
| Dashboard TypeScript build | PASS | `npm --prefix automation-hub-dashboard run build:check` passed. |
| Landing TypeScript build | BLOCKED | This environment cannot resolve/download several missing npm packages. Docker's `npm ci` remains the reproducible validation path on the VPS. |
| Health endpoints | BLOCKED | Require the running image. |
| API endpoints | BLOCKED | Require the running image. |
| Authentication | BLOCKED | Require the running image and session-cookie flow. |
| User settings persistence | BLOCKED | Require the running image plus volume restart test. |
| SQLite persistence | BLOCKED | Require the running image plus volume restart test. |
| Trading engine | BLOCKED | Require the running image. |
| Paper trading | BLOCKED | Require the running image. |
| Bot manager initialization | BLOCKED | Require the running image. |
| Startup log exceptions | BLOCKED | Require the running image. |

## Required Ubuntu 24.04 / Hostinger validation

After installing Docker Engine and the Compose plugin, copy this directory,
create `.env` from `.env.example` with unique secrets, and run:

```sh
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1/health
curl -fsS http://127.0.0.1/api/v1
curl -fsSI http://127.0.0.1/
docker compose logs --tail=200 app nginx
```

Expected: app is `healthy`, Nginx is `running`, health returns HTTP 200 with
`status: ok`, API version returns HTTP 200 with `ok: true`, and `/` returns
HTTP 200 through Nginx. Sign in through the rendered landing/dashboard using
`HUB_USERNAME` and `HUB_PASSWORD`; update a setting, execute `docker compose
restart app`, and verify it persists. Check authenticated `/engine/status` for
the auto-started paper engine and use the dashboard's paper workflow to produce
and inspect a simulated paper trade. The app log must contain no traceback or
startup exception.

The application is **not runtime-validated** until the above commands run on a
Docker-capable host. This report intentionally does not represent blocked work
as a PASS.
