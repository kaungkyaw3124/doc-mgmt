#!/usr/bin/env bash
# assign-unassigned.sh — give every document, customer, project and product
# that has no group yet (data from before department separation, iteration 19)
# to ONE group, by name; then the search indexes are rebuilt so the data is
# searchable by that group. Nothing is assigned unless you run this.
# Running it again is harmless (nothing left to assign). See docs/GROUPS.md.
#
# Usage: ./scripts/assign-unassigned.sh "Operation"
# Env:   COMPOSE_PROJECT_NAME / COMPOSE_OVERRIDE (other stacks, like backup.sh).
set -euo pipefail
GROUP_NAME="${1:?usage: $0 <group-name>}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$REPO_ROOT/infra/docker-compose.yml" ${COMPOSE_OVERRIDE:+-f "$COMPOSE_OVERRIDE"})

# Runs inside document-service: it holds the internal secret and reaches the
# other services on the compose network. Calls the superuser-only
# /documents/assign-unassigned and /products/assign-unassigned endpoints.
"${COMPOSE[@]}" exec -T -e GROUP_NAME="$GROUP_NAME" document-service python - <<'PY'
import json, os, sys

import httpx

from app.core.config import settings

name = os.environ["GROUP_NAME"]
groups = httpx.get("http://auth-service:8000/groups-public", timeout=10).json()
match = [g for g in groups if g["name"] == name]
if not match:
    sys.exit(f"No group named {name!r}. Groups: {', '.join(g['name'] for g in groups) or '(none)'}")
group_id = match[0]["id"]
headers = {"X-Internal-Secret": settings.internal_shared_secret, "X-Is-Superuser": "1", "X-Group-Ids": group_id}
result = {"group": name, "group_id": group_id}
for label, url in (("document-service", "http://localhost:8000/documents/assign-unassigned"),
                   ("catalogue-service", f"{settings.catalogue_service_url}/products/assign-unassigned")):
    r = httpx.post(url, json={"group_id": group_id}, headers=headers, timeout=120)
    if r.status_code != 200:
        sys.exit(f"{label}: HTTP {r.status_code} {r.text}")
    result[label] = r.json()
print(json.dumps(result, indent=2))
PY
