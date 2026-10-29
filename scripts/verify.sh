#!/usr/bin/env bash
# verify.sh — the one command the loop trusts (see loop/LOOP.md).
#
# Boots the full stack, waits for every backend's /health, then runs the
# black-box pytest suite in tests/ against the Nginx gateway. Exits non-zero
# and names the failed step if anything goes wrong.
#
# Usage: ./scripts/verify.sh [--keep]
#   --keep   leave the stack running afterwards, even on success.
#            Without it the stack is torn down on success and left UP on
#            failure, so the broken state can be inspected with
#            `docker compose -f infra/docker-compose.yml logs <service>`.
#
# Env overrides: BASE_URL (default http://localhost:8080/api),
# HEALTH_TIMEOUT (seconds, default 180), PYTHON (default python3).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$REPO_ROOT/infra/docker-compose.yml")
GATEWAY="${GATEWAY:-http://localhost:8080}"
export BASE_URL="${BASE_URL:-$GATEWAY/api}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"
PYTHON="${PYTHON:-python3}"
VENV="$REPO_ROOT/.venv-tests"

KEEP=0
for arg in "$@"; do
    case "$arg" in
        --keep) KEEP=1 ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

fail() {
    # $1 = step name, $2 = exit code. The stack is deliberately left up
    # here (whatever --keep says) — a failed run is exactly when you want
    # to read the containers' logs.
    echo
    echo "verify.sh: FAILED at step: $1 (exit $2)"
    echo "verify.sh: stack left running for debugging —"
    echo "  docker compose -f infra/docker-compose.yml ps"
    echo "  docker compose -f infra/docker-compose.yml logs <service> --tail 100"
    exit "$2"
}

step() { echo; echo "==> [verify] $*"; }

# --- step 1: env files --------------------------------------------------
# Each service reads its own services/<svc>/.env (gitignored). Seed any
# missing one from the committed .env.example — those hold only local
# dev/test placeholder values (and secrets_check.py refuses them outright
# when ENVIRONMENT=production), so this can never put a real secret in
# play. An existing .env is never overwritten.
step "env files"
for svc in auth-service catalogue-service document-service search-service; do
    env_file="$REPO_ROOT/services/$svc/.env"
    if [ ! -f "$env_file" ]; then
        cp "$REPO_ROOT/services/$svc/.env.example" "$env_file" || fail "env files" 1
        echo "created services/$svc/.env from .env.example"
    fi
done

# --- step 2: build + start ----------------------------------------------
step "docker compose up -d --build"
"${COMPOSE[@]}" up -d --build || fail "docker compose up" $?

# --- step 3: wait for health --------------------------------------------
# The backends' /health routes are not proxied by any /api/* location (see
# docs/api.md "Health checks"), so they're probed from inside the nginx
# container — the same network path the gateway itself uses to reach
# them — and the published gateway port is probed from the host. Both
# have to answer before tests start.
step "wait for health (timeout ${HEALTH_TIMEOUT}s)"
deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
pending="auth-service document-service catalogue-service search-service gateway"
while :; do
    still=""
    for target in $pending; do
        if [ "$target" = gateway ]; then
            code=$(curl -s -o /dev/null -w '%{http_code}' "$GATEWAY/api/auth/groups-public" || true)
            [ "$code" = "200" ] || still="$still $target"
        else
            "${COMPOSE[@]}" exec -T nginx wget -q -T 3 -O /dev/null "http://$target:8000/health" >/dev/null 2>&1 \
                || still="$still $target"
        fi
    done
    pending="${still# }"
    [ -z "$pending" ] && { echo "all services healthy"; break; }
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "not healthy after ${HEALTH_TIMEOUT}s: $pending"
        "${COMPOSE[@]}" ps
        fail "wait for health" 1
    fi
    sleep 3
done

# --- step 4: pytest -----------------------------------------------------
step "install test dependencies"
if [ ! -x "$VENV/bin/python" ]; then
    "$PYTHON" -m venv "$VENV" || fail "create test venv" $?
fi
"$VENV/bin/pip" install -q -r "$REPO_ROOT/tests/requirements.txt" || fail "install test dependencies" $?

step "pytest tests/ -v (BASE_URL=$BASE_URL)"
(cd "$REPO_ROOT" && "$VENV/bin/python" -m pytest tests/ -v)
rc=$?
[ "$rc" -eq 0 ] || fail "pytest" "$rc"

# --- done ---------------------------------------------------------------
echo
if [ "$KEEP" -eq 1 ]; then
    echo "verify.sh: PASSED (stack left running: --keep)"
else
    step "docker compose down"
    "${COMPOSE[@]}" down || fail "docker compose down" $?
    echo "verify.sh: PASSED"
fi
exit 0
