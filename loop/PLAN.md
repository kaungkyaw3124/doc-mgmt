# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **0b** (re-run of iteration 0) |
| Goal | Prove the test harness actually runs and passes |
| Attempt limit | 5 per failing check |
| Base branch | `dev` (= `quotation-layout-fixes` + the iteration 0 harness) |
| Branch | `loop/iter-0b-run-harness` |
| Status | READY FOR CLAUDE CODE |

## Why
Iteration 0 wrote `scripts/verify.sh` and `tests/` but could not run them:
the environment blocked Docker Hub and PyPI
(see `loop/history/report-iter-0.md`). The harness code is already on
`dev`. This iteration only runs it and fixes whatever breaks.

## Pre-check (do first)
Confirm the network works before anything else:
```
docker pull nginx:alpine
pip download pytest==8.3.3 -d /tmp/pipcheck
```
If either still returns 403/Forbidden, stop immediately with status
`BLOCKED` and list the exact domains refused. Do not try workarounds.

## Tasks
1. Start the Docker daemon if needed, then run `./scripts/verify.sh`.
2. Fix failures in the **harness** (verify.sh, tests, conftest, env files)
   until it runs end to end.
3. If a test fails because of a real **app** bug, do not fix the app:
   mark it `@pytest.mark.xfail(reason="BUG: ...", strict=True)` and list it
   in the report.
4. The existing per-service unit tests (`services/*/tests/`) and the CI
   workflow (`.github/workflows/tests.yml`) came from earlier work. Run
   the unit tests once and report the result; do not change them.

## Decisions already made
- Health checks: keep the current approach (probe `/health` from inside the
  nginx container). Do not add public `/health` routes to nginx.
- Login rate limits: if they break repeated runs, raise them only in a
  test-only env file, never in the default config.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Stack boots from clean | `docker compose -f infra/docker-compose.yml down -v && ./scripts/verify.sh` exits 0 |
| A2 | Re-runnable | Run `./scripts/verify.sh` again without `down -v`; exits 0 |
| A3 | Tests detect failure | Stop `catalogue-service`; verify.sh or pytest fails with a clear message. Restore it. Paste the output |
| A4 | Every smoke test runs | pytest summary shows all tests passed or xfail; each xfail has a `BUG:` reason in the report |
| A5 | Unit tests reported | Pass/fail counts per service pasted in the report |

## Next
After this passes, iteration 1 will be the user's first real feature or fix.
