#!/bin/sh
# Generates a self-signed TLS certificate/key for Nginx's HTTPS listener
# (infra/nginx/nginx.conf.template's `listen 443 ssl` block) if one
# doesn't already exist at /etc/nginx/certs — idempotent, so a container
# restart doesn't regenerate it (and doesn't re-trigger the browser's
# "new certificate" trust prompt) as long as the certs volume persists
# (see infra/docker-compose.yml's nginx service).
#
# SERVER_NAME should be set (via infra/.env) to whatever host/IP clients
# actually use to reach this deployment — a private VM's IP is normal
# here, since there's no public DNS name to get a real (Let's Encrypt)
# certificate for. It becomes the certificate's Subject Alternative
# Name, which is what browsers check against the URL bar's host; a
# mismatch there is what "insecure/invalid certificate" warnings
# actually flag, not the fact that it's self-signed. Browsers still show
# a one-time trust prompt for a self-signed cert regardless — that part
# is expected and has to be accepted once per browser/device.
set -eu

CERT_DIR="/etc/nginx/certs"
CERT_FILE="$CERT_DIR/fullchain.pem"
KEY_FILE="$CERT_DIR/privkey.pem"
SERVER_NAME="${SERVER_NAME:-localhost}"

if [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]; then
    exit 0
fi

command -v openssl >/dev/null 2>&1 || apk add --no-cache openssl >&2

mkdir -p "$CERT_DIR"

# SERVER_NAME may be an IP (a private VM's address — the common case
# this script exists for) or a hostname; openssl's subjectAltName needs
# to know which. Good enough heuristic for IPv4 — doesn't attempt to
# detect IPv6.
case "$SERVER_NAME" in
    ''|*[!0-9.]*) SAN="DNS:$SERVER_NAME" ;;
    *) SAN="IP:$SERVER_NAME" ;;
esac

echo "Generating a self-signed TLS certificate for '$SERVER_NAME' ($SAN) — browsers will show a one-time trust warning; expected for a private deployment with no public domain. Set SERVER_NAME in infra/.env to the host/IP you actually use to reach this deployment." >&2

openssl req -x509 -nodes -newkey rsa:2048 \
    -days 3650 \
    -keyout "$KEY_FILE" \
    -out "$CERT_FILE" \
    -subj "/CN=$SERVER_NAME" \
    -addext "subjectAltName=$SAN"
