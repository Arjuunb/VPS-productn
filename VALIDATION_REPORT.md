# Final validation report

## Local validation

| Subsystem | Status | Evidence |
|---|---|---|
| Python source syntax | PASS | `PYTHONPYCACHEPREFIX=/tmp python3 -m compileall -q automation-hub bot tradexa` |
| Dashboard TypeScript | PASS | `npm --prefix automation-hub-dashboard run build:check` completed successfully. |
| Dashboard production bundle | PASS | Vite generated `automation-hub-dashboard/dist/index.html` and assets. |
| Landing TypeScript / production bundle | BLOCKED | Required locked npm packages could not be resolved in this environment; validate through the Docker image on the VPS. |
| Dockerfile / Compose static validation | PASS | Compose uses an internal app port, health probe, persistent volume, bounded logs, non-root app process and Nginx proxy. |
| Docker image build | NOT RUN | Docker CLI/daemon is not installed in this environment. |
| Compose startup / container health | NOT RUN | Docker CLI/daemon is not installed in this environment. |
| FastAPI / Nginx HTTP runtime | NOT RUN | Requires the unavailable Docker runtime. |
| Authentication / settings persistence / simulated trading | NOT RUN | Requires the unavailable Docker runtime. |

## Required VPS validation

On a clean Ubuntu 24.04/Hostinger VPS with Docker Engine and Compose installed:

```sh
cp .env.example .env
# Set all CHANGE_ME values to distinct secrets, then:
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1/health
curl -fsS http://127.0.0.1/api/v1
curl -fsSI http://127.0.0.1/ | head
docker compose logs --tail=200 app nginx
```

Expected results: `app` is `healthy`, `nginx` is `running`; `/health` returns
JSON with `"status":"ok"`; `/api/v1` returns `"ok":true`; `/` returns HTTP
200 through Nginx. Then sign in using `HUB_USERNAME`/`HUB_PASSWORD`, change a
setting, restart with `docker compose restart app`, and confirm it remains;
the `tradexa-data` named volume is the persistence boundary. Confirm the paper
engine is running with authenticated `GET /engine/status`, and validate an
intentional paper simulation through the dashboard or the authenticated API.

No production claim is made for NOT RUN checks until this exact sequence passes
on a Docker-capable host.
