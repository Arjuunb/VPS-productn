#!/usr/bin/env sh
set -eu
[ -f .env ] || { echo 'Missing .env. Copy .env.example and set unique secrets.' >&2; exit 1; }
docker compose config --quiet
docker compose up -d --build --remove-orphans --wait --wait-timeout 180
docker compose ps
./scripts/healthcheck.sh
