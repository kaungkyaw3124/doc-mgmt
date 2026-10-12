#!/usr/bin/env bash
# Backs up everything that makes this deployment unique into ONE tarball:
#   - Postgres data directory (infra/data/postgres) — all 3 databases
#     (docmgmt, catalogue, auth), i.e. every document/product/customer/
#     project/company/user/group/role/permission grant.
#   - MinIO data directory (infra/data/minio) — every uploaded file
#     (catalog images/specs, company logos/seals, generated exports kept
#     in storage).
#   - Every service's .env file (services/*/.env) — secrets and config
#     (JWT secret, DB/MinIO/Meilisearch credentials, etc.). These are
#     NOT in git (gitignored, deployment-specific) and nothing else
#     captures them.
#   - infra/data/meili (Meilisearch's search index) is INTENTIONALLY
#     EXCLUDED — it's a derived/rebuildable index over catalogue-service's
#     own data (re-synced automatically), not a source of truth. Skipping
#     it keeps the backup smaller; nothing is lost.
#
# Stops the stack first for a consistent on-disk snapshot (a raw copy of
# a live Postgres/MinIO data directory while they're still writing can be
# corrupt) and restarts it afterward either way, even on failure.
#
# Run from the infra/ directory:
#   ./backup.sh [output-directory]
# Produces: <output-directory>/docmgmt-backup-<timestamp>.tar.gz
# Default output directory: ./backups (created if missing)

set -euo pipefail
cd "$(dirname "$0")"

OUT_DIR="${1:-./backups}"
mkdir -p "$OUT_DIR"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="$OUT_DIR/docmgmt-backup-$TIMESTAMP.tar.gz"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

echo "Stopping the stack for a consistent snapshot..."
docker compose stop

echo "Staging files..."
mkdir -p "$STAGING/infra/data"
cp -a data/postgres "$STAGING/infra/data/postgres"
cp -a data/minio "$STAGING/infra/data/minio"

for svc in auth-service catalogue-service document-service search-service; do
    env_file="../services/$svc/.env"
    if [ -f "$env_file" ]; then
        mkdir -p "$STAGING/services/$svc"
        cp "$env_file" "$STAGING/services/$svc/.env"
    else
        echo "  ! warning: $env_file not found — skipping (restore will need it recreated manually)"
    fi
done

echo "Archiving to $ARCHIVE..."
tar -czf "$ARCHIVE" -C "$STAGING" .

echo "Restarting the stack..."
docker compose start

echo ""
echo "Done: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
echo "Copy this ONE file to the new machine and run restore.sh against it."
