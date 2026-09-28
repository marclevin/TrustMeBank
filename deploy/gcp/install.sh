#!/usr/bin/env bash
# Runs as root on the VM (Debian 12). Installs Docker and Caddy, unpacks the source bundle into
# /opt/trustmebank, writes .env on first install, and starts the stack. Idempotent.
set -euo pipefail

DOMAIN=${DOMAIN:?DOMAIN is required}
BUNDLE=${BUNDLE:?BUNDLE (path to source tar.gz) is required}
APP_DIR=/opt/trustmebank

export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null; then
  echo "--> Installing Docker"
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl >/dev/null
  curl -fsSL https://get.docker.com | sh >/dev/null
fi
if ! command -v caddy >/dev/null; then
  echo "--> Installing Caddy"
  apt-get install -y -qq caddy >/dev/null
fi

echo "--> Unpacking source into $APP_DIR"
mkdir -p "$APP_DIR"
find "$APP_DIR" -mindepth 1 -maxdepth 1 ! -name .env -exec rm -rf {} +
tar -xzf "$BUNDLE" -C "$APP_DIR"
rm -f "$BUNDLE"

if [ ! -f "$APP_DIR/.env" ]; then
  echo "--> Writing .env with fresh secrets"
  SECRET_KEY=$(head -c 48 /dev/urandom | base64 | tr -d '\n=/+')
  ADMIN_PASSWORD=$(head -c 18 /dev/urandom | base64 | tr -d '\n=/+')
  POSTGRES_PASSWORD=$(head -c 24 /dev/urandom | base64 | tr -d '\n=/+')
  cat > "$APP_DIR/.env" <<EOF
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
SECRET_KEY=$SECRET_KEY
ADMIN_PASSWORD=$ADMIN_PASSWORD
PUBLIC_BASE_URL=https://$DOMAIN
SESSION_COOKIE_SECURE=true
SEED_ON_STARTUP=true
# Bind the app to loopback only; Caddy terminates HTTPS and proxies to it.
TRUSTMEBANK_PORT=127.0.0.1:8000
EOF
  chmod 600 "$APP_DIR/.env"
else
  echo "--> Keeping existing .env; updating PUBLIC_BASE_URL"
  sed -i "s#^PUBLIC_BASE_URL=.*#PUBLIC_BASE_URL=https://$DOMAIN#" "$APP_DIR/.env"
fi

echo "--> Configuring Caddy for $DOMAIN"
cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
EOF
systemctl enable --now caddy >/dev/null
systemctl reload caddy

echo "--> Building and starting containers"
cd "$APP_DIR"
docker compose up -d --build --remove-orphans

echo "--> Waiting for the app"
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null http://127.0.0.1:8000/health; then
    echo "--> App is healthy"
    exit 0
  fi
  sleep 3
done
echo "App did not become healthy; last log lines:" >&2
docker compose logs --tail=40 app >&2
exit 1
