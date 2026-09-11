#!/bin/sh
# Renders nginx.conf.template into the real config and starts Nginx.
# Split out of docker-compose.yml's entrypoint (which used to be a single
# envsubst one-liner) so the Task 7 CORS production validation below has
# somewhere to run — see validate-cors-config.sh.
set -eu

SCRIPT_DIR="$(dirname "$0")"
"$SCRIPT_DIR/validate-cors-config.sh"
"$SCRIPT_DIR/generate-self-signed-cert.sh"

# envsubst is invoked with an explicit variable list, so it does NOT
# touch Nginx's own $variables ($host, $uri, $upstream_http_..., etc.) in
# the template — only these two placeholders are replaced. See the
# comment at the top of nginx.conf.template.
envsubst '$INTERNAL_SHARED_SECRET $CORS_ALLOWED_ORIGIN' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf
exec nginx -g 'daemon off;'
