#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# TLS certificate bootstrap
#
# The container looks for a certificate in /etc/nginx/certs/:
#   cert.pem  — certificate (chain for CA-signed; self-signed for dev)
#   key.pem   — private key
#
# Mount your CA-signed cert via docker-compose (or -v flag):
#   volumes:
#     - ./nginx/certs:/etc/nginx/certs
# Then copy your files:
#   cp /path/to/fullchain.pem  ./nginx/certs/cert.pem
#   cp /path/to/privkey.pem    ./nginx/certs/key.pem
#
# Let's Encrypt (Certbot):
#   certbot certonly --standalone -d yourdomain.com
#   cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem ./nginx/certs/cert.pem
#   cp /etc/letsencrypt/live/yourdomain.com/privkey.pem   ./nginx/certs/key.pem
#   # Add a cron job: certbot renew && docker compose restart nginx
#
# If no cert is found, a self-signed certificate is generated automatically
# (suitable for development / initial testing only — browsers will warn).
# ─────────────────────────────────────────────────────────────────────────────

CERT=/etc/nginx/certs/cert.pem
KEY=/etc/nginx/certs/key.pem

mkdir -p /etc/nginx/certs

if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
    echo "[nginx-entrypoint] No certificate found — generating self-signed TLS cert (DEV ONLY)..."
    openssl req -x509 -nodes -days 730 -newkey rsa:2048 \
        -keyout "$KEY" \
        -out "$CERT" \
        -subj "/C=US/ST=Local/L=Local/O=AgenticPlatform/CN=localhost" \
        -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
        2>/dev/null
    echo "[nginx-entrypoint] Self-signed certificate generated at $CERT"
    echo "[nginx-entrypoint] Replace with a CA-signed cert for production (see comments above)."
else
    echo "[nginx-entrypoint] Using existing certificate at $CERT"
fi

exec "$@"
