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

## Production HTTPS: `trade-logx.com` and `www.trade-logx.com`

The Compose deployment includes Nginx, Certbot, a persistent `letsencrypt`
volume, and a persistent shared ACME webroot. Certificates and private keys
never enter the repository or the application container.

Run these commands on the VPS after pulling the HTTPS changes:

```sh
cd /opt/VPS-productn
git pull --ff-only origin main
cp -n .env.example .env
nano .env
# Set HUB_CORS_ORIGINS=https://trade-logx.com,https://www.trade-logx.com
# Replace every CHANGE_ME value before continuing.

docker compose config
docker compose up -d --build app nginx certbot
docker compose ps
```

Before requesting the certificate, verify that both DNS records resolve to
`2.24.141.144` and that port 80 reaches this VPS. The initial Nginx config
continues proxying normal HTTP traffic and serves the ACME path:

```sh
docker compose run --rm --entrypoint sh certbot -c \
  'mkdir -p /var/www/certbot/.well-known/acme-challenge && printf acme-ok > /var/www/certbot/.well-known/acme-challenge/health'
curl -fsS http://trade-logx.com/.well-known/acme-challenge/health
curl -fsS http://www.trade-logx.com/.well-known/acme-challenge/health
```

The two requests must print `acme-ok`. Then issue the first certificate (use an
email address you control):

```sh
docker compose run --rm --entrypoint certbot certbot certonly --webroot \
  -w /var/www/certbot \
  -d trade-logx.com -d www.trade-logx.com \
  --email YOUR_EMAIL@example.com --agree-tos --no-eff-email

# Switch immediately to the HTTPS config; the watcher also reloads after future renewals.
docker compose restart nginx
```

Validate the completed deployment:

```sh
docker compose config
docker compose ps
curl -fsSI https://trade-logx.com/
curl -fsSI https://www.trade-logx.com/
curl -sSI http://trade-logx.com/ | grep -Ei 'HTTP/|location:'
curl -sSI http://www.trade-logx.com/ | grep -Ei 'HTTP/|location:'
echo | openssl s_client -connect trade-logx.com:443 -servername trade-logx.com 2>/dev/null | openssl x509 -noout -issuer -subject -dates
docker compose logs --tail=100 nginx certbot app
```

Expected results: both HTTPS URLs return a successful response; both HTTP URLs
return `301` with an `https://` location; certificate dates show a valid
Let's Encrypt certificate; and `app` is healthy while `nginx` is healthy/running.
The Certbot container runs `renew` every 12 hours. Nginx detects an updated
certificate and reloads within 60 seconds. Test renewal without consuming a
certificate issuance limit:

```sh
docker compose run --rm --entrypoint certbot certbot renew --dry-run --webroot -w /var/www/certbot
```

Useful operations:

```sh
docker compose logs -f --tail=200
docker compose restart app
docker compose exec app python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read())"
```

The compose configuration uses `restart: unless-stopped`, bounded local logs,
health checks, a non-root application user, persistent trading and certificate
volumes, an internal-only FastAPI port, and only Nginx publishes ports 80/443.
