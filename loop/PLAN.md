# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **0** |
| Goal | Build the test harness so the loop can verify anything at all |
| Attempt limit | 5 per failing check |
| Branch | `loop/iter-0-test-harness` |
| Status | READY FOR CLAUDE CODE |

## Why this iteration first
The repo has **no tests and no test framework** (`docs/overview.md`, Tech
stack table). A test→fix loop is impossible without a reliable "did it
work?" signal, so iteration 0 builds that signal. All later iterations
(the real product goals) depend on it.

## Context to read first
- `docs/overview.md`, `docs/architecture.md`, `docs/deployment.md`
- `docs/api.md` (endpoints the tests will call)
- `docs/coding-standards.md`
- `infra/docker-compose.yml`, `infra/nginx/nginx.conf`
- `seed-data-setup/scripts/seed_data.py` (shows how to log in and call the API)

## Tasks
1. **Test env file**: create `infra/.env.test` (or reuse the existing
   `.env.example` files) so the full stack boots locally with test-only
   credentials. Do not commit real secrets.
2. **`scripts/verify.sh`** — the one command the loop trusts. It must:
   1. `docker compose -f infra/docker-compose.yml up -d --build`
   2. wait until all four services' `/health` endpoints respond through
      Nginx on `http://localhost:8080` (timeout ~180s, fail clearly if not)
   3. run `pytest tests/ -v`
   4. exit non-zero if any step fails; print which step failed.
   Add a `--keep` flag to leave the stack running, default is to leave it up
   only on failure (for debugging).
3. **`tests/` (pytest + httpx or requests)**, black-box against the gateway
   at `http://localhost:8080/api`:
   - `conftest.py`: base URL from env `BASE_URL`, an `admin_token` fixture
     that logs in with the seeded admin, a helper for auth headers.
   - `test_health.py`: each service healthy via the gateway.
   - `test_auth.py`: admin login OK; wrong password → 401; a request with
     no token to a protected route (e.g. `GET /api/documents`) is rejected.
   - `test_documents_smoke.py`: create customer → create quotation with at
     least one line item → read it back → export quotation PDF (200 and
     body starts with `%PDF`) → export XLSX (200) → soft-delete → appears
     in `/api/documents/trash`.
   - `test_catalogue_smoke.py`: create category → create product → fetch it.
   - `test_search_smoke.py`: the product/document created above becomes
     searchable (poll up to ~10s — Meilisearch indexing is async).
   - Tests must clean up or use unique names (uuid suffix) so they can be
     re-run without resetting the DB.
4. `tests/requirements.txt` with pinned versions.
5. Add a short "Running tests" section to `docs/operations.md`.

## Out of scope
- Fixing any bugs the new tests uncover. If a smoke test fails because of a
  real app bug, **do not fix the app** — mark the test with
  `@pytest.mark.xfail(reason="BUG: ...", strict=True)`, list the bug in the
  report under "Found but not fixed", and keep going. (This is the one
  exception to the "never skip tests" rule, and only for pre-existing bugs.)
- CI pipelines, unit tests per service, frontend tests.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Stack boots from clean | `docker compose -f infra/docker-compose.yml down -v && ./scripts/verify.sh` exits 0 |
| A2 | Re-runnable | Running `./scripts/verify.sh` a second time without `down -v` also exits 0 |
| A3 | Tests actually detect failure | Temporarily stop `catalogue-service`; `verify.sh` (or `pytest`) must fail with a clear message. Restore after. Paste the failure output in the report. |
| A4 | Coverage of smoke flows | All test files listed in Task 3 exist and run; any xfails each have a `BUG:` reason recorded in the report |
| A5 | No secrets committed | `git diff main --stat` shows no real credentials; only test/example values |

## What comes next (preview for the planner)
Iteration 1 will be the user's first real feature/bug goal, built on top of
this harness. Each later iteration adds its own tests to `tests/`.
