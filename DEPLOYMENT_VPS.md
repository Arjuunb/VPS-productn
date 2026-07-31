# Ubuntu 24.04 / Hostinger VPS deployment

Install Docker Engine and the Docker Compose plugin on the VPS, then copy this
working directory to the server.  No cloud platform configuration is required.

1. `cp .env.example .env`
2. Replace every `CHANGE_ME` value with a unique random secret (for example,
   `openssl rand -hex 32`). Set `HUB_CORS_ORIGINS` to the final public origin.
3. Run `docker compose up -d --build`.
4. Verify `docker compose ps` reports both services healthy/running and run
   `./scripts/healthcheck.sh`.

The `tradexa-data` named volume is the durable state store. Back it up before
upgrades with `docker run --rm -v tradexa-trading-bot-production_tradexa-data:/data -v "$PWD":/backup alpine tar czf /backup/tradexa-data.tgz /data`.

Nginx exposes HTTP on port 80. Put a TLS terminator in front of it (Hostinger's
managed proxy, or a certificate-enabled Nginx configuration) before public
internet use; set `HUB_CORS_ORIGINS` to the resulting HTTPS origin.

Useful operations:

```sh
docker compose logs -f --tail=200
docker compose restart app
docker compose exec app python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read())"
```

The compose configuration uses `restart: unless-stopped`, bounded local logs,
health checks, a non-root application user, a persistent volume, and an
internal-only FastAPI port.
