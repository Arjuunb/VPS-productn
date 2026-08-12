#!/usr/bin/env sh
set -eu

# Verify FastAPI directly inside its private container network. Checking the
# public HTTP /health route alone can return a successful redirect without ever
# reaching the application after HTTPS is enabled.
docker compose exec -T app python -c \
  "import json, urllib.request; payload=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=10)); assert payload.get('status') == 'ok', payload; print('FastAPI health: OK')"

# Verify that the public reverse-proxy process is serving its local liveness
# route. This path intentionally remains available before and after TLS setup.
curl --fail --silent --show-error http://127.0.0.1/nginx-health | grep -q '^ok$'
printf '%s\n' 'Nginx health: OK'
