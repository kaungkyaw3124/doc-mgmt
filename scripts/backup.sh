#!/usr/bin/env bash
# backup.sh — take a backup now: backups/backup-YYYY-MM-DD-HHMM.tar.gz, then
# keep only the newest $KEEP (default 14) backup-* files. Used by the nightly
# cron (install-backup-cron.sh) and by deploy.sh before every rebuild. The
# work is done by backup-service's engine — the same code the web UI uses.
#
# Usage: ./scripts/backup.sh            (KEEP=14 by default)
# Env:   KEEP, COMPOSE_PROJECT_NAME / COMPOSE_OVERRIDE (other stacks).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$REPO_ROOT/infra/docker-compose.yml" ${COMPOSE_OVERRIDE:+-f "$COMPOSE_OVERRIDE"})
echo "$(date '+%F %T') backup starting"
# A just-started container clears its temp folder on startup; wait until it is serving.
for _ in $(seq 60); do
    "${COMPOSE[@]}" exec -T backup-service python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" >/dev/null 2>&1 && break
    sleep 1
done
"${COMPOSE[@]}" exec -T backup-service python -m app.engine backup --keep "${KEEP:-14}"
