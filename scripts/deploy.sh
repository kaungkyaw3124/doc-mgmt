#!/usr/bin/env bash
# deploy.sh — update a server: git pull -> backup -> rebuild -> restart nginx
# -> wait for the gateway. The backup is taken BEFORE anything is rebuilt, and
# the deploy stops if it fails. Never removes volumes or data.
#
# Usage: ./scripts/deploy.sh
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$REPO_ROOT/infra/docker-compose.yml")
cd "$REPO_ROOT"

git pull --ff-only
export APP_COMMIT="$(git rev-parse --short HEAD)"

# Only backup-service is (re)built first — so the very first deploy of this
# feature can back up too — the rest of the running app is left as it is.
"${COMPOSE[@]}" up -d --build --no-deps backup-service
for _ in $(seq 30); do
    "${COMPOSE[@]}" exec -T backup-service true 2>/dev/null && break
    sleep 2
done
./scripts/backup.sh

"${COMPOSE[@]}" up -d --build
"${COMPOSE[@]}" restart nginx   # nginx caches upstream IPs

for _ in $(seq 60); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/)" = "200" ] && { echo "deployed $APP_COMMIT: http://localhost:8080"; exit 0; }
    sleep 2
done
echo "gateway did not answer 200 within 2 minutes — check: docker compose -f infra/docker-compose.yml ps" >&2
exit 1
