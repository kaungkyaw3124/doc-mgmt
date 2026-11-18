#!/usr/bin/env bash
# reindex.sh — rebuild the search index for documents and products from the
# database. Saves never fail because of search: a failed index update is only
# logged ("Failed to index …" in the service log), and this puts the missing
# items back. Safe to run any time (the database is only read).
#
# Usage: ./scripts/reindex.sh
# Env:   COMPOSE_PROJECT_NAME / COMPOSE_OVERRIDE (other stacks).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$REPO_ROOT/infra/docker-compose.yml" ${COMPOSE_OVERRIDE:+-f "$COMPOSE_OVERRIDE"})
for pair in document-service:Document catalogue-service:Product; do
    svc="${pair%%:*}" model="${pair##*:}"
    "${COMPOSE[@]}" exec -T "$svc" python -c "
from app.core.db import SessionLocal
from app import models
from app.core.search_client import rebuild_index
with SessionLocal() as db:
    print('$svc: re-indexed', rebuild_index(db.query(models.$model).all()))
"
done
