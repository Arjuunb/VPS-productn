#!/usr/bin/env sh
set -eu
[ -f .env ] || { echo 'Missing .env. Copy .env.example and set unique secrets.' >&2; exit 1; }

# Compose's env_file is allowed to contain blank deployment metadata. Export
# authoritative values from the checked-out revision so those blanks cannot
# hide the version that is actually being deployed.
GIT_COMMIT="${GIT_COMMIT:-$(git rev-parse HEAD 2>/dev/null || printf unknown)}"
GIT_BRANCH="${GIT_BRANCH:-$(git branch --show-current 2>/dev/null || printf unknown)}"
DEPLOYED_AT="${DEPLOYED_AT:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
export GIT_COMMIT GIT_BRANCH DEPLOYED_AT

docker compose config --quiet
docker compose up -d --build --remove-orphans --wait --wait-timeout 180
docker compose ps
./scripts/healthcheck.sh
