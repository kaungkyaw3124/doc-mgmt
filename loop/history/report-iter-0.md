# report.md — Iteration 0

## 1. Summary
- **Status**: BLOCKED
- **Goal**: Build the test harness so the loop can verify anything at all
- **Branch / commit**: `security/auth-hardening` (not `loop/iter-0-test-harness`; see §8) @ the commit that adds this report (`loop iter 0: test harness ... [BLOCKED]`)
- **One-paragraph summary**: Every Task 1–5 deliverable is written:
  `scripts/verify.sh`, the pytest suite in `tests/`, pinned
  `tests/requirements.txt`, env files seeded from the existing `.env.example`
  files, and a "Running tests" section in `docs/operations.md`. **None of
  the acceptance checks could be run in this execution environment.** Its
  network policy refuses Docker Hub (`registry-1.docker.io`, so
  `postgres:16`, `nginx:alpine`, `minio/minio` and `getmeili/meilisearch`
  can't be pulled) and PyPI (`pypi.org`, so pytest/httpx and the services'
  own Python deps can't be installed). The proxy answered 403 to both.
  The suite has only been checked statically (`bash -n`, `py_compile`). The
  part of `verify.sh` that could run (step-failure reporting and exit
  codes) behaved correctly. A1–A4 still need one run on a machine that can
  reach those two registries.

## 2. Baseline (before any change)
No `scripts/verify.sh` and no `tests/` existed, so there was no harness to
run. The equivalent baseline, booting the stack, failed for environmental
reasons before any app code ran:
```
$ docker compose -f infra/docker-compose.yml up -d --build
 Image nginx:alpine Error failed to resolve reference "docker.io/library/nginx:alpine": failed to do request: Head "https://registry-1.docker.io/v2/library/nginx/manifests/alpine": Forbidden
 Image postgres:16 Interrupted
 Image minio/minio Interrupted
 Image getmeili/meilisearch:v1.10 Interrupted
EXIT 1

$ curl -sS "$HTTPS_PROXY/__agentproxy/status"   (recentRelayFailures)
  "kind": "connect_rejected", "detail": "gateway answered 403 to CONNECT (policy denial or upstream failure)", "host": "registry-1.docker.io:443"
```
(The Docker daemon wasn't running in the container at first; it was started with `dockerd`, and that worked.)

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | Stack boots from clean | NOT RUN (blocked) | `./scripts/verify.sh` → `FAILED at step: docker compose up (exit 1)`, image pull `Forbidden` from registry-1.docker.io |
| A2 | Re-runnable | NOT RUN (blocked) | depends on A1. The suite is designed for re-runs: uuid-suffixed names, per-test cleanup, and no failed logins against `admin` (see §7, rate limiter) |
| A3 | Tests detect failure | NOT RUN (blocked) | needs a running stack. Expected signal: `test_service_reachable_via_gateway[catalogue-service]` fails with `catalogue-service not healthy via gateway: GET .../api/categories -> 502`. Before that, `verify.sh` fails at `wait for health` listing `catalogue-service` |
| A4 | Coverage of smoke flows | PARTIAL | All files from Task 3 exist and compile (`py_compile OK`). They couldn't be *run* (pytest not installable, no stack). There are no xfails because no app bugs could be observed |
| A5 | No secrets committed | PASS | The new files hold only the `.env.example` placeholder login (`admin` / `CHANGE_ME`). `services/*/.env` are gitignored (`git check-ignore` → `.gitignore:2:.env`) and not committed. `git diff main --stat` is large only because this branch had already diverged from `main` before this iteration; none of that diff is from this iteration |

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `scripts/verify.sh` | added | Task 2. Steps: seed missing `services/*/.env` from `.env.example` → `compose up -d --build` → wait ≤180s for each backend's `/health` (probed from inside the nginx container) plus the gateway on :8080 → pytest in a gitignored `.venv-tests` → `down` on success. Prints `FAILED at step: <name>` and leaves the stack up on failure. `--keep` leaves it up always |
| `tests/conftest.py` | added | `BASE_URL` from env, session `admin_token`, `auth_headers()`, `unique()`, `poll()`, and fixtures for customer / quotation / category / product that clean up after themselves |
| `tests/test_health.py` | added | One parametrized case per service through the gateway, plus the web UI at `/` |
| `tests/test_auth.py` | added | Admin login 200. Wrong password → 401 (uses a throwaway registered user, removed afterwards via `/deny`). No token / garbage token on `GET /api/documents` → 401 |
| `tests/test_documents_smoke.py` | added | customer → quotation with 1 line → read back (total 20.0) → PDF starts with `%PDF` → XLSX starts with `PK` → trash → appears in `/documents/trash` and is gone from `/documents` |
| `tests/test_catalogue_smoke.py` | added | category → product (SKU prefixed by short_term) → fetch |
| `tests/test_search_smoke.py` | added | Product name and a unique token in a quotation's line item each become searchable (polls ≤10s) |
| `tests/requirements.txt` | added | `pytest==8.3.3`, `httpx==0.27.2` |
| `docs/operations.md` | modified | Task 5 "Running tests" section |
| `.gitignore` | modified | ignore `.venv-tests/` |
| `loop/LOOP.md`, `loop/PLAN.md`, `loop/REPORT_TEMPLATE.md` | added (copied from `origin/main`) | They exist only on `main`; this branch didn't have them |

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | A1 (`verify.sh` step `docker compose up`) | `Head "https://registry-1.docker.io/v2/library/nginx/manifests/alpine": Forbidden` | Environment's network policy denies Docker Hub | None possible from inside the session: the proxy says not to route around policy denials. Stopped here |
| 2 | Test dependency install | `Tunnel connection failed: 403 Forbidden` for `pypi.org/simple/pytest/` | Environment's network policy denies PyPI | Same. A native (non-Docker) stack wasn't possible either: it would need PyPI for service deps, and MinIO's download host is also denied |

No fail→fix cycles on app or test code were possible, so the 5-attempt limit was never reached. The block is environmental.

## 6. Final verification output
```
$ ./scripts/verify.sh
==> [verify] env files
==> [verify] docker compose up -d --build
Error response from daemon: failed to resolve reference "docker.io/getmeili/meilisearch:v1.10": ... Forbidden

verify.sh: FAILED at step: docker compose up (exit 1)
verify.sh: stack left running for debugging —
  docker compose -f infra/docker-compose.yml ps
  docker compose -f infra/docker-compose.yml logs <service> --tail 100
exit=1

$ ./scripts/verify.sh --bogus
unknown argument: --bogus
exit=2

$ bash -n scripts/verify.sh            -> bash -n OK
$ python3 -m py_compile tests/*.py     -> py_compile OK
```
No pytest summary line exists: pytest couldn't be installed or run here.

## 7. Found but not fixed (out of scope)
- **Services' `/health` routes are not reachable through Nginx.** See
  `docs/api.md` "Health checks" and `infra/nginx/nginx.conf.template`: there
  is no `/api/*/health` location. PLAN Task 2 asks for health "through Nginx
  on :8080". I didn't add public gateway routes, since that would change the
  gateway's attack surface. `verify.sh` instead probes
  `http://<svc>:8000/health` from inside the nginx container (the same network
  path the gateway uses) and checks the gateway on :8080 separately.
  `test_health.py` uses one cheap real route per service through the gateway.
  See §8 for the decision this needs.
- **The login rate limiter can lock the seeded admin out of test runs.**
  `services/auth-service/app/core/config.py` sets 5 failed attempts per
  (ip, username) per 15 min, and `check_login_rate_limit` runs before
  password verification. A wrong-password test against `admin` would turn
  every later login into a 429 after ~5 runs. The tests avoid this. The
  per-IP cap (20 failures / 15 min, any username) still applies, and each
  suite run adds one failure, so ~20 runs inside 15 minutes would trip it.
  Raising the limits in a test-only `.env` would fix this if it ever bites.
- **Categories have no DELETE endpoint**
  (`services/catalogue-service/app/routers/categories.py`), so every run
  leaves one `test-category-*` row behind. Harmless, but it grows.
- **Branch divergence.** `main` (where `loop/` lives) and
  `security/auth-hardening` have diverged heavily (102 files differ;
  `main` has `web/index.html` inline JS and lacks this branch's
  `gateway_auth.py`, `secrets_check.py` and per-service unit tests). The
  planner should decide which line later iterations target.

## 8. Blockers / decisions needed
- **Blocker: the execution environment can't reach Docker Hub or PyPI.**
  - **What I tried**: started `dockerd` (worked); `docker compose up -d --build`
    (image pulls refused with 403 by the egress proxy for
    `registry-1.docker.io`); `pip install` directly and through
    `$HTTPS_PROXY` (403 for `pypi.org`); checked for a native fallback
    (Postgres 16 binaries exist and Meilisearch's GitHub release is
    reachable, but `dl.min.io` is 403, nginx isn't installed, and the services'
    Python deps need PyPI).
  - **Options**: A) allow `registry-1.docker.io` (plus `auth.docker.io` /
    `production.cloudflare.docker.com`) and `pypi.org` /
    `files.pythonhosted.org` in the cloud environment's network settings
    (Network access → Allowed domains), then re-run this iteration;
    B) run `./scripts/verify.sh` on a dev machine or in CI (the
    `infra-integration` job in `.github/workflows/tests.yml` already boots
    the stack on `ubuntu-latest`) and paste the output into this report.
  - **My recommendation**: A, then re-run iteration 0 unchanged. If any smoke
    test then fails because of a real app bug, mark it `xfail(strict=True,
    reason="BUG: ...")` per the plan.
- **Decision: health checks through the gateway.** A) keep the current
  approach (probe `/health` from inside the nginx container; no gateway
  change). B) add unauthenticated `location = /api/<svc>/health` routes to
  `nginx.conf.template` so the health endpoints are literally reachable on
  :8080. **Recommendation: A.** It meets the intent without widening the
  public surface.
- **Branch deviation**: LOOP.md says to work on `loop/iter-0-test-harness`
  and not push. This session was required to develop on and push
  `security/auth-hardening`, and pushing keeps the work alive past the
  ephemeral container. The planner may want to rename or cherry-pick.

## 9. Suggestions for the next iteration
- Re-run iteration 0's acceptance checks (A1–A4) once the environment can
  pull images and packages. Nothing in the harness should need to change.
- Add a CI job that runs `./scripts/verify.sh` so the loop's source of truth
  also gates pushes.
- Consider a test-only env overlay (`infra/.env.test`) that raises the login
  rate limits for the test stack only.
