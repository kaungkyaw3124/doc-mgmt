#!/usr/bin/env bash
# loop-test-down.sh — tear down ONLY the throwaway loop-test stack and delete
# ONLY its data folder infra/data-loop-test/ (bind mounts survive `down -v`).
# Never touches the real stack or infra/data/. Safe to run twice.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="${COMPOSE_PROJECT_NAME:-loop-test}"
if [ "$PROJECT" != "loop-test" ]; then
    echo "loop-test-down.sh: refusing — compose project is '$PROJECT', not 'loop-test'" >&2
    exit 1
fi
docker compose -p loop-test -f "$REPO_ROOT/infra/docker-compose.yml" \
    -f "$REPO_ROOT/infra/docker-compose.loop-test.yml" down -v --remove-orphans
DATA="$REPO_ROOT/infra/data-loop-test"
if [ -d "$DATA" ]; then
    # Files are owned by the containers' users; delete them from a container.
    # Mount the folder itself (never its parent) so nothing else is reachable.
    docker run --rm -v "$DATA:/wipe" alpine sh -c 'rm -rf /wipe/* /wipe/.[!.]* /wipe/..?*'
    rmdir "$DATA"
fi
echo "loop-test-down.sh: loop-test stack and infra/data-loop-test/ removed"
