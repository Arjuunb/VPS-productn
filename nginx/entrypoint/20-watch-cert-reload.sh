#!/bin/sh
# The certificate volume is read-only. Compare its mtime and render TLS only
# after a valid certificate is present; this keeps the HTTP ACME bootstrap safe.
last=""
while :; do
  cert=/etc/letsencrypt/live/trade-logx.com/fullchain.pem
  current="$(stat -c %Y "$cert" 2>/dev/null || true)"
  if [ -n "$current" ] && [ "$current" != "$last" ]; then
    cp /etc/nginx/templates/https.conf /etc/nginx/conf.d/default.conf
    nginx -s reload || true
    last="$current"
  fi
  sleep 60 & wait $!
done &
