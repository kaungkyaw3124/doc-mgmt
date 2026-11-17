#!/usr/bin/env bash
# restore.sh — restore a backup from the command line (the web UI's Admin ->
# Backup & Restore does the same). REPLACES ALL DATA. A pre-restore backup is
# taken first (backups/pre-restore-*.tar.gz) — restore that to roll back.
#
# Usage: ./scripts/restore.sh <file.tar.gz | file.tar.gz.enc>
#   An encrypted file asks for its password (or set BACKUP_PASSWORD).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$REPO_ROOT/infra/docker-compose.yml" ${COMPOSE_OVERRIDE:+-f "$COMPOSE_OVERRIDE"})
src="${1:?usage: $0 <backup file>}"
[ -f "$src" ] || { echo "no such file: $src" >&2; exit 1; }

# The engine reads from its /backups volume: copy the file in when it lives elsewhere.
name="$(basename "$src")"
dest="$REPO_ROOT/backups/$name"
if [ "$(realpath "$src")" != "$(realpath -m "$dest")" ]; then
    "${COMPOSE[@]}" cp "$src" "backup-service:/backups/$name" </dev/null
fi
if [ "$(head -c 8 "$src")" = "Salted__" ] && [ -z "${BACKUP_PASSWORD:-}" ]; then
    read -rsp "Password for $name: " BACKUP_PASSWORD; echo
fi
export BACKUP_PASSWORD="${BACKUP_PASSWORD:-}"

# </dev/null: exec -T would otherwise eat the answer to the prompt below.
"${COMPOSE[@]}" exec -T -e BACKUP_PASSWORD backup-service python -m app.engine verify "$name" </dev/null
read -rp "This REPLACES ALL DATA with $name. Type RESTORE to continue: " answer
[ "$answer" = "RESTORE" ] || { echo "cancelled"; exit 1; }
"${COMPOSE[@]}" exec -T -e BACKUP_PASSWORD backup-service python -m app.engine restore "$name"
