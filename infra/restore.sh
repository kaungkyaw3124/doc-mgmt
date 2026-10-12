#!/usr/bin/env bash
# Restores a backup produced by backup.sh onto a (new or existing) machine
# that already has this repository checked out — git gives you the code
# (Dockerfiles, nginx config, app source); this script gives you the DATA
# and SECRETS that make a specific deployment's install unique (see
# backup.sh's header comment for exactly what that covers).
#
# Run from the infra/ directory, with the repo already cloned (git clone,
# or git checkout the right branch) but BEFORE the stack has ever been
# started on this machine (an existing infra/data/ here gets overwritten):
#   ./restore.sh /path/to/docmgmt-backup-<timestamp>.tar.gz
#
# After this finishes: `docker compose up -d --build` to start the stack —
# deliberately not done automatically here, so you can review what was
# restored first.

set -euo pipefail
cd "$(dirname "$0")"

ARCHIVE="${1:?Usage: ./restore.sh /path/to/docmgmt-backup-<timestamp>.tar.gz}"
if [ ! -f "$ARCHIVE" ]; then
    echo "File not found: $ARCHIVE" >&2
    exit 1
fi

if [ -d data/postgres ] || [ -d data/minio ]; then
    echo "infra/data/ already has content on this machine."
    read -p "Overwrite it with the backup's data? [y/N] " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "Aborted — nothing was changed."
        exit 1
    fi
    echo "Stopping the stack (if running) before overwriting data..."
    docker compose down || true
fi

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

echo "Extracting $ARCHIVE..."
tar -xzf "$ARCHIVE" -C "$STAGING"

echo "Restoring Postgres + MinIO data into infra/data/..."
mkdir -p data
rm -rf data/postgres data/minio
cp -a "$STAGING/infra/data/postgres" data/postgres
cp -a "$STAGING/infra/data/minio" data/minio

echo "Restoring service .env files..."
for svc in auth-service catalogue-service document-service search-service; do
    src="$STAGING/services/$svc/.env"
    dest="../services/$svc/.env"
    if [ -f "$src" ]; then
        cp "$src" "$dest"
        echo "  restored $dest"
    else
        echo "  ! $svc/.env was not in this backup — copy services/$svc/.env.example to services/$svc/.env and fill it in by hand before starting the stack."
    fi
done

echo ""
echo "Restore complete. Review the restored .env files, then run:"
echo "  docker compose up -d --build"
