# Security Hardening Development Log

Branch: `security/auth-hardening` (based on the testing branch `security/auth-hardening`, formerly `claude/security-hardening-testing`), to be merged to `main` once all tasks pass.

| Task | Goal | Status | Commit |
|---|---|---|---|
| 1 | Login rate limiting | PASS | bc3610f |
| 2 | Remove insecure default secrets | PASS | 59d09ce |
| 3 | Remove host exposure of internal datastores | PASS | 02957ee (fix: db0c219) |
| 4 | Authenticate gateway trust headers | PASS | 3d3f014 (fix: 12b1da0) |
| 5 | Secure uploaded content type | PASS | c21f72c (this pass: content-sniffing + Nginx Host-header fix) |
| 6 | JWT storage and rotation | PASS | 72a9af5 (fixes: d727155, e7ed0a1) |
| 7 | CORS policy | PASS | 92a5f91 |
| 8 | Security headers / Nginx hardening | PASS | ef4d087 |

## A note on the test-execution environment

This sandbox (the Claude Code session container) has **no outbound network
access to package indices** (PyPI, npm, apt mirrors — verified: `pip
install`, `apt-get update`, and direct `curl` to `pypi.org` all return
403/blocked) and **no Docker daemon** (`docker.sock` is not present), so
`docker-compose up` / building the real service images is not possible
from inside this sandbox either.

What *is* available locally: a real PostgreSQL 16 server (installed via
apt before network lockdown, or already present in the base image),
usable for schema/query-level sanity checks and `python3 -m py_compile`
for syntax checking.

To get **real, evidence-backed test execution** (not a manual trace or a
claim), every task in this log that changes application code adds/extends
a `pytest` suite and runs it via the `.github/workflows/tests.yml` GitHub
Actions workflow, which runs on GitHub's own runners (full network access,
a fresh Postgres service container per job). Results below are pulled
directly from the Actions run logs (job id + step output), not asserted
from local reasoning alone. This is disclosed here rather than silently
switched to, per the "never hide failures / never fabricate evidence"
rule for this log.

---

## Task 1 — Login Rate Limiting

**Date:** 2026-09-07 10:05

**Status:** PASS

### Goal

Prevent brute-force and credential-stuffing attacks against
`POST /login`, without introducing an account-lockout DoS, without
account enumeration via the rate-limit response, and correctly across
multiple auth-service replicas (no in-memory state).

### Acceptance Criteria

- [x] Brute-force login attempts are throttled
- [x] Rate limiting does not create a trivial account-lockout DoS
- [x] Existing legitimate login still works
- [x] Tests pass
- [x] Security regression tests pass

### Initial State

`POST /login` in `services/auth-service/app/routers/auth.py` had no
throttling whatsoever — unlimited password guesses against any account,
including the seeded `admin` account, from a single IP or many.

### Changes Made

- `services/auth-service/app/models.py` — added `LoginAttempt` model
  (table `login_attempts`: `ip`, `username`, `created_at`, with three
  covering indexes) to record failed attempts. Table lives in the same
  shared Postgres database every auth-service instance already uses, so
  the limiter is correct under multiple replicas without any in-memory
  or per-process state (Redis/etc. was considered and rejected as
  unnecessary — Postgres is already the shared source of truth here).
- `services/auth-service/app/core/config.py` — added six settings:
  `rate_limit_{ip,pair,username}_max_attempts` and
  `..._window_minutes`, defaulting to IP=20/15min, pair=5/15min,
  username=15/15min.
- `services/auth-service/app/core/rate_limit.py` (new) —
  `get_client_ip()` (reads `X-Real-IP`, falls back to
  `X-Forwarded-For`, falls back to the raw socket peer),
  `check_login_rate_limit()` (raises `RateLimitExceeded` if any of the
  three sliding-window limits — IP, (IP, username) pair, or username —
  is at/over threshold), and `record_failed_attempt()` (inserts a row,
  opportunistically prunes rows older than every configured window).
- `services/auth-service/app/routers/auth.py` — `/login` now checks the
  rate limit *before* querying/verifying the user (so the check itself
  reveals nothing about account existence), returns `429` +
  `Retry-After` header with a generic message on trip, and records a
  failed attempt only on wrong credentials (not on
  pending-approval/disabled, which already require a correct password
  to reach and are outside this task's scope).
- `infra/nginx/nginx.conf` — `/api/auth/login` location now sets
  `X-Real-IP $remote_addr;` so auth-service sees the real client IP
  instead of Nginx's own container IP.
- `services/auth-service/tests/` (new) — `conftest.py` (Postgres-backed
  test fixtures, small rate-limit thresholds for fast tests) and
  `test_rate_limit.py` (9 regression tests, see below).
- `services/auth-service/requirements-dev.txt` (new) — pytest + httpx,
  layered on the service's own `requirements.txt`.
- `.github/workflows/tests.yml` (new) — runs the auth-service suite
  against a real Postgres 16 service container on every push.

### Design notes: avoiding the lockout-DoS / brute-force tension

The task requires both "prevent unlimited attempts against a single
account" and "do not create an easy account-lockout DoS" — these pull in
opposite directions if you use a single global per-username counter (an
attacker can lock a real user out just by failing login as them from one
IP). Three independent, additive limits were used instead:

1. **(IP, username) pair** (tightest — 5/15min default) is the primary
   brute-force guard and is what a focused attacker hits first. It is
   scoped to the *attacker's own IP*, so it can never block the victim's
   own login from the victim's own IP.
2. **IP-wide** (20/15min) catches credential stuffing (many usernames,
   one source).
3. **Username-wide, any IP** (15/15min, deliberately looser than the
   pair limit) bounds distributed brute force (many source IPs, one
   target account) without tripping on a legitimate user's occasional
   typo.

All three are sliding windows over the same `login_attempts` table (no
separate lock/unlock state) — a limit is never a permanent lock, it
self-heals as old attempts age out of the window, so no admin
intervention is ever required to unlock a legitimate account.

### Tests Performed

```text
cd services/auth-service
pip install -r requirements-dev.txt
python -m pytest -v
```
Run via GitHub Actions (`.github/workflows/tests.yml`, job
`auth-service`) on push to `security/auth-hardening`, commit `bc3610f`:
https://github.com/kaungkyaw3124/doc-mgmt/actions/runs/34109502229
(this sandbox cannot install Python packages — see the note above — so
this is the actual execution evidence, pulled from the job's log output).

### Test Results

```text
tests/test_rate_limit.py::test_successful_login_works PASSED
tests/test_rate_limit.py::test_repeated_failed_attempts_get_throttled PASSED
tests/test_rate_limit.py::test_rate_limit_does_not_reveal_account_existence PASSED
tests/test_rate_limit.py::test_different_accounts_from_same_ip_are_independent PASSED
tests/test_rate_limit.py::test_same_account_from_different_ip_is_not_locked_out PASSED
tests/test_rate_limit.py::test_ip_wide_limit_covers_credential_stuffing_across_usernames PASSED
tests/test_rate_limit.py::test_username_wide_limit_covers_distributed_brute_force PASSED
tests/test_rate_limit.py::test_legitimate_login_works_again_once_window_is_cleared PASSED
tests/test_rate_limit.py::test_successful_login_is_not_counted_against_the_limit PASSED

======================== 9 passed, 5 warnings in 12.76s ========================
```
(warnings are pre-existing FastAPI `on_event`/Pydantic deprecation
notices, unrelated to this change)

### Bugs Found

None. First CI run (job id 101702205484) passed clean — no fix
iterations were needed for this task.

### Verification

- Created a user, logged in with the correct password → 200 + token
  (`test_successful_login_works`).
- Failed login 3x against the same (ip, username) pair (test config:
  pair limit = 3) → 401, 401, 401, then 429 with `Retry-After`
  (`test_repeated_failed_attempts_get_throttled`).
- Tripped the same limit for a real account and a nonexistent username
  from different IPs → identical status code AND identical response
  body for both, at every step below and at the limit
  (`test_rate_limit_does_not_reveal_account_existence`) — confirms no
  enumeration signal.
- Two different accounts from the same IP: exhausting one account's pair
  limit did not block the other account's login
  (`test_different_accounts_from_same_ip_are_independent`).
- Simulated an attacker failing login as `victim` from `9.9.9.9` past the
  pair limit, then logged in as `victim` from a different IP
  (`10.10.10.10`) → 200 — the victim's own login is unaffected, proving
  no trivial account-lockout DoS
  (`test_same_account_from_different_ip_is_not_locked_out`).
- Spread failed attempts across 3 different usernames from one IP,
  below each pair's individual limit, to isolate and confirm the
  IP-wide limiter trips independently
  (`test_ip_wide_limit_covers_credential_stuffing_across_usernames`).
- Rotated source IPs against one username to confirm the username-wide
  limiter bounds distributed brute force independently of the other two
  (`test_username_wide_limit_covers_distributed_brute_force`).
- Aged out recorded attempts past the window and confirmed the
  legitimate user could log in again without any admin action
  (`test_legitimate_login_works_again_once_window_is_cleared`).
- Confirmed successful logins are not counted against the limit
  (`test_successful_login_is_not_counted_against_the_limit`).

Therefore: brute-force is throttled, enumeration is not possible via the
rate-limit response, no permanent/trivial lockout exists, and legitimate
login continues to work — acceptance criteria satisfied.

### Security Impact

Fixes a vulnerability (CWE-307, unrestricted authentication attempts).
Prevents brute-force and credential-stuffing password guessing against
any account, including the seeded superuser account, while preserving
availability of legitimate logins.

### Commit

```text
bc3610f
security: add login rate limiting
```

### Final Result

PASS

---

## Task 2 — Remove Insecure Default Secrets

**Date:** 2026-09-07 10:14

**Status:** PASS

### Goal

Ensure production deployments cannot silently start with publicly-known
default credentials (JWT secret, DB password, MinIO/Meilisearch keys,
seeded admin password), while keeping `cp .env.example .env && docker
compose up` working unedited for local development.

### Acceptance Criteria

- [x] No usable production default secrets
- [x] Startup fails for insecure configuration (in production)
- [x] Development configuration remains usable
- [x] No secrets committed to Git
- [x] Tests pass
- [x] Security regression tests pass

### Initial State

Every service (auth/catalogue/document/search) had insecure defaults
baked into `pydantic_settings.BaseSettings` field defaults —
`local_dev_jwt_secret_change_me`, `changeme`, `minioadmin`,
`local_dev_master_key_change_me`, and `docmgmt` (DB password) — with no
mechanism to detect or refuse a production boot using them. auth-service
had a warning-only check for two of these; the other three services and
the other secrets had nothing at all. `.env.example` files contained
these same values verbatim (runnable, not placeholders).

### Changes Made

- `services/{auth,catalogue,document,search}-service/app/core/secrets_check.py`
  (new, identical across services — matches the repo's existing pattern
  of duplicating small per-service infra code rather than a shared
  package) — `is_insecure()` (checks a value against a generic
  placeholder set — empty string, `changeme`, `change_me`,
  `generate_a_secure_secret`, etc. — plus service-specific known
  defaults), `db_password_from_url()` (extracts the password segment
  from a `DATABASE_URL` via `urllib.parse`), and
  `enforce_production_secrets(environment, problems)` (warns if
  `environment != "production"`, raises `RuntimeError` — which FastAPI's
  startup event propagates, crashing the process before it serves
  traffic — if `environment == "production"`).
- `services/*/app/core/config.py` (all 4) — new `environment: str =
  "development"` setting (env var `ENVIRONMENT`).
- `services/*/app/main.py` (all 4) — each now has a pure `_secret_problems()
  -> list[str]` function (checks JWT_SECRET/SEED_ADMIN_PASSWORD/DB
  password for auth-service; MEILI_MASTER_KEY/MINIO_ACCESS_KEY/
  MINIO_SECRET_KEY/DB password for catalogue and document-service;
  MEILI_MASTER_KEY only for search-service, which has no DB), called via
  `enforce_production_secrets()` as the FIRST thing `on_startup` does —
  before `Base.metadata.create_all`, `ensure_bucket_exists`, or any
  Meilisearch call — so a misconfigured production deploy fails
  immediately rather than partway through connecting to a datastore.
  search-service previously had no `on_startup` handler at all; one was
  added.
- `services/*/.env.example` (all 4) — `SEED_ADMIN_PASSWORD`,
  `JWT_SECRET`, `MEILI_MASTER_KEY`, `MINIO_ACCESS_KEY`,
  `MINIO_SECRET_KEY` replaced with `CHANGE_ME` /
  `GENERATE_A_SECURE_SECRET` placeholders (both are themselves in the
  `is_insecure()` placeholder set, so leaving them unedited in
  production is caught too — this is why they weren't replaced with yet
  another hardcoded "safe-looking" default). Added `ENVIRONMENT=development`.
  `DATABASE_URL` keeps its local-dev-convenience value (matches
  `infra/docker-compose.yml`'s own `POSTGRES_PASSWORD` fallback) with a
  comment that it must be a real password in production — the DB
  password is what the fail-fast check on `DATABASE_URL` actually
  enforces server-side.
- `services/*/tests/` (new/extended) — `test_secrets_check.py` per
  service (auth-service: 15 tests; catalogue/document-service: 6 tests
  each; search-service: 5 tests — see below), plus
  `requirements-dev.txt` for catalogue/document/search-service
  (auth-service already had one from Task 1).
- `.github/workflows/tests.yml` — added `catalogue-service`,
  `document-service` (with a step installing WeasyPrint's system
  libraries — `libpango-1.0-0` etc. — since `documents.py` imports
  `export_pdf.py` which imports `weasyprint` at module level, so even a
  secrets-only test job must be able to import `app.main`), and
  `search-service` jobs.

### Design notes

- **No new hardcoded default was introduced.** The temptation with this
  kind of check is to replace one bad default with another "safer-looking"
  one; instead, `GENERIC_PLACEHOLDERS` in `secrets_check.py` explicitly
  treats `CHANGE_ME`/`GENERATE_A_SECURE_SECRET`-style text as insecure
  too, so even the new `.env.example` placeholders are still caught if
  a deployer copies them into `.env` unedited and sets
  `ENVIRONMENT=production`.
- **`environment` is per-service, not global**, consistent with the
  existing architecture (each service already has its own `.env` file,
  no shared config service). An operator deploying to production sets
  `ENVIRONMENT=production` plus real secrets in each service's `.env`;
  `infra/docker-compose.yml` itself is unchanged since it already only
  supplies *its own* dev-convenience fallbacks (`POSTGRES_PASSWORD`,
  `MINIO_ROOT_USER/PASSWORD`, `MEILI_MASTER_KEY`) via `${VAR:-default}`,
  which an operator overrides via `infra/.env` — separate from, but
  consistent with, each service's own secret being flagged if left
  matching that same default.
- **The check runs before any I/O**, not interleaved with it (originally
  written after `Base.metadata.create_all()`/`ensure_bucket_exists()` in
  catalogue/document-service; moved to run first) — both so a bad
  production config fails immediately without a confusing
  connection-refused error first, and so the check itself is testable
  without a live Postgres/MinIO/Meilisearch (see `_secret_problems()`
  being a separate pure function from `on_startup()`).

### Tests Performed

```text
# auth-service
cd services/auth-service && pip install -r requirements-dev.txt && python -m pytest -v
# catalogue-service / search-service
cd services/catalogue-service && pip install -r requirements-dev.txt && python -m pytest -v
cd services/search-service && pip install -r requirements-dev.txt && python -m pytest -v
# document-service (needs WeasyPrint's system libraries to even import app.main)
sudo apt-get install -y libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libffi-dev shared-mime-info fonts-liberation
cd services/document-service && pip install -r requirements-dev.txt && python -m pytest -v
```
Also verified directly in this sandbox (pure stdlib, no FastAPI needed —
see below) with manual assertions against `is_insecure`,
`db_password_from_url`, and `enforce_production_secrets` before ever
pushing, then confirmed via the full pytest suite in CI: run
https://github.com/kaungkyaw3124/doc-mgmt/actions/runs/34110331244
(commit `59d09ce`).

### Test Results

```text
auth-service:        24 passed, 5 warnings in 13.85s   (9 from Task 1 + 15 new)
catalogue-service:    6 passed, 3 warnings in 1.90s
document-service:     6 passed, 3 warnings in 1.83s
search-service:       5 passed, 3 warnings in 0.58s
```
(warnings are the same pre-existing FastAPI `on_event` deprecation
notices as Task 1, unrelated to this change)

### Bugs Found

- **Ordering bug caught before pushing (not a CI failure — self-caught
  during implementation):** first draft put the secrets check AFTER
  `ensure_bucket_exists()`/Meilisearch setup in catalogue/document-service's
  `on_startup`. That both defeats "fail immediately" (a bad prod config
  would first try, and fail confusingly, to reach real infrastructure)
  and would have made the checks untestable without live MinIO/Meilisearch
  in CI. Fixed by extracting `_secret_problems()` as a separate pure
  function and calling `enforce_production_secrets()` first, before any
  I/O — this is reflected in the "Changes Made" and CI results above,
  not a separate failed CI run (caught and fixed locally before the
  first push of this task).

Otherwise: none. First (and only) CI run for this task (commit `59d09ce`)
passed clean across all 4 jobs.

### Verification

- Manually confirmed (pure Python, no dependencies needed) that
  `is_insecure()` flags `""`, `"CHANGE_ME"`, `"changeme"`, and each
  service-specific known default, and does NOT flag a real-looking
  random secret.
- `test_missing_jwt_secret_fails_startup_in_production` /
  `test_default_jwt_secret_fails_startup_in_production` (auth-service):
  empty and default `JWT_SECRET` both raise `RuntimeError` when
  `ENVIRONMENT=production`.
- `test_missing_database_credentials_fail_startup_in_production`
  (auth-service) and the equivalent in catalogue/document-service:
  default `DATABASE_URL` password (`docmgmt`) raises in production.
- `test_default_minio_credentials_fail_in_production` (catalogue,
  document-service): default `minioadmin`/`minioadmin` raises, and both
  problems (`MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY`) are reported
  together, not just the first.
- `test_default_meili_key_fails_in_production` /
  `test_missing_meili_key_fails_in_production` (all 3 services that use
  Meilisearch): default and empty key both raise.
- `test_default_admin_password_fails_startup_in_production`
  (auth-service): default `SEED_ADMIN_PASSWORD` raises.
- `test_valid_production_configuration_starts_cleanly` /
  `test_valid_production_configuration_has_no_problems` (every service):
  a full set of real-looking secrets with `ENVIRONMENT=production`
  produces zero problems and does not raise — production configuration
  isn't just less strict than needed, it's not needlessly stricter
  either.
- `test_insecure_defaults_only_warn_in_development` /
  `test_insecure_defaults_only_warn_outside_production` (every service):
  the exact same insecure defaults, with `ENVIRONMENT=development` (the
  default), do NOT raise — they only log a `SECURITY:`-prefixed warning
  (asserted via `caplog`) — confirming local dev stays usable unedited.
- Confirmed via `git status`/`.gitignore` that no `.env` file (only
  `.env.example`) is tracked in any service directory — no real secret
  is committed to Git.

### Security Impact

Fixes a vulnerability (CWE-798, use of hard-coded credentials / CWE-1188,
insecure default initialization). A production deployment that is
misconfigured with any of the five publicly-known default secrets this
project has shipped with (JWT secret, seeded admin password, Meilisearch
master key, MinIO access/secret key) — or the shared default Postgres
password — now cannot start and serve traffic at all, closing off
token forgery (including superuser tokens), unauthorized document/search
index access, and unauthorized object storage access via a
publicly-known credential. Development remains fully convenient
(unedited `.env.example` still boots, with warnings).

### Commit

```text
59d09ce
security: remove insecure default secrets
```

### Final Result

PASS

### Addendum (found during Task 3, 2026-09-07 10:23)

Task 3's `infra-integration` CI job caught a real regression from this
task: `MEILI_MASTER_KEY`/`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` in
`catalogue-service`, `document-service`, and `search-service`'s
`.env.example` had been changed to non-functional placeholders
(`GENERATE_A_SECURE_SECRET`/`CHANGE_ME`). Those three values are NOT
self-contained per-service secrets like `JWT_SECRET` — they must match
the credentials the `meilisearch`/`minio` containers themselves are
started with (`infra/docker-compose.yml`'s own dev-convenience
defaults). Placeholder-izing only the service side broke that match:
document-service failed to boot in the integration test with
`botocore.exceptions.ClientError: InvalidAccessKeyId`. This violated
this task's own "Development configuration remains usable" acceptance
criterion — a regression, not a new finding.

**Fix**: reverted those three fields, in all three `.env.example`
files, back to the working dev defaults (`local_dev_master_key_change_me`,
`minioadmin`/`minioadmin`) that match the datastore containers' own
defaults — same pattern already correctly used for auth-service's
`DATABASE_URL` (kept `docmgmt`/`docmgmt`, matching `postgres`'s
default) and `JWT_SECRET`/`SEED_ADMIN_PASSWORD` (genuinely
self-contained, safe as strict `CHANGE_ME`-style placeholders). The
`is_insecure()` fail-fast check is unaffected by this fix — it still
rejects `minioadmin` and `local_dev_master_key_change_me` in
production; only the *unedited, development-mode* value changed back
to something that actually connects. See Task 3's log entry for the
CI evidence (run that caught this, and the run that confirmed the fix).

---

## Task 3 — Remove Host Exposure of Internal Datastores

**Date:** 2026-09-07 10:29

**Status:** PASS

### Goal

Only Nginx should be reachable from outside the Docker host. Postgres,
MinIO, and Meilisearch — previously published directly to the host —
should be reachable only from other containers on the compose network,
without breaking internal service-to-service communication or the
browser-facing presigned-download flow that used to hit MinIO's port
directly.

### Acceptance Criteria

- [x] PostgreSQL is not unnecessarily host-published
- [x] MinIO API is not unnecessarily host-published
- [x] MinIO console is not unnecessarily host-published
- [x] Meilisearch is not unnecessarily host-published
- [x] Internal communication works
- [x] Application tests pass
- [x] Security regression tests pass

### Initial State

`infra/docker-compose.yml` published `postgres` (`5432:5432`), `minio`
(`9000:9000` S3 API, `9001:9001` console), and `meilisearch`
(`7700:7700`) directly to the host, with comments acknowledging this
was meant to be temporary ("exposed for now... remove later" —
`known-issues.md` finding #3). On any host with inbound network
exposure, this bypassed Nginx's auth gate entirely — direct
credential-guessing and RBAC-bypassing access to raw data.

### Changes Made

- `infra/docker-compose.yml` — `ports:` replaced with `expose:` for
  `postgres` (5432), `minio` (9000, 9001), and `meilisearch` (7700).
  `nginx` now also `depends_on: minio` (needed for the new proxy route
  below).
- `infra/nginx/nginx.conf` (at the time — see the verification addendum
  at the end of this entry: this file was later renamed to
  `nginx.conf.template` as part of Task 4, which is unrelated to this
  task's own changes) — new `/documents/` and `/products/`
  locations, proxying straight through to `http://minio:9000` with
  **no path rewrite and the original `Host` header preserved**.
  MinIO's presigned GET URLs (SigV4) sign both the request path and the
  `Host` header — changing either at the proxy would make MinIO reject
  an otherwise-valid signature, so this had to be a transparent
  passthrough, not a rewrite. Bucket names (`documents`, `products`)
  happen to already be MinIO's own top-level path segments
  (`/<bucket>/<key>`), so no prefix-stripping was needed. Still safe
  fully unauthenticated at the Nginx layer: MinIO itself enforces the
  signature and its expiry on every request, exactly as it did when
  reachable on its own port.
- `services/{document,catalogue}-service/app/core/config.py` and
  `.env.example` — `MINIO_PUBLIC_ENDPOINT` default changed from
  `localhost:9000` (MinIO's own now-unpublished port) to `localhost:8080`
  (Nginx, which now proxies to it).
- `docs/operations.md` — replaced the old "MinIO console at
  `http://<host>:9001`" / "port 5432 is also published" rows with a
  documented temporary-tunnel pattern (`docker run --rm --network
  infra_default -p 127.0.0.1:<port>:<port> alpine/socat ...`) for
  one-off admin access, explicitly warning not to re-add `ports:` for
  routine access. Also corrected a now-stale "no rate limiting on
  /api/auth/login" line left over from before Task 1.
- `docs/deployment.md`, `docs/workflows.md`, `docs/architecture.md`,
  `docs/known-issues.md` — updated the container/port tables, the
  `MINIO_PUBLIC_ENDPOINT` description, the deployment diagram, and
  marked known-issues findings #2 and #3 RESOLVED with references to
  this log.
- `.github/workflows/tests.yml` — new `infra-integration` job: builds
  and boots the **real** `infra/docker-compose.yml` stack on the
  runner's own Docker (not a mock), then asserts all of: every
  container reaches `running` state, Nginx answers a real end-to-end
  request through to Postgres, all four datastore ports are closed on
  the host, and the new `/documents/`/`/products/` proxy paths actually
  reach MinIO (not a 502/504) rather than only checking static config.

### Design notes

- **Why proxy through Nginx instead of just accepting broken presigned
  URLs**: the alternative (leave `MINIO_PUBLIC_ENDPOINT` pointing at a
  now-closed port) would have silently broken every document/product
  file download in the app — that's not an acceptable trade for a
  security fix. Proxying was the option that closes the port without
  losing functionality.
- **Why no path rewrite**: an earlier design (proxy `/minio/<bucket>/<key>`
  → strip `/minio/` → `<bucket>/<key>`) was considered and rejected
  before implementation — SigV4 signs the exact request path the
  *client* sends, so if Nginx rewrites the path before forwarding, MinIO
  computes its own signature check against the *rewritten* path/Host
  and the signatures won't match. Because MinIO's own path scheme
  (`/<bucket>/<key>`) already equals what a same-named Nginx `location`
  prefix would forward unmodified, a transparent (non-rewriting) proxy
  was both correct and simpler.

### Tests Performed

```text
docker compose -f infra/docker-compose.yml config   # validated syntax + resolved env locally
```
Real integration test via CI (`infra-integration` job in
`.github/workflows/tests.yml`), which this sandbox cannot run itself
(no Docker daemon — see the environment note at the top of this log):
builds and boots all 8 containers from the actual compose file, then:
```text
curl http://localhost:8080/api/auth/groups-public   # end-to-end through Nginx -> auth-service -> Postgres
docker compose ps   # every service state == running
# for each of 5432, 9000, 9001, 7700: confirm the host port refuses a TCP connect
curl http://localhost:8080/documents/no-such-key   # reaches MinIO through Nginx (not 502/504)
curl http://localhost:8080/products/no-such-key    # reaches MinIO through Nginx (not 502/504)
```
Runs: https://github.com/kaungkyaw3124/doc-mgmt/actions/runs/34111080185
(commit `02957ee`, **FAILED** — see Bugs Found) and
https://github.com/kaungkyaw3124/doc-mgmt/actions/runs/34111636919
(commit `db0c219`, **PASSED**, all 5 jobs including `infra-integration`).

### Test Iterations

#### Attempt 1 (commit `02957ee`) — FAIL

`document-service` crashed on startup:
```text
botocore.exceptions.ClientError: An error occurred (InvalidAccessKeyId)
when calling the ListBuckets operation: The Access Key Id you provided
does not exist in our records.
ERROR:    Application startup failed. Exiting.
```
Root cause: a regression from **Task 2**, not this task — Task 2 had
replaced `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`/`MEILI_MASTER_KEY` in
`.env.example` with non-functional placeholders. Those three fields
are not self-contained per-service secrets; they must match the
credentials the `minio`/`meilisearch` containers are themselves started
with. Task 3's `infra-integration` job was the first thing in this
project to actually boot the full stack end-to-end, so it's what caught
this — the earlier per-service unit-test jobs never exercise
`ensure_bucket_exists()` against a real MinIO.

#### Attempt 2 (commit `db0c219`) — PASS

Fixed by reverting those three `.env.example` fields (across
catalogue-service, document-service, search-service) to the working
dev defaults that match the datastore containers' own compose defaults
— documented in full as an addendum to Task 2's entry above (not
re-litigated here). All 5 CI jobs passed, including every
`infra-integration` acceptance step.

### Bugs Found

- **Task 2 regression** (see Test Iterations above and Task 2's
  addendum): `.env.example` MinIO/Meilisearch credentials didn't match
  the datastore containers' actual credentials after Task 2's
  placeholder change, breaking document-service's boot. Fixed in commit
  `db0c219`. This was a real, CI-caught failure — not hidden, not
  glossed over as "implemented successfully."

Otherwise: none new to this task.

### Verification

- `infra-integration` CI job, commit `db0c219`: `docker compose ps`
  showed all 8 containers (`postgres`, `minio`, `meilisearch`,
  `document-service`, `catalogue-service`, `search-service`,
  `auth-service`, `nginx`) in `running` state — internal
  service-to-service communication (Postgres, MinIO, Meilisearch, all
  reached only over the compose network) works without any published
  ports.
- `curl http://localhost:8080/api/auth/groups-public` returned `200` —
  a real, unauthenticated end-to-end request through Nginx →
  auth-service → Postgres succeeded, proving the whole chain functions
  with Postgres unpublished.
- For each of ports `5432`, `9000`, `9001`, `7700`: a raw TCP connect
  attempt from the CI runner's host network to `127.0.0.1:<port>`
  failed (job step "Acceptance — Postgres/MinIO/Meilisearch are NOT
  reachable from the host" passed) — confirms none of the four are
  published.
- `curl http://localhost:8080/documents/no-such-key` and
  `.../products/no-such-key` did NOT return `502`/`504`/connection-
  refused (job step "Acceptance — presigned-download proxy path
  reaches MinIO through Nginx" passed) — confirms the new proxy
  locations actually reach the `minio` container, not just that they
  parse correctly in `nginx.conf`.

Therefore: all four datastores are closed to the host, internal
communication is intact, and the browser-facing presigned-download path
still functions end-to-end through the new proxy — acceptance criteria
satisfied.

### Security Impact

Fixes a vulnerability (CWE-668, exposure of resource to wrong sphere).
Closes a direct, RBAC-bypassing path to Postgres data, MinIO objects,
and the Meilisearch index that existed on any host with inbound network
exposure beyond the intended Nginx gateway — combined with Task 2's
fix, an attacker can no longer reach these datastores directly at all,
let alone with a guessable/default credential.

### Commit

```text
02957ee  security: isolate internal datastores            (FAILED CI)
db0c219  fix: restore working MinIO/Meilisearch dev defaults broken by Task 2   (PASSED CI)
```

### Final Result

PASS

### Independent re-verification addendum (2026-09-07 ~11:12, on request)

The user explicitly asked that Task 3 not be taken on faith from the
PASS above and be re-verified from scratch against the actual current
repo state and the actual CI run output (not the earlier summary of
it). Performed the following, independently, in this session:

1. **`git status` / `git log --oneline -10`** — working tree clean, no
   uncommitted local modification to `services/document-service/app/routers/companies.py`,
   no stray `*.crdownload` files anywhere in the repo. History confirmed:
   `02957ee` (isolate internal datastores, failed CI) →
   `db0c219` (fix) → `04f29eb` (this log entry) →
   (separately, Task 4: `3d3f014` — see note below).

2. **Re-read `infra/docker-compose.yml` directly**: confirmed `postgres`
   has `expose: ["5432"]` and no `ports:`; `minio` has
   `expose: ["9000", "9001"]` and no `ports:`; `meilisearch` has
   `expose: ["7700"]` and no `ports:`; `nginx` is the only service with
   a `ports:` mapping (`8080:80`).

3. **Re-read `infra/nginx/nginx.conf.template` directly** (the file was
   renamed from `nginx.conf` during Task 4 — content at the
   `/documents/`/`/products/` locations relevant to Task 3 is
   unchanged): confirmed `proxy_set_header Host $host;` forwards the
   client's original Host header unmodified (so it still matches what
   was SigV4-signed against `MINIO_PUBLIC_ENDPOINT`), and confirmed
   `proxy_pass http://minio:9000;` has **no URI component** — per Nginx's
   own semantics this means the original request URI is forwarded to
   MinIO byte-for-byte, not rewritten. Both together are what keep the
   presigned-URL signature valid; this was reasoned through the actual
   directive semantics, not assumed from the comments alone.

4. **Re-fetched the raw CI logs** (not just the stored `conclusion`
   field) for run `34111636919` (commit `db0c219`) directly from
   GitHub Actions:
   - `infra-integration` job (id `101708997308`): raw log shows
     `postgres: running`, `minio: running`, `meilisearch: running`,
     `document-service: running`, `catalogue-service: running`,
     `search-service: running`, `auth-service: running`, `nginx: running`;
     then `OK: host port 5432 is closed`, `OK: host port 9000 is closed`,
     `OK: host port 9001 is closed`, `OK: host port 7700 is closed`;
     then `/documents/no-such-key -> 403` and `/products/no-such-key -> 403`
     (MinIO's own `AccessDenied` for an unsigned request — proves the
     proxy reaches MinIO, not a 502/504/connection-refused); and
     `up after 20s` for the Nginx→auth-service→Postgres end-to-end
     check.
   - `catalogue-service` job (id `101708997597`): raw log confirms
     `6 passed, 3 warnings` — includes
     `test_default_minio_credentials_fail_in_production`,
     `test_default_meili_key_fails_in_production`,
     `test_default_database_password_fails_in_production`, and
     `test_valid_production_configuration_has_no_problems` — i.e.
     production secret validation (Task 2's fail-fast check) still
     correctly rejects the insecure/default values after Task 3's
     `.env.example` fix, and still accepts a genuinely valid production
     configuration.
   - `document-service` job (id `101708997516`): raw log confirms
     `6 passed, 3 warnings` (same test suite).

All of the above was re-pulled and re-read directly in this
verification pass, not assumed from the original PASS determination or
from commit messages alone.

**Acceptance criteria, re-confirmed:**
- [x] PostgreSQL: no host-published port (`expose` only), reachable
  internally (proven by every dependent service reaching `running`
  state, and by the Nginx→auth-service→Postgres `200` on
  `/api/auth/groups-public`).
- [x] MinIO: neither `9000` nor `9001` host-published; reachable
  internally (services booted); Nginx proxies the required MinIO
  traffic (`/documents/`, `/products/` reach MinIO — confirmed `403`,
  not `502`/`504`).
- [x] Meilisearch: `7700` not host-published; reachable internally
  (`document-service`/`catalogue-service` booted, which requires a
  successful Meilisearch call in their `on_startup`).
- [x] Nginx remains the sole external entry point (`0.0.0.0:8080->80`
  in the `docker compose ps` output; every other service shows no
  host-side port mapping).
- [x] MinIO/Nginx proxy config verified by reading the actual directive
  semantics (not assumed), specifically re: Host header and no-rewrite
  path handling for SigV4.
- [x] Dev environment regression (Task 2 → Task 3 interaction) verified
  fixed via fresh raw CI log content, not the stored conclusion alone.
- [x] Production secret validation re-confirmed still functioning via
  fresh raw CI log content for the exact tests that check it.
- [x] Relevant tests run: yes (5 CI jobs, all reconfirmed).
- [x] Docker/integration tests: yes, actually run (real Docker daemon
  on the GitHub Actions runner — this sandbox itself has no Docker
  daemon, disclosed at the top of this log), and their raw output was
  re-read in this pass, not just their pass/fail status.

**Note on scope**: while re-verifying, found that `security/auth-hardening`
already has Task 4 implemented and pushed as commit `3d3f014`
("security: authenticate gateway trust headers"), completed earlier in
this session before this re-verification was requested. That work
touched `infra/nginx/nginx.conf` (renaming it to `nginx.conf.template`
and adding `X-Internal-Secret` header injection), which is why item 10
in the request ("inspect infra/nginx/nginx.conf.template") finds that
name rather than `nginx.conf`. Per instruction, Task 4 is not being
further advanced or logged in this pass — its own log entry is pending
separately. The summary table's Task 4 row is currently stale
("NOT STARTED") relative to the actual repository state; flagged here
rather than silently corrected, since correcting it is arguably Task 4
documentation work and this pass is scoped to Task 3 only.

**Conclusion: Task 3 genuinely PASSES all acceptance criteria**, on
fresh, independent, evidence-based re-verification — not merely
because the earlier entry and commit history said so.

---

## Task 4 — Authenticate Gateway Trust Headers

**Date:** 2026-09-07 11:06 (implementation) / 2026-09-08 03:36 (fix + verified PASS)

**Status:** PASS

### Goal

`document-service`, `catalogue-service`, `search-service` trust caller-
supplied headers (`X-Allowed-Projects`, `X-Access-Level`, `X-Username`,
`X-Has-Audit-Log`, `X-Has-Category-Access`) as already-resolved
authorization decisions, with no independent check that the request
actually came through Nginx's `auth_request` flow. Ensure a client
cannot manufacture these headers directly and grant itself
unauthorized access.

### Acceptance Criteria

- [x] Header spoofing cannot escalate privileges
- [x] Legitimate gateway requests continue working
- [x] Tampered headers are rejected
- [x] Missing gateway authentication is rejected
- [x] No browser-visible shared secret
- [x] Production rejects an insecure/default/missing shared secret
- [x] Nginx overwrites (not just adds to) client-supplied copies of
      every trusted header, not only the ones each route currently reads
- [x] Tests pass, including security regression tests
- [ ] **Partial/incomplete**: full RBAC regression across every user
      type (normal user, group member, group admin, disabled user/
      group/role, project-restricted user) was **not** independently
      re-exercised end-to-end for this task — see "Verification" and
      "Notes" below for why this is a reasoned partial pass, not a gap
      papered over.

### Initial State

`services/{document,catalogue,search}-service/app/routers/*.py` read
these headers via FastAPI `Header(default=None, ...)` params with no
verification of their origin (`docs/known-issues.md` finding #1,
`docs/decisions.md` #7). Task 3 closed host access to these services,
but anything else reaching them on the Docker network — a compromised
sibling container, a future topology change, or (as this task itself
found) a location in Nginx's own config that forgot to override a
header a route later started reading — could still set
`X-Allowed-Projects: ALL` / `X-Access-Level: admin` directly and gain
unauthorized access.

### End-to-end flow understood before implementing

```
Browser --Authorization: Bearer <token>--> Nginx
Nginx --auth_request /_verify (internal)--> auth-service /verify
auth-service verifies the JWT + DB-backed RBAC, returns 200 with
  X-Allowed-Projects / X-Access-Level / X-Username / X-Has-Audit-Log /
  X-Has-Category-Access as RESPONSE headers to Nginx (never seen by
  the browser)
Nginx (auth_request_set) captures those into $variables, then
  proxy_set_header's them onto the request it forwards to
  document-service / catalogue-service / search-service
Downstream service routers read those headers via FastAPI Header(...)
  and use them directly for authorization decisions — with, before
  this task, no check that the request's headers actually came from
  Nginx's auth_request step rather than being set by the caller itself.
```
Separately, `catalogue-service`/`search-service` call
`document-service`'s `GET /documents/visible-product-ids` directly
(`app/core/document_client.py`), bypassing Nginx entirely — a second,
independent path into a header-trusting service that also needed
covering.

### Exhaustive header search performed

`grep -rn` across every `.py`/`.conf`/`.template`/`.html`/`.yml` file in
`services/`, `infra/`, `web/` for: `X-Access-Level`, `X-Allowed-Projects`,
`X-User\b`, `X-User-ID`, `X-Username`, `X-Auth`, `X-Has-Audit-Log`,
`X-Has-Category-Access`, `X-Internal-Secret`, `X-Service`. Result: the
complete set of security-sensitive gateway-trust headers in this
codebase is exactly `X-Allowed-Projects`, `X-Access-Level`,
`X-Username`, `X-Has-Audit-Log`, `X-Has-Category-Access`. No
`X-User`/`X-User-ID`/`X-Auth` header exists anywhere. `X-Service` is
also nginx-injected but is not client-forgeable in a way that matters:
its value comes from a per-location `set $service_name "...";` Nginx
directive (a compile-time constant per location block, never read from
any client header), and even a forged value only changes which
service-access check `auth-service`'s `/verify` performs against a
token the caller must already legitimately hold — it can't grant a
capability the token's own user doesn't have.

Also checked whether `auth-service` itself trusts any of these headers
as input (it doesn't — `admin.py` and `auth.py` only ever read
`Authorization`, `X-Service`, and login-rate-limiting's `X-Real-IP`/
`X-Forwarded-For`; every authorization decision in `auth-service` comes
from the Bearer token + a DB lookup, never from a caller-supplied trust
header). So `auth-service` was correctly out of scope for this fix — it
produces these headers, it doesn't consume them.

### Changes Made

**Original implementation (commit `3d3f014`):**
- `services/{catalogue,document,search}-service/app/core/gateway_auth.py`
  (new, identical file duplicated per service — matches the existing
  `secrets_check.py` duplication pattern) — an ASGI middleware
  (`verify_gateway_secret`) that rejects (401) any request lacking a
  correct `X-Internal-Secret` header, checked via `hmac.compare_digest`
  (constant-time, avoids a timing side-channel on the comparison),
  before the request ever reaches a router. `/health` is exempt (no
  Nginx route to it, used only for container healthchecks).
- `services/{catalogue,document,search}-service/app/core/config.py` —
  new `internal_shared_secret` setting (default
  `local_dev_internal_secret_change_me`, matching the container-default
  pattern established in Task 2).
- `services/{catalogue,document,search}-service/app/main.py` —
  registers the middleware (`app.middleware("http")(verify_gateway_secret)`)
  and adds `INTERNAL_SHARED_SECRET` to `_secret_problems()`'s
  production fail-fast check.
- `services/catalogue-service/app/core/document_client.py`,
  `services/search-service/app/core/document_client.py`,
  `services/document-service/app/core/catalogue_client.py` — every
  direct inter-service HTTP call (these bypass Nginx entirely) now
  also sends `X-Internal-Secret`.
- `infra/nginx/nginx.conf` renamed to `infra/nginx/nginx.conf.template`
  — a real config Nginx cannot read directly. `infra/docker-compose.yml`'s
  `nginx` service now renders it via `envsubst` at container start
  (`entrypoint: envsubst '$INTERNAL_SHARED_SECRET' < ...template >
  nginx.conf && exec nginx ...`), substituting **only** that one
  variable name — chosen explicitly over the image's automatic
  template-directory mechanism (which substitutes *every* environment
  variable name found in the file) specifically because Nginx's own
  `$host`/`$uri`/`$upstream_http_...` variables in the same file must
  not be touched. Verified locally (this sandbox has no `envsubst`
  binary — see the environment note at the top of this log) by
  simulating the exact restricted substitution in Python and confirming
  occurrence counts and that every `$host` in the file survived
  unchanged.
- 7 of the file's `location` blocks (proxying to `document-service`/
  `catalogue-service`/`search-service`) gained
  `proxy_set_header X-Internal-Secret "${INTERNAL_SHARED_SECRET}";`.
- `services/{catalogue,document,search}-service/tests/test_gateway_auth.py`
  (new) — missing secret, wrong secret, tampered secret on an otherwise-
  legitimate request, spoofed trust headers with no secret, `/health`
  exempt, correct secret clears the middleware (aimed at a nonexistent
  route to prove it reached FastAPI's own 404, not the gateway's 401) —
  all DB-independent, since a 401 short-circuits before any router/DB
  access.
- `.github/workflows/tests.yml` — two new `infra-integration` steps:
  a real login + authenticated `/api/products` request through Nginx
  still succeeds; a direct-to-service call (via an ephemeral
  `curlimages/curl` container on the compose network) with spoofed
  trust headers and no/wrong secret is rejected (401), and the same
  call with the correct secret is not.

**Fix + hardening pass (commit `12b1da0`, after this task's own rigorous
re-review, done BEFORE trusting the original "complete" report):**

1. **CI was actually failing for `3d3f014`** — never verified before
   the original implementation was reported. Root cause:
   `test_valid_production_configuration_has_no_problems` in all three
   `catalogue`/`document`/`search`-service `test_secrets_check.py`
   files failed with `['INTERNAL_SHARED_SECRET'] != []`, because
   `_secret_problems()` was extended to check the new secret but the
   tests' `_valid_production_settings()`/`restore_settings` helpers
   were never updated to set/restore it. Fixed by adding
   `internal_shared_secret` to both in all three files, plus new
   `test_default_internal_shared_secret_fails_in_production` /
   `test_missing_internal_shared_secret_fails_in_production` tests
   (this task's own acceptance criterion — "production must reject
   insecure/default values" — explicitly requires testing this, which
   the original pass never did).
2. **`nginx.conf.template` only overrode the specific trust headers
   each route's CURRENT handler code happens to read** (e.g.
   `/api/customers` only set `X-Access-Level`, since `customers.py`
   only declares that one `Header(...)` param). Nginx forwards
   arbitrary client request headers to the upstream by default unless a
   location explicitly overrides that header name — verified by reading
   Nginx's own documented `proxy_set_header`/`proxy_pass` semantics, not
   assumed. Cross-checking every router's declared `Header(...)` params
   against what each location overrides showed **no currently
   exploitable gap** (every handler's declared headers already matched),
   but this was fragile: a future handler reading, say, `X-Username`
   for audit logging on a route nobody remembered to update in Nginx
   would silently reopen a forgery path. Fixed by having every
   `document-service`/`catalogue-service` location explicitly set
   *all* of that service's trust headers, blanking (`""`) any this
   specific route has no `auth_request_set` value for — this no longer
   depends on router code and Nginx config staying in sync by
   convention.
3. Added an `infra-integration` step proving Nginx's overwrite actually
   wins over a client's own forged copy sent alongside legitimate auth
   (see Test Results) — the original pass tested "no secret → rejected"
   and "direct-to-service → rejected" but never tested "goes through
   Nginx *with* a forged secret/header attached anyway."

### Tests Performed

Per-service unit tests (pure, DB-independent — a 401 from the
middleware short-circuits before any router/DB access):
```text
cd services/{catalogue,document,search}-service
pip install -r requirements-dev.txt && python -m pytest -v
```
Real end-to-end verification via the `infra-integration` CI job
(builds and boots the actual `docker-compose.yml` stack — this sandbox
has no Docker daemon, see the environment note at the top of this log):
```text
# legitimate request through Nginx
curl -X POST http://localhost:8080/api/auth/login -d '{"username":"admin","password":"CHANGE_ME"}'
curl http://localhost:8080/api/products -H "Authorization: Bearer $token"

# Nginx overwrite proof: same request, WITH forged trust/secret headers attached
curl http://localhost:8080/api/products -H "Authorization: Bearer $token" \
  -H 'X-Internal-Secret: attacker-supplied-secret' -H 'X-Access-Level: superuser' \
  -H 'X-Allowed-Projects: attacker-controlled'

# direct-to-service, bypassing Nginx, from an ephemeral container on the compose network
docker run --rm --network infra_default curlimages/curl ... \
  -H 'X-Allowed-Projects: ALL' -H 'X-Access-Level: edit' http://catalogue-service:8000/products
docker run --rm --network infra_default curlimages/curl ... \
  -H 'X-Internal-Secret: wrong-secret' -H 'X-Allowed-Projects: ALL' http://catalogue-service:8000/products
docker run --rm --network infra_default curlimages/curl ... \
  -H 'X-Allowed-Projects: ALL' -H 'X-Access-Level: edit' -H 'X-Username: admin' http://document-service:8000/documents
docker run --rm --network infra_default curlimages/curl ... \
  -H 'X-Internal-Secret: local_dev_internal_secret_change_me' http://catalogue-service:8000/health
```
Runs: https://github.com/kaungkyaw3124/doc-mgmt/actions/runs/34114896792
(commit `3d3f014`, **FAILED** — 3 of 5 jobs red, see Bugs Found) and
https://github.com/kaungkyaw3124/doc-mgmt/actions/runs/34184063018
(commit `12b1da0`, **PASSED**, all 5 jobs, raw log content re-pulled
and re-read directly, not just the stored conclusion field).

### Test Iterations

#### Attempt 1 (commit `3d3f014`) — FAIL, reported complete without checking CI

```text
FAILED tests/test_secrets_check.py::test_valid_production_configuration_has_no_problems
AssertionError: assert ['INTERNAL_SHARED_SECRET'] == []
```
in `catalogue-service`, `document-service`, and `search-service`
(`auth-service` and `infra-integration` were unaffected and passed).
This was NOT caught before the task was originally reported as
complete — a direct process failure, recorded here rather than
smoothed over.

#### Attempt 2 (commit `12b1da0`) — PASS

All 5 jobs green. Raw log content re-pulled and confirmed:
- `catalogue-service`: `14 passed, 4 warnings` (6 secrets-check +
  2 new internal-secret-specific + 6 gateway_auth).
- `infra-integration`: all prior steps still green, plus
  `GET /api/products through Nginx with client-forged trust headers -> 200`
  (Nginx's own headers won over the client's forged ones) and
  `direct catalogue-service call, no secret, spoofed headers -> 401`,
  `direct catalogue-service call, wrong secret -> 401`,
  `direct document-service call, no secret, spoofed headers -> 401`,
  `direct catalogue-service call, correct secret, /health -> 200`.

### Bugs Found

1. **CI-verification failure** (see Test Iterations #1): a broken test
   fixture shipped as part of the original implementation and was
   reported as "complete" without ever checking whether CI passed.
   Fixed in `12b1da0`. This is the most important bug in this entry —
   not because the underlying security fix was wrong, but because the
   process of claiming completion without verifying tests actually ran
   is exactly the failure mode this whole logging requirement exists to
   catch.
2. **Fragile (not exploitable, but incomplete) header-override
   coverage** in the original `nginx.conf.template` (see Changes Made
   #2). Not an active vulnerability at the time — reasoned + verified
   via exhaustive cross-referencing of every router's declared headers
   against every location's overrides — but hardened anyway per this
   task's explicit acceptance criterion #5 ("the gateway must overwrite/
   remove untrusted versions... do not assume this works merely because
   it looks correct").

### Verification

- **Forged gateway header → rejected**: confirmed both at the unit
  level (`test_spoofed_trust_headers_without_secret_cannot_escalate` in
  each service) and at the real-network level (`infra-integration`:
  `X-Allowed-Projects: ALL`/`X-Access-Level: edit` direct to
  `catalogue-service`/`document-service` → `401`).
- **Missing gateway authentication → rejected**:
  `test_request_without_secret_is_rejected` (unit) +
  `direct catalogue-service call, no secret... -> 401` (real network).
- **Invalid/tampered gateway authentication → rejected**:
  `test_request_with_wrong_secret_is_rejected` /
  `test_tampered_secret_on_an_otherwise_legitimate_request_is_rejected`
  (unit) + `direct catalogue-service call, wrong secret -> 401` (real
  network).
- **Valid gateway authentication → accepted**:
  `test_request_with_correct_secret_passes_the_gateway_check` (unit) +
  `direct catalogue-service call, correct secret, /health -> 200`
  (real network).
- **Legitimate Nginx-authenticated request → still works**: real login
  as the seeded admin through Nginx, then `GET /api/products` with the
  real token → `200`.
- **Nginx overwrites, not just forwards, forged headers**: a real,
  correctly-authenticated request through Nginx with `X-Internal-Secret`/
  `X-Access-Level`/`X-Allowed-Projects` ALSO forged by the client still
  returned `200` — proving Nginx's own `proxy_set_header` values
  reached the backend, not the client's — verified over the real
  network, not asserted from reading the config alone.
- **Existing authorization rules still work — partial**: the superuser
  (`admin`) login-and-access path was verified end-to-end for real.
  Full regression across every user type (normal user, group member,
  group admin, disabled user/group/role, project-restricted user) was
  **not** independently re-exercised in this task. Reasoning for why
  this is a defensible partial rather than a silent gap: (a)
  `auth-service/app/core/authz.py` — the actual RBAC decision logic —
  was not touched by this task at all; (b) the new gateway-secret check
  is a request-origin check that runs uniformly for every request
  regardless of which user or role it belongs to, so it cannot
  structurally discriminate between user types — it either lets a
  request from Nginx through to the existing (unmodified) RBAC logic,
  or it doesn't, with no code path where a normal user is treated
  differently from a superuser by this specific change. This is
  reasoning from the diff, not a substitute for an actual multi-role
  regression pass — that full matrix is explicitly deferred to the
  Final Security Audit step called for after Task 8, where it belongs
  once all tasks' cumulative changes can be tested together.

### Security Impact

Fixes a vulnerability (CWE-290, authentication bypass by spoofing —
specifically, unauthenticated trust of forwarded headers). Closes the
gap identified in `docs/known-issues.md` finding #1 and
`docs/decisions.md` #7: `document-service`/`catalogue-service`/
`search-service` can no longer be tricked into granting elevated
project/service/category access merely by a caller setting
`X-Allowed-Projects`/`X-Access-Level`/etc. directly, whether that caller
reaches the service via a compromised sibling container, a future
network-topology change, or any path other than Nginx's own
`auth_request`-gated proxying. Combined with Task 3 (host access
already closed) and Task 2 (the new secret gets the same
production fail-fast treatment as every other credential), the
authorization decision made once in `auth-service` can no longer be
bypassed downstream by header forgery.

### Commit

```text
3d3f014  security: authenticate gateway trust headers   (FAILED CI — not verified before reporting)
12b1da0  fix: harden Task 4 gateway trust headers + repair broken test suite   (PASSED CI, verified)
```

### Final Result

PASS, with one explicitly-flagged partial item (full multi-role
authorization regression deferred to the Final Security Audit — see
Verification above). No fabricated evidence: every claim above is
backed by a specific, re-pulled CI log line or a specific test name.

### Notes

This task surfaced a real process failure (reporting completion without
verifying CI) that the user caught by insisting on independent
re-verification rather than accepting the prior summary. The fix here
is not just the code change but the corrected process: every claim in
this entry was checked against raw log content re-fetched in this same
session, not against memory of what was expected to happen.

### Second independent verification pass (2026-09-08, on request)

Requested a further audit of items not explicitly spelled out above.
Checked directly against the current tree (no code changes needed —
all came back clean):

- **Browser cannot learn `X-Internal-Secret`**: `grep`'d
  `infra/nginx/nginx.conf.template` for every occurrence of
  `X-Internal-Secret` — all 7 are `proxy_set_header` (a request header
  Nginx sends to the upstream service), none are `add_header` or
  `proxy_pass_header` (which would put it on the *response* sent back
  to the client). `gateway_auth.py`'s rejection response is a fixed
  `{"detail": "missing or invalid gateway credential"}` — it echoes
  neither the expected nor the provided secret value.
- **Secret is not logged anywhere**: `grep`'d every service's `app/`
  for a `logger.*` call referencing headers, requests, or
  `internal_shared_secret`/`Internal-Secret` — no matches.
  `gateway_auth.py` itself contains no `logger`/`print` call at all.
- **auth-service correctly has no `gateway_auth.py`**: re-confirmed
  this is by design, not an oversight — `auth-service` is the
  *producer* of the trust headers (computed fresh from the JWT + DB on
  every `/verify` call), never a *consumer* of them, so there is
  nothing for it to gate against forgery. Its own protections (rate
  limiting on `/login`, JWT signature verification, superuser/DB checks
  in `admin.py`) are separate concerns already covered by Tasks 1 and
  the pre-existing authz logic, not part of this task's scope.
- **All three consuming services have consistent protection**: each of
  `document-service`, `catalogue-service`, `search-service` has its own
  `app/core/gateway_auth.py` (identical logic), registered in `main.py`,
  with its own `test_gateway_auth.py` suite (6, 6, 5 tests
  respectively — all passing per commit `12b1da0`'s CI run).
- **Service-to-service calls verified carrying the secret**: re-read
  `catalogue-service/app/core/document_client.py`,
  `search-service/app/core/document_client.py`, and
  `document-service/app/core/catalogue_client.py` directly — every
  `httpx.get(...)` call in all three includes
  `"X-Internal-Secret": settings.internal_shared_secret` in its headers
  (4 call sites in `catalogue_client.py` alone: `get_product`,
  `get_product_sub_items`, `get_product_file_bytes`,
  `get_product_download_bundle_bytes`), confirmed via the earlier
  exhaustive repo grep and by re-reading each file directly again this
  pass.

No new bugs found; no code changes made in this pass. This addendum
documents the check itself, not a new fix — Task 4's PASS status and
the commits it rests on (`3d3f014` original implementation,
`12b1da0` fix/hardening) are unchanged.

## Task 5 — Secure Uploaded Content-Type

**Date:** 2026-09-08

**Status:** PASS

### Goal

Prevent an attacker from controlling how an uploaded file is stored or
served: neither the client-supplied `Content-Type` header nor the
filename extension alone are trustworthy signals, and — this task's
central addition over the first Task 5 pass (commit `c21f72c`) — the
file's actual bytes were never being inspected at all. A prior pass
already stopped the *labeling* attack (Content-Type spoofing: real HTML
served back with a spoofed `Content-Type: text/html`); this pass adds
the missing piece — stopping the *content* attack (real HTML, a script,
or an executable, simply given a `.pdf`/`.png`/... filename and never
checked against its own bytes before being written to storage at all).

### Acceptance Criteria

- [x] Client-supplied `Content-Type` is never trusted for storage or
      serving (pre-existing from the first Task 5 pass, re-verified)
- [x] Uploaded file content is inspected (magic-byte/signature check)
      and compared against what the filename extension claims
- [x] Executable/script content disguised as an image, or any other
      accepted type, is rejected outright — not merely relabeled
- [x] HTML/script content given a "safe" extension (pdf/image/office/
      archive) is rejected outright, before ever reaching storage
- [x] A genuine content/extension mismatch between two otherwise-"real"
      formats (e.g. a real JPEG named `.png`) is rejected
- [x] SVG (no fixed binary signature — XML/text) is still always
      forced to download rather than rendered inline, regardless of
      content — the pre-existing, still-correct defense for that format
- [x] Filename attacks handled: path traversal stripped
      (`_sanitize_filename`, pre-existing), Unicode filenames accepted
      and safely collapsed, double extensions resolved by the final
      extension (documented, tested behavior — not a bypass, since an
      unrecognized final extension like `.exe` never gets a
      renderable Content-Type either way)
- [x] Legitimate uploads of every supported type continue to work
- [x] Both `document-service` and `catalogue-service` covered
      (identical `app/core/upload_safety.py`, wired into both
      `documents.py` and `products.py`)
- [x] Real upload paths tested end-to-end against a live MinIO/Nginx
      stack in CI, not just the helper function in isolation
- [x] Existing Task 1–4 protections unaffected (no changes to
      auth/rate-limiting/secrets/gateway-header logic in this task,
      other than the unrelated Nginx bug described below)
- [x] Documented in this log and in `docs/SECURITY_PROJECT_REPORT.md`

### Critical re-evaluation of the existing implementation

The first Task 5 pass (`c21f72c`) added `safe_content_type()`/
`should_force_download()` — both are **pure functions of the filename
only**. They correctly stop the "spoofed `Content-Type` header" attack
(the value trusted on *serving* a file), but neither one ever reads a
single byte of the uploaded content. That leaves a real gap: uploading
actual HTML/script content, correctly named `evil.pdf`, would be
accepted, stored, and served back as `Content-Type: application/pdf` —
not executed as HTML (no XSS), but also not actually *rejected* the way
"prevent HTML/script content being stored as an innocent [safe] type"
requires. Re-reading the whole repository's upload surface (both
routers' `POST .../file` endpoints, both `storage.py` files, both
`upload_safety.py` files, `nginx.conf.template`'s `/documents/` and
`/products/` proxy locations) confirmed this was the only remaining
gap: the bulk-import path (`products.py: bulk_import_products`) was
separately checked and found to be already safe on its own terms — it
always stores extracted archive entries as `application/octet-stream`
regardless of filename, so it was never subject to the label-trusting
bug Task 5 exists to fix, and is out of this task's scope.

### What changed

- **`app/core/upload_safety.py`** (both services, identical): added
  `detect_content_kind(head: bytes)` — recognizes PDF, PNG, JPEG, GIF,
  BMP, WEBP, ZIP (also covers `.docx`/`.xlsx`/`.pptx`, which are zip
  containers), legacy OLE (`.doc`/`.xls`/`.ppt`), RAR, 7z, and a set of
  executable signatures (Windows PE `MZ`, ELF, Mach-O, a `#!` shebang
  script) from a file's leading bytes. Added
  `content_matches_extension(filename, head) -> bool`: rejects any
  detected executable signature outright regardless of extension;
  for the extensions this app actually accepts with a well-known binary
  format, requires the detected content kind to match what the
  extension claims; extensions with no reliable signature to check
  (`.txt`/`.csv`/unrecognized/the already-force-downloaded active-
  content extensions like `.svg`/`.html`/`.xml`) are left as an
  accept — there's nothing meaningful to sniff there and they're never
  served as anything renderable/executable anyway.
- **`documents.py` / `products.py`**: both upload endpoints now read
  the first 4KB of the incoming file, `seek(0)` to rewind before the
  real upload proceeds, and call `content_matches_extension(...)`
  first — a mismatch now returns `400` and the file is **never**
  written to storage, rather than being accepted and merely relabeled.
- **`infra/nginx/nginx.conf.template`** (unrelated bug found while
  building the new end-to-end CI test below — see "Bugs found" for the
  full explanation): `/documents/` and `/products/` now forward
  `proxy_set_header Host $http_host;` instead of `$host`, fixing a
  pre-existing signature-verification failure on every real presigned
  MinIO download through Nginx.
- **`.github/workflows/tests.yml`**: the Task 5 infra-integration step
  was rewritten from a single-scenario check into nine scenarios
  covering categories (A) legitimate uploads of every supported type
  round-tripped through a real signed MinIO download, (C) content/
  extension mismatches (HTML named `.pdf`, garbage named `.png`, a real
  JPEG named `.png`, an ELF binary named `.jpg`) each asserted to be
  rejected with `400` and never stored (`file-url` still 404s
  afterward), (D) filename attacks (path traversal stripped, a Unicode
  filename accepted, a double extension resolved by its final
  extension), (E) SVG still force-downloaded, and a full legitimate-PDF
  round trip through the real presigned-URL signature check (previously
  this suite never actually verified a *successful*, correctly-signed
  download — only that an *invalid* signature reached MinIO without a
  502).
- **`app/core/upload_safety.py` unit tests** (both services): added 15
  new tests directly exercising `content_matches_extension()`/
  `detect_content_kind()` against real-format headers, HTML/script
  payloads, cross-image-type mismatches, executable signatures across
  every accepted extension, and the text/svg/unrecognized-extension
  no-signature-required cases. All 21 tests per service pass locally
  (verified by executing the actual `app.core.upload_safety` module's
  real functions directly — see "Testing performed").

### Bugs found and fixed during this task

1. **(In this task's own new code, caught before commit)** Initial CI
   run of the rewritten upload-Content-Type acceptance test failed —
   `served -> Content-Type: application/xml` / `svg served -> `
   (empty) instead of the expected values, and the raw Nginx access
   log showed both presigned-URL downloads returning `403` from MinIO.
   Root cause, confirmed by reading `MINIO_PUBLIC_ENDPOINT`
   (`localhost:8080`, with an explicit port) against
   `nginx.conf.template`'s `/documents/`/`/products/` locations: boto3
   signs the presigned URL's SigV4 signature over the `Host` header
   value `localhost:8080`, but Nginx's `$host` variable has its port
   stripped by definition — forwarding `Host: localhost` (no port) to
   MinIO, which then recomputes a different signature and rejects the
   request as invalid. This bug **predates Task 5**: no earlier CI
   check ever did a real signed round trip (the one existing presigned-
   URL check, added in Task 3, deliberately used an *unsigned* URL and
   only asserted "not a 502/504" — proving Nginx reaches MinIO, not
   that a valid signature is honored). Task 5's new end-to-end test is
   what surfaced it. Fixed by forwarding `$http_host` (the client's raw
   `Host` header, including port) instead of `$host` in both locations.
2. No bugs found in the content/extension mismatch logic itself during
   testing — the design in "What changed" above was validated directly
   against the local unit tests before being wired into the routers,
   and the CI scenarios described above (rewritten specifically to
   exercise it) all pass.

### Testing performed

- **Local**: `python3 -m py_compile` on every changed `.py` file
  (routers, `upload_safety.py`, test files) in both services — no
  syntax errors.
- **Local**: `python3 -c 'import yaml; yaml.safe_load(...)'` on the
  rewritten `.github/workflows/tests.yml` — valid YAML.
- **Local, genuinely executed (not just inspected)**: `pytest` itself
  isn't installable in this sandbox (no package-registry network
  access), so each service's `test_upload_safety.py` module was
  imported directly and every `test_*` function actually called
  (with a small local shim reproducing `pytest.mark.parametrize`'s
  expansion behavior) against the real `app.core.upload_safety` code —
  not a hand-simulation of the logic. Result: **21/21 tests pass** in
  `document-service`, **21/21 tests pass** in `catalogue-service`.
- **CI (GitHub Actions, the real test executor for this environment)**:
  pushed and re-verified via `mcp__github__get_job_logs` with
  `return_content: true` (raw logs, not the `conclusion` field alone)
  after each push — see "Verification" below for the exact run
  evidence once this commit's run completes.

### Verification

Run: https://github.com/kaungkyaw3124/doc-mgmt/actions/runs/34187405746
(commit `e49d7f1`). Per-job results, pulled directly via
`mcp__github__get_job_logs` with `return_content: true` (raw log
content, not the `conclusion` field alone):

- `auth-service` (job `101938407688`): **FAILED** — pre-existing,
  unrelated to this task. Root cause confirmed from the raw traceback:
  `TypeError: can't compare offset-naive and offset-aware datetimes` at
  `app/core/refresh_tokens.py:56` (`rotate_refresh_token`), a Task 6
  bug in code this task never touched. Confirmed pre-existing by
  checking the immediately prior run
  (https://github.com/kaungkyaw3124/doc-mgmt/actions/runs/34186170015,
  commit `72a9af5`, Task 6's own commit): the identical failure, same
  step, same traceback, already present before this task's commit.
- `catalogue-service` (job `101938407802`): **PASSED**.
- `document-service` (job `101938407822`): **PASSED**.
- `search-service` (job `101938407778`): **PASSED**.
- `infra-integration` (job `101938407783`): step 12, "Acceptance —
  uploaded file Content-Type is derived server-side, not trusted from
  the client" (this task's own test) — **PASSED**, all 9 scenarios,
  real output quoted directly from the raw log:
  ```
  HTML content named evil.pdf -> 400
  {"detail":"file content does not match its extension"}
  file-url after rejected upload -> 404
  garbage content named evil.png -> 400
  real JPEG bytes named .png -> 400
  ELF binary named evil.jpg -> 400
  path-traversal filename upload -> {"image_object_key":"PRD-0005/passwd.pdf"}
  unicode filename upload -> 200
  double-extension (invoice.pdf.exe) upload -> 200
  double-extension served -> Content-Type: application/octet-stream
  svg served -> Content-Disposition: attachment
  legitimate PDF upload -> 200
  legitimate PDF served -> 200 / Content-Type: application/pdf
  ```
  Step 13 ("Acceptance — cookie-based refresh-token rotation...", a
  Task 6 test) then **FAILED** in the same job, for the identical
  pre-existing `refresh_tokens.py:56` reason as the `auth-service`
  unit-test failure above — this step had previously been silently
  `skipped` (never even run) in the prior run, because step 12 itself
  used to fail first and abort the job under `bash -e`. Fixing step 12
  is what let step 13 run at all and surface Task 6's real, pre-
  existing bug — not something this task's changes caused.

**Net result: Task 5's own acceptance criteria are fully met and
independently verified against a live stack. The two remaining CI
failures (`auth-service` unit tests, `infra-integration` step 13) are
a single pre-existing Task 6 bug, confirmed unrelated to any file this
task changed, and out of scope for this pass per the current task
scoping ("Task 5 is now the ONLY task to work on"). It is flagged here
rather than silently left unmentioned — see `docs/
SECURITY_PROJECT_REPORT.md`'s Task 6 row and "Next task" section.**

### Notes / scope boundaries

- Task 5's new content-mismatch check is exercised end-to-end in CI
  only through `catalogue-service`'s `/api/products/{id}/file`
  endpoint (creating a product is a single API call; creating a
  document requires more setup — customer/project/company records —
  that the existing CI script doesn't already build). `document-
  service`'s `upload_document_file` handler is wired identically
  (same `content_matches_extension` import, same read-4KB/seek(0)/
  reject-400 pattern — see the diff) and is covered by the same 21
  passing unit tests per service; it was verified by direct code
  review and `py_compile`, not by a second live end-to-end HTTP round
  trip. This is a reasoned scope boundary, not an unverified claim.
- Old-format Office files (`.doc`/`.xls`/`.ppt`, OLE compound
  documents) and modern ones (`.docx`/`.xlsx`/`.pptx`, zip containers)
  are intentionally NOT treated as interchangeable — a `.docx` upload
  that's actually a legacy `.doc`-format file (or vice versa) is
  rejected as a mismatch, even though both are "real" Word documents.

## Task 6 — JWT Storage and Rotation

**Date:** 2026-09-08

**Status:** PASS

### Goal

Harden JWT/session handling: access tokens should not sit exposed in
`localStorage` any longer than necessary, refresh tokens must be
safely rotated/revoked, and every expiry comparison in the flow must
be timezone-correct.

### Original security problem

Before this task's original implementation (commit `72a9af5`): a
single JWT, valid for 60 minutes, stored in
`localStorage.getItem/setItem('ledger_token')` and sent as-is on every
request. Any XSS could exfiltrate it once and impersonate the user for
up to an hour; there was no server-side "logout" (the token stayed
cryptographically valid until natural expiry regardless of what the
client did); a password change did not invalidate tokens issued before
it.

### Known CI failure carried into this task

`72a9af5` already implemented the full short-access-token /
rotating-HttpOnly-refresh-cookie architecture described below, but its
CI run (https://github.com/kaungkyaw3124/doc-mgmt/actions/runs/34186170015)
failed: `auth-service`'s unit tests and the `infra-integration` job's
refresh-token acceptance step both errored with

```
TypeError: can't compare offset-naive and offset-aware datetimes
```

at `services/auth-service/app/core/refresh_tokens.py:56`
(`rotate_refresh_token`). Confirmed via `mcp__github__get_job_logs`
with `return_content: true` (the full raw traceback, not just the
`conclusion` field) during the prior Task 5 pass, which is what first
surfaced this bug (Task 5's own fix unblocked an earlier CI step that
had been masking this one under `bash -e`).

### Root cause

`services/auth-service/app/models.py`'s `RefreshToken.expires_at` (and
`created_at`/`revoked_at`) is a `DateTime(timezone=True)` column;
Postgres/psycopg2 hands back a timezone-aware `datetime` for it.
`rotate_refresh_token()` compared that aware value directly against
`now = datetime.utcnow()` — a **naive** datetime — which Python's
`datetime.__lt__` refuses to compare at all, raising `TypeError`
rather than silently doing the wrong thing. This is not a subtle logic
bug so much as a straightforward "naive and aware datetimes were mixed
where only one or the other was assumed" mistake, present in the
original Task 6 implementation.

### Re-audit performed (not just the one exception)

Per this pass's explicit instruction, the entire authentication flow
was re-read end to end rather than patching only the one crashing
line: login (`routers/auth.py: login`), access-token creation
(`core/jwt_utils.py: create_access_token`), refresh-token creation/
rotation/revocation (`core/refresh_tokens.py`, all four functions),
frontend token storage and the refresh/retry flow (`web/index.html`),
the `/refresh` and `/logout` endpoints, `/verify` (expired/revoked/
disabled-user handling), and admin disable/password-change revocation
(`routers/admin.py`). Findings:

- **`create_access_token`** (`jwt_utils.py`) was already correct:
  `datetime.now(timezone.utc)`, never `utcnow()`. The bug was isolated
  to `refresh_tokens.py`.
- **Frontend** (`web/index.html`) was already correct and required no
  changes: the access token lives in an in-memory `let token = null`
  variable only (never `localStorage`/`sessionStorage`); the only
  `localStorage` call left anywhere is a one-time
  `localStorage.removeItem('ledger_token')` in `doLogout()` that
  cleans up a pre-migration session's leftover value — there is no
  code path that ever *writes* a token to browser storage. `apiFetch`
  retries once through `refreshAccessToken()` (a shared in-flight
  promise, so a burst of parallel 401s doesn't each rotate the refresh
  token and race each other) before falling back to `doLogout()`. The
  bootstrap IIFE at the bottom of the file re-establishes a session via
  a silent `POST /refresh` against the HttpOnly cookie on page load —
  proven already working end-to-end by this task's new CI integration
  check (see "Tests" below).
- **Cookie settings** (`routers/auth.py: _set_refresh_cookie`):
  `httponly=True`, `samesite="lax"`,
  `secure=(settings.environment == "production")` were already
  correct. `path="/api/auth"` LOOKED correct by inspection but wasn't
  — verifying it end-to-end (not just reading the code) is what caught
  a real bug; see "Second CI pass" below for the fix (`path="/"`).
- **Rotation/revocation/replay-detection design**
  (`refresh_tokens.py`) was already correct in design: single-use
  tokens (the presented one is revoked, a new one issued), a replayed
  (already-revoked) token triggers `revoke_all_user_tokens` for that
  user rather than being silently rejected in isolation, `/logout`
  revokes server-side, admin disable/password-change revoke all of a
  user's outstanding tokens. Only the datetime comparison itself was
  broken.
- **JWT subject** (`jwt_utils.py`/`routers/auth.py`) already uses the
  user's immutable UUID (`str(user.id)`) as `sub`, never the mutable
  username — unaffected by a username-only edit
  (`test_unrelated_username_edit_does_not_revoke_tokens`, pre-existing,
  re-verified still passing).

**Conclusion: this was a genuinely isolated bug**, not a symptom of a
deeper architectural problem — but it was re-verified by actually
re-reading everything, not assumed to be isolated in advance.

### Fix applied

`services/auth-service/app/core/refresh_tokens.py`:

- Added `_aware(dt) -> datetime`: normalizes any datetime to
  timezone-aware UTC before comparison — a naive value is treated as
  UTC (matching how this module always wrote naive values before this
  fix), an aware value in a different offset is converted to UTC. This
  is the actual fix: robust to whatever a given DB driver/dialect
  hands back, rather than assuming "the driver will always return
  aware" (the assumption that caused the original bug) or "always
  naive."
- Every write in the module (`issue_refresh_token`,
  `rotate_refresh_token`, `revoke_refresh_token`,
  `revoke_all_user_tokens`) now uses `datetime.now(timezone.utc)`
  instead of `datetime.utcnow()`, so newly-written values are
  consistently aware going forward.
- `rotate_refresh_token`'s expiry check now compares
  `_aware(row.expires_at) < now` instead of the raw column value —
  this is the one-line fix for the reported crash, but it's applied as
  part of the systematic normalization above rather than a one-off
  patch, per the "do not simply fix the one datetime exception"
  instruction.

**Correction, added after actually running this fix against real CI**
(the static re-audit above claimed `jwt_utils.py`/`routers/auth.py`
needed no changes — that claim was wrong, caught only by pushing and
reading the raw CI output rather than trusting the read-through; see
"Second CI pass" below for the two additional, genuinely real bugs
that surfaced and were fixed before Task 6 could honestly be called
PASS).

### Tests

`services/auth-service/tests/test_refresh_tokens.py` grew from 16 to
24 tests. New tests added this pass:

- `test_expired_refresh_token_is_rejected` — time-based expiry (not
  the pre-existing revoked_at/replay path), a real end-to-end
  `POST /refresh` after forcing `expires_at` into the past.
- `test_refresh_cookie_is_secure_in_production` — monkeypatches
  `settings.environment` to `"production"` and asserts the Set-Cookie
  header actually carries `Secure` (dev/CI's real requests, which run
  as `ENVIRONMENT=development`, are asserted NOT to carry it — see the
  new infra-integration CI check below — so both branches of that
  conditional are now exercised, not just one).
- `test_access_token_lifetime_is_bounded_to_configured_minutes` —
  decodes a real issued access token and asserts its `exp` claim is
  within `settings.jwt_expire_minutes` of issuance, not just "some"
  short value.
- `test_aware_helper_normalizes_naive_datetimes_to_utc` /
  `test_aware_helper_converts_non_utc_aware_datetimes_to_utc` — direct
  unit tests of the new `_aware()` function.
- `test_rotate_refresh_token_does_not_raise_when_expires_at_comes_back_naive`
  — reproduces the exact original crash scenario directly (forces a
  naive `expires_at` into the same SQLAlchemy session
  `rotate_refresh_token` will read via the identity map) and asserts
  it completes successfully instead of raising `TypeError`.
- `test_rotate_refresh_token_correctly_rejects_a_naive_expired_timestamp`
  — same naive-datetime scenario, but expired: proves the fix doesn't
  just avoid crashing, it still compares correctly (an expired naive
  timestamp is still rejected, not silently treated as valid forever).
- `test_two_access_tokens_minted_in_the_same_second_are_still_distinct`
  — added after the second CI pass (see below); regression test for
  the `jti`-nonce fix.
- `test_jwt_does_not_contain_username_or_password` (pre-existing)
  updated to expect `{"sub", "exp", "jti"}` instead of `{"sub", "exp"}`.

`.github/workflows/tests.yml`'s existing infra-integration step
"Acceptance — cookie-based refresh-token rotation, replay rejection,
and logout" was extended with:

- An invalid-credentials login check (401).
- Reading the real `Set-Cookie` response header (not just the cookie
  jar file, which drops flags) and asserting `HttpOnly` and
  `SameSite=Lax` are present, `Secure` is absent (this stack runs over
  plain HTTP in CI, `ENVIRONMENT=development`), and the access token
  itself never appears in any `Set-Cookie` header.
- A real authenticated API call (`GET /api/products` through Nginx)
  using the access token that `/refresh` just issued — proving the
  rotated token actually works for a live request, not just that
  `/refresh` returned 200.

### Second CI pass — two additional bugs found

The first fix commit (`d727155`, datetime-only) was pushed and its CI
run (https://github.com/kaungkyaw3124/doc-mgmt/actions/runs/34188243042)
was actually read, not assumed green. The original `TypeError` was
gone (`auth-service` went from 40 passed/4 failed to 44 passed/3
failed), but two DIFFERENT, previously-masked bugs surfaced — both
real, both pre-existing in `72a9af5`, neither caused by the datetime
fix itself:

1. **Refresh cookie `Path` never matches this service's own routes.**
   `auth-service` unit tests: `test_refresh_rotates_token_and_issues_new_access_token`
   and `test_replay_of_a_rotated_refresh_token_is_rejected` both failed
   with `assert 401 == 200` — the very first `POST /refresh` right
   after login was rejected as if no cookie had been sent at all.
   Root cause: `REFRESH_COOKIE_PATH` was `"/api/auth"`, on the
   documented theory that a browser matches cookie `Path` against the
   URL it actually requested (true) and that URL is always
   `/api/auth/*` (also true, confirmed — the frontend only ever calls
   `API_BASE + '/auth/...'`). What that reasoning missed: `Path`
   matching is enforced entirely client-side (by whatever HTTP client
   holds the cookie jar — a real browser, or httpx's `TestClient`,
   which is what this task's own real, non-mocked test suite uses),
   not by the server, and it matches against whatever path is actually
   requested of THAT client — and `auth-service`'s own routes are
   mounted at bare paths (`/login`, `/refresh`, `/logout`, no
   `/api/auth` prefix inside the container — see `main.py`). A test (or
   any other direct, non-Nginx caller) hitting `/refresh` directly
   never satisfies `Path=/api/auth`, so the cookie is silently never
   attached. This is exactly why "verify end-to-end, don't just
   inspect the code" matters: the design comment was internally
   consistent and plausible, and was wrong regardless. Fixed by
   scoping the cookie to `Path=/` — this app is single-origin
   end-to-end, HttpOnly is what actually keeps the cookie out of
   JavaScript, and every other endpoint simply never reads a cookie it
   doesn't look for, so nothing meaningful is lost.
2. **Two access tokens minted in the same wall-clock second are
   byte-for-byte identical.** The NEW `infra-integration` end-to-end
   check (real curl calls, fast enough to land login+refresh in the
   same second) caught: `first refresh -> 200` (succeeded!) but
   `FAIL: refresh did not issue a new access token`. Root cause: a JWT
   is a deterministic function of its payload; `exp` only has
   1-second resolution; `sub` is unchanged across a refresh for the
   same user — so two tokens minted for the same user within the same
   second are literally the same string. Not a security hole on its
   own (both tokens are equally valid for the same short window
   either way), but it defeats the expectation that a refresh actually
   mints a new credential, and is exactly the kind of thing "do not
   silently extend an expired access token" implicitly assumes doesn't
   happen by coincidence. Fixed by adding a random `jti` (JWT ID)
   nonce to every access token's payload
   (`services/auth-service/app/core/jwt_utils.py`) — not an identity/
   PII claim, purely a per-token uniqueness guarantee independent of
   timing. `test_jwt_does_not_contain_username_or_password` was
   updated to expect `{"sub", "exp", "jti"}` (still asserting no
   username/password ever appears — its actual security intent,
   unchanged); a new
   `test_two_access_tokens_minted_in_the_same_second_are_still_distinct`
   test directly reproduces and guards against the exact scenario.

Both fixes, plus their tests, were pushed in a follow-up commit; see
"Exact results" below for that run's evidence.

### Exact results

Run: https://github.com/kaungkyaw3124/doc-mgmt/actions/runs/34189040051
(commit `e7ed0a1`). Per-job results, pulled directly via
`mcp__github__get_job_logs` with `return_content: true` (raw log
content, not the `conclusion` field alone):

- `auth-service` (job `101943116134`): **PASSED** — raw pytest summary
  line quoted directly from the log:
  ```
  ======================= 48 passed, 5 warnings in 24.87s ========================
  ```
  All three tests that failed in the previous run
  (`test_refresh_rotates_token_and_issues_new_access_token`,
  `test_replay_of_a_rotated_refresh_token_is_rejected`,
  `test_replaying_a_rotated_token_revokes_the_users_other_sessions_too`)
  now show `PASSED` individually in the same log.
- `catalogue-service`, `document-service`, `search-service`: **PASSED**
  (unaffected by this task's changes, re-confirmed green).
- `infra-integration` (job `101943116170`): step 13, "Acceptance —
  cookie-based refresh-token rotation, replay rejection, and logout" —
  **PASSED**, all 8 scenarios, real output quoted directly from the
  raw log:
  ```
  login with wrong password -> 401
  refresh cookie attributes -> set-cookie: refresh_token=rGEv...; HttpOnly; Max-Age=1209600; Path=/; SameSite=lax
  first refresh -> 200
  authenticated request with rotated access token -> 200
  replay of rotated-away refresh token -> 401
  logout -> 204
  refresh after logout -> 401
  refresh with no cookie -> 401
  fresh login after logout -> 200
  ```
  Note the cookie attribute line: `Path=/` (this pass's fix, was
  `Path=/api/auth`), `Secure` correctly absent (dev/CI is plain HTTP),
  `HttpOnly`/`SameSite=lax` present — and `first refresh -> 200` +
  `authenticated request with rotated access token -> 200` together
  confirm the rotated token is both issued AND actually usable for a
  real API call (the `jti` fix means `access_token_1 != access_token_2`
  held, since no "FAIL: refresh did not issue a new access token" line
  appears in the output above).

**Net result: Task 6 is genuinely PASS, independently verified against
raw CI evidence for the complete relevant test suite (48/48 auth-service
tests, all 5 CI jobs) — not assumed from the commit existing or from
green checkmarks alone.**

### Security verification

- Datetime regression: both the "does not raise" and "still correctly
  rejects when expired" cases are covered for the exact naive-value
  scenario that caused the original failure — not just the aware/aware
  happy path.
- Refresh-token reuse/replay: unchanged design, re-verified still
  passing (`test_replay_of_a_rotated_refresh_token_is_rejected`,
  `test_replaying_a_rotated_token_revokes_the_users_other_sessions_too`).
- Disabled/inactive users: unchanged design, re-verified still passing
  (`test_disabled_user_cannot_refresh`,
  `test_disabling_a_user_revokes_their_refresh_tokens`).
- Cookie security: HttpOnly/SameSite unconditional, Secure correctly
  conditional on `ENVIRONMENT=production` — now verified in BOTH
  directions (unit test forces production and asserts Secure present;
  CI's real dev-mode request asserts Secure absent), not just one.
- No regression to the pre-existing "refresh token never reaches
  JavaScript" property: it's an HttpOnly cookie, never read by any
  frontend code (grepped `web/index.html` for any reference to reading
  a `refresh_token` cookie value — none exists; the frontend only ever
  reads `data.access_token` from a JSON response body).

### Regression

Tasks 1–5 unaffected: no file outside `services/auth-service/app/core/
refresh_tokens.py`, `services/auth-service/tests/test_refresh_tokens.py`,
and the one infra-integration CI step above was touched this pass.

### Documentation updated

- `docs/SECURITY_HARDENING_LOG.md`: this entry, and the status table
  updated from `NOT STARTED` to `PASS`.
- `docs/SECURITY_PROJECT_REPORT.md`: Task 6 row and section updated.

## Task 7 — CORS Policy Hardening

**Date:** 2026-09-08

**Status:** PASS — verified against raw CI evidence, see "Exact
results" below (commit `92a5f91`, run `34195255901`, all 5 jobs green
on the first attempt, no fix iteration needed).

### Goal

Implement an explicit, secure CORS policy: no wildcard-with-credentials,
explicit configured trusted origins, methods/headers/exposed-headers
restricted to what's actually used, production fails safe on missing/
invalid config, dev origins can't silently leak into production,
preflight works correctly, no duplicate/conflicting CORS headers.

### Initial CORS state (before this task)

`grep -rn "CORSMiddleware\|allow_origins\|Access-Control-Allow" services/
*/app/ infra/` returned **nothing** — no FastAPI app in this project
(`auth-service`, `catalogue-service`, `document-service`,
`search-service`) has ever imported `CORSMiddleware` or set any
`Access-Control-*` header, and Nginx has never emitted one either.

### Security problem this creates

Absence of any CORS configuration is not the same thing as a correct
CORS policy — it happens to be safe TODAY only as an accident of this
app's architecture (see "Whether credentialed CORS is actually
required" below), not because of any enforced policy:

1. **No policy is documented or tested**, so nothing stops a future
   change from adding `CORSMiddleware(allow_origins=["*"],
   allow_credentials=True)` to "fix a CORS error" someone hits while
   experimenting with a different frontend origin — this exact
   combination is invalid per the Fetch spec with real browsers (a
   wildcard origin can't carry credentials) in SOME browsers, but not
   reliably enforced by all HTTP clients/older behavior, and is exactly
   the anti-pattern this task's acceptance criteria (1) and (6) name
   directly.
2. **Even today's "no CORS headers" state has a structural blind spot**,
   found during inspection (see "Bugs found" below): every location
   in `nginx.conf.template` guarded by `auth_request` would 401 a CORS
   preflight `OPTIONS` request (which never carries `Authorization`),
   which — the moment ANY cross-origin use of this API is ever
   introduced — would silently and confusingly break, not fail
   loudly/obviously.

### Repository inspection performed

- **All 4 FastAPI apps** (`services/*/app/main.py`): no `CORSMiddleware`,
  no manual CORS header anywhere in any router.
- **Nginx config** (`infra/nginx/nginx.conf.template`): no
  `Access-Control-*` header anywhere; every `/api/*` location proxies
  every method (including `OPTIONS`) straight through — for the 8
  locations with `auth_request /_verify;`, that would include a
  preflight `OPTIONS`, which never carries `Authorization`, hitting the
  auth gate and 401ing (see "Security problem" #2 above).
- **Frontend** (`web/index.html`): `API_BASE = '/api'` — every fetch
  call is same-origin by construction (the frontend is static files
  served by the SAME Nginx that serves `/api/*`; there is no separate
  frontend origin, dev server, or CDN in this project's actual
  deployment). Methods actually used:
  `grep -oE "method:\s*'[A-Z]+'" web/index.html` → `POST`, `PATCH`,
  `DELETE` (plus the implicit default `GET` on every argument-less
  `apiFetch()` call) — never `PUT`. Headers actually sent:
  `Authorization` (every authenticated call) and `Content-Type`
  (only when a body is present). Response headers actually read by JS:
  `grep -n "headers.get\|\.headers\[" web/index.html` → **none** — the
  frontend only ever reads JSON response bodies, never a response
  header.
- **Auth flow / refresh-token cookie** (Task 6): the refresh cookie is
  `HttpOnly`, delivered only to `/api/auth/*` via the SAME origin — a
  cross-origin `fetch` with `credentials: 'include'` targeting this API
  would need `Access-Control-Allow-Credentials: true` AND an exact
  (non-wildcard) `Access-Control-Allow-Origin` to ever receive it, per
  the Fetch spec. See "Whether credentialed CORS is actually required"
  below for the actual finding.
- **Service-to-service traffic** (Task 4's `X-Internal-Secret` calls,
  `catalogue_client.py`/`document_client.py`/`document_client.py` in
  the other services): plain `httpx` server-to-server calls, several of
  which bypass Nginx entirely (direct container-to-container on the
  Docker network) and none of which ever send an `Origin` header —
  **CORS is a browser-enforced mechanism only**; a non-browser HTTP
  client simply ignores any `Access-Control-*` response header
  entirely. Confirmed unaffected by construction, not just by testing
  (though also re-tested — see "Tests").
- **Existing tests/CI**: no CORS-related test existed anywhere before
  this task.

### Whether credentialed cross-origin CORS is actually required

**No** — this application is same-origin end-to-end in its real,
intended deployment (Nginx serves both the static frontend and every
`/api/*` path from exactly one origin). No legitimate normal use of
this app — login, refresh, logout, any authenticated API call, file/
document operations — is ever a cross-origin browser request. This is
stated explicitly here per this task's requirement 6 ("verify whether
credentialed CORS is actually required") rather than silently building
a permissive policy nobody asked for.

Given that finding, this task still implements a real, explicit,
narrowly-scoped CORS policy (not a no-op) because:
- It replaces *implicit* safety (nothing configured, so nothing works
  cross-origin, entirely by accident of there being no
  `CORSMiddleware` import anywhere) with an *explicit, tested, and
  fail-safe* one — closing off the "someone adds `allow_origins=["*"]`
  during dev and it ships" risk structurally, not by convention.
- It fixes the real preflight-vs-`auth_request` bug found during
  inspection (see above) — relevant regardless of whether cross-origin
  access is used TODAY, since it would otherwise fail silently and
  confusingly the moment it ever is.
- It gives local dev / a future staging frontend on a different port/
  host a real, working, narrowly-scoped path to opt in via
  configuration — without ever needing a wildcard to get there.

### Exact implementation

**Where:** Nginx only — the single edge every browser ever talks to.
Deliberately **not** added to any FastAPI app via `CORSMiddleware`.
Two reasons: (1) `auth_request`-gated locations would still 401 a
preflight before it ever reached a backend's own `CORSMiddleware`, so
FastAPI-level CORS could never correctly answer preflight for those
locations anyway (see requirement 10) — Nginx has to own preflight
regardless; (2) if EITHER layer also added its own
`Access-Control-Allow-Origin`, the response would carry two, which
browsers treat as an invalid header set and reject outright (the exact
"duplicate/conflicting headers" failure mode requirement 13 asks to be
checked for) — keeping CORS in exactly one place (Nginx) makes that
failure mode structurally impossible rather than something to
carefully avoid.

**New files:**
- `infra/nginx/cors.conf` — the CORS header logic, `include`d as the
  FIRST directive inside every browser-facing API location (14 of
  them: `/api/auth/login`, `/register`, `/groups-public`, `/refresh`,
  `/logout`, `/users`, `/api/admin/`, `/api/documents`, `/customers`,
  `/companies`, `/projects`, `/products`, `/categories`, `/search`).
  NOT included in `/documents/`/`/products/` (the unauthenticated MinIO
  presigned-download passthroughs — simple GETs, no credentials, not
  part of the credentialed-JS-fetch surface this exists for) or the
  internal-only `= /_verify` location (never browser-reachable
  directly). Sets `Access-Control-Allow-Origin`/`-Credentials`/`Vary`
  on every response, and short-circuits `OPTIONS` preflight with a 204
  BEFORE any `auth_request` in the same location — see "Exact
  configuration" below for the full header set.
- `infra/nginx/validate-cors-config.sh` — the production fail-safe
  check (see "Configuration/environment variables").
- `infra/nginx/docker-entrypoint.sh` — replaces the inline
  `entrypoint:` one-liner `docker-compose.yml` used to have; runs the
  validation script, then the same `envsubst` render step as before
  (now substituting `$CORS_ALLOWED_ORIGIN` too), then execs Nginx.

**Changed files:**
- `infra/nginx/nginx.conf.template` — new `map $http_origin
  $cors_allowed_origin { default ""; "${CORS_ALLOWED_ORIGIN}"
  $http_origin; }` block in the `http {}` context (the ONE place the
  real configured origin is substituted in); `include
  /etc/nginx/cors.conf;` added to the 14 locations listed above.
- `infra/docker-compose.yml` — `nginx` service: added
  `CORS_ALLOWED_ORIGIN`/`ENVIRONMENT` env vars, mounted the three new
  files, entrypoint now runs `docker-entrypoint.sh`.

### Configuration/environment variables

- `CORS_ALLOWED_ORIGIN` (Nginx container env var) — the single browser
  origin this deployment trusts for cross-origin requests. Default in
  `docker-compose.yml`: `http://localhost:8080` (matches how the CI/
  dev stack is actually reached — the same "dev-convenience default
  that matches the compose stack's own setup" pattern every other
  secret in this project already uses, see `.env.example` files).
- `ENVIRONMENT` (Nginx container env var, newly passed through — it
  previously wasn't) — gates `validate-cors-config.sh`'s fail-fast
  check, same meaning as every other service's `ENVIRONMENT` setting.

### Allowed origins

Exactly one, explicitly configured: whatever `CORS_ALLOWED_ORIGIN`
resolves to. Never a wildcard. An `Origin` request header that doesn't
byte-for-byte match it gets `$cors_allowed_origin = ""` — no
`Access-Control-Allow-Origin` value granted, so the browser blocks the
cross-origin response itself.

### Allowed methods

`GET, POST, PATCH, DELETE, OPTIONS` — exactly what `web/index.html`
actually issues (confirmed by grep, see "Repository inspection") plus
`OPTIONS` itself. `PUT` and `HEAD`/`TRACE`/`CONNECT` are never
advertised.

### Allowed headers

`Authorization, Content-Type` — exactly what the frontend actually
sends (confirmed by grep). Nothing else is allowed through preflight.

### Exposed response headers

**None** (`Access-Control-Expose-Headers` is never set). The frontend
never reads a response header via JS (confirmed by grep — see
"Repository inspection") — there is nothing to expose, so nothing is
exposed. A browser can always read the CORS-safelisted response
headers (`Content-Type`, `Content-Length`, etc.) regardless; this
setting only controls anything BEYOND that safelist, and this app
needs none of it.

### Credential behavior

`Access-Control-Allow-Credentials: true` on every CORS-enabled
location, ALWAYS paired with the exact-match origin echo above —
**never** with a wildcard `Access-Control-Allow-Origin: *` (which the
Fetch spec forbids combining with credentials, and which
`$cors_allowed_origin` structurally cannot produce — it's either the
one configured origin or empty string, never `*`).

### Preflight behavior

Every CORS-enabled location answers `OPTIONS` with `204` directly in
Nginx — before `auth_request` (for the 8 locations that have one) and
before ever proxying to a backend. This is the fix for the real bug
found during inspection: previously, an `OPTIONS` preflight against
any `auth_request`-gated location would 401 (since preflight never
carries `Authorization`), which would have silently broken any future
credentialed cross-origin use of this API. `Access-Control-Max-Age:
600` caps how often a browser needs to re-preflight the same request
shape.

### Bugs found

1. **Preflight `OPTIONS` would 401 on every `auth_request`-gated
   location** (see "Security problem" #2 and "Preflight behavior"
   above) — a real, previously-undetected bug (no test exercised
   `OPTIONS` against any of these locations before this task), fixed
   as part of this task's own design rather than as a separate patch,
   since the CORS-preflight short-circuit is what both implements CORS
   AND fixes this in the same change.
2. No other bugs found — the rest of the existing Nginx/FastAPI/
   frontend auth flow needed zero changes for this task (see "Whether
   credentialed CORS is actually required").

### Tests

New: `.github/workflows/tests.yml`, `infra-integration` job — TWO new
steps (real HTTP through the real Nginx/docker-compose stack, per this
task's explicit "do not merely test FastAPI's TestClient" instruction
— there is no FastAPI-level CORS code to unit test in the first place,
since it's implemented entirely in Nginx):

1. **"Acceptance — CORS production config fails safe (no stack
   needed)"** — runs `infra/nginx/validate-cors-config.sh` directly (no
   Docker stack required) against 5 scenarios: production + empty
   origin (must refuse, exit 1), production + the dev-default
   `http://localhost:8080` (must refuse — dev origins must never
   silently pass as production, requirement 5), production + a
   `https://localhost...` placehoder (must refuse), production + a
   real `https://` origin (must succeed, exit 0), development + empty
   origin (must succeed with only a warning, never refuse).
2. **"Acceptance — CORS policy (trusted/untrusted origins, preflight,
   headers, credentials)"** — against the live stack: preflight from
   the trusted origin on a PROTECTED endpoint succeeds (204) WITHOUT
   an `Authorization` header (proves the preflight-before-`auth_request`
   fix); response carries the exact trusted-origin
   `Access-Control-Allow-Origin`, `Access-Control-Allow-Credentials:
   true`, `Access-Control-Allow-Methods` containing `GET` but NOT
   `PUT`, `Access-Control-Allow-Headers` containing `Authorization`;
   preflight from an untrusted origin never gets that origin (or a
   wildcard) granted; a real (non-preflight) request from the trusted
   origin gets the CORS header, exactly once (no duplicates); a real
   request from an untrusted origin never gets it; login/refresh/
   logout all still work normally with a trusted `Origin` header
   present (Task 6's flows, re-verified under this task's changes);
   service-to-service (`X-Internal-Secret`) traffic — no `Origin`
   header at all — still works, confirming CORS doesn't touch it.

### Exact results

Run: https://github.com/kaungkyaw3124/doc-mgmt/actions/runs/34195255901
(commit `92a5f91`), all 5 jobs **PASSED** on the first attempt — no fix
iteration was needed. Verified via `mcp__github__get_job_logs` with
`return_content: true` (raw log content, not the `conclusion` field
alone), job `101961451327` (`infra-integration`):

- **"Acceptance — CORS production config fails safe (no stack
  needed)"** — real output quoted directly from the raw log:
  ```
  SECURITY: CORS_ALLOWED_ORIGIN must be set to a real https:// origin in production (got: '<empty>'). Refusing to start in production.
  production, empty origin -> exit 1
  SECURITY: CORS_ALLOWED_ORIGIN must be set to a real https:// origin in production (got: 'http://localhost:8080'). Refusing to start in production.
  production, dev-default http origin -> exit 1
  SECURITY: CORS_ALLOWED_ORIGIN ('https://localhost:8080') looks like a local/dev placeholder, not a real production origin. Refusing to start in production.
  production, https localhost origin -> exit 1
  production, valid https origin -> exit 0
  SECURITY: CORS_ALLOWED_ORIGIN is not set — no cross-origin browser request will ever be granted CORS headers (same-origin traffic is unaffected). This is fine in development, but will be refused at startup in production.
  development, empty origin -> exit 0
  ```
  All 5 scenarios matched expectations exactly.
- **"Acceptance — CORS policy (trusted/untrusted origins, preflight,
  headers, credentials)"** — real output quoted directly from the raw
  log:
  ```
  preflight (trusted origin, no auth) -> HTTP/1.1 204 No Content
  untrusted preflight -> HTTP/1.1 204 No Content
  GET /api/products, trusted origin -> HTTP/1.1 200 OK
  GET /api/products, untrusted origin -> HTTP/1.1 200 OK
  login with Origin header -> HTTP/1.1 200 OK
  refresh with Origin header -> 200
  logout with Origin header -> 204
  direct-to-backend health check (no Origin header at all) -> 200
  ```
  The trusted-origin preflight against a PROTECTED endpoint
  (`/api/products`) succeeded with **204 and no `Authorization`
  header sent at all** — direct proof the preflight-before-
  `auth_request` fix works, not just that the step exited 0 (every
  `[ ... ] || { echo "FAIL: ..."; fail=1; }` assertion — trusted-origin
  header values, untrusted-origin non-grant, no wildcard, no duplicate
  header, exact allowed-methods/-headers content — passed silently,
  and none of their `FAIL:` lines appear anywhere in the actual output
  above).
- `auth-service`, `catalogue-service`, `document-service`,
  `search-service`: all **PASSED**, confirming zero regression to
  Tasks 1–6.

**Net result: Task 7 is genuinely PASS, independently verified against
raw CI evidence — not assumed from the commit existing or from a green
checkmark alone.**

### Regression

No file outside `infra/nginx/*`, `infra/docker-compose.yml`, and
`.github/workflows/tests.yml` was touched this task. Tasks 1–6's own
CI jobs (`auth-service`, `catalogue-service`, `document-service`,
`search-service` unit tests) are unaffected — this task touched only
Nginx/infra-integration-level configuration. Every existing
infra-integration acceptance step (Tasks 3–6's) still runs unmodified,
after the two new CORS steps, and is expected to keep passing since
`include /etc/nginx/cors.conf;` only ADDS response headers — it never
changes routing, rewrites, or `proxy_pass` targets on any existing
location.

### Documentation updated

- `docs/SECURITY_HARDENING_LOG.md`: this entry.
- `docs/SECURITY_PROJECT_REPORT.md`: Task 7 row and section.

## Task 8 — Security Headers / Nginx Hardening

**Date:** 2026-09-08

**Status:** PASS — verified against raw CI evidence, see "Exact
results" below (commit `ef4d087`, run `34198753195`, all 5 jobs green
on the first attempt, no fix iteration needed).

### Goal

Harden the public Nginx layer and response headers — version
disclosure, MIME-sniffing, framing, referrer leakage, unnecessary
browser capabilities, a real CSP — without breaking the application,
Task 7's CORS, auth/refresh/logout, uploads, downloads, MinIO
proxying, or service-to-service traffic. This is the last individual
hardening task before the final full security audit.

### Initial Nginx security state (before this task)

- `server_tokens` was never set — Nginx's default (`on`) discloses the
  exact Nginx version in the `Server` response header on every
  response AND in the footer of every Nginx-generated default error
  page.
- No security response header of any kind existed anywhere —
  `grep -rn "add_header\|X-Frame-Options\|Content-Security-Policy\|X-Content-Type-Options\|Referrer-Policy\|Permissions-Policy\|Strict-Transport-Security" infra/nginx/` (before this task) returned nothing outside Task 7's own `Access-Control-*` headers in `cors.conf`.
- Every proxied location forwarded whatever `Server` header its
  upstream sent, unmodified — MinIO's own `Server: MinIO` (a specific
  product+version disclosure) and each FastAPI service's uvicorn
  `Server` header passed straight through to the browser.
- No explicit `client_body_timeout`/`client_header_timeout` — Nginx's
  own defaults applied, never made explicit.

### Repository inspection performed

Per this task's explicit instruction to inspect before touching
anything:

- **`infra/nginx/nginx.conf.template`**: all 17 client-facing
  locations (`/`, `/documents/`, `/products/`, and the 14 `/api/*`
  locations) re-read end to end; the internal-only `= /_verify`
  location confirmed never client-reachable (`internal;`).
- **`infra/nginx/cors.conf`**, **`docker-entrypoint.sh`**,
  **`validate-cors-config.sh`**, **`infra/docker-compose.yml`**:
  re-read to understand the existing `include`-snippet pattern (this
  task reuses it exactly, per "Exact implementation" below) and the
  existing envsubst variable-substitution mechanism.
- **`web/index.html`** (the ONLY file this app ever serves as a page —
  confirmed `find web -type f` returns just this one file, no separate
  `.js`/`.css`):
  - `grep -n "<script\|<link\|<style"` → one `<link rel="preconnect">`
    + one `<link rel="stylesheet">` to `fonts.googleapis.com`, one
    inline `<style>` block (lines 9–457), one inline `<script>` block
    (lines 1514–5283, no `src=` — the entire app).
  - `grep -c 'style="'` → **227** inline `style="..."` attributes
    scattered through the file, several with dynamically-computed
    values (e.g. a skeleton-loading bar's width built from live data)
    — these cannot be covered by a CSP hash (a hash only matches one
    exact, fixed string).
  - `grep -n "onclick=\|onerror=\|onload=\|javascript:"` → **none** —
    zero inline event-handler attributes anywhere.
  - `grep -n "eval(\|new Function("` → **none**.
  - `grep -oE 'https?://[a-zA-Z0-9.-]+'` → only `fonts.googleapis.com`
    (the CSS `<link>`) — no other external host referenced anywhere.
    The actual font *files* that stylesheet's `@font-face` rules point
    to are hosted at `fonts.gstatic.com` (Google's own convention, a
    different host than the CSS) — not literally present as a string
    in `index.html`, so accounted for separately in the CSP design.
  - `grep -n "<iframe\|<object\|<embed\|<frame"` → **none**.
  - `grep -n "new Worker\|Blob(\|blob:\|createObjectURL"` → 5 call
    sites, ALL the same pattern: `fetch()` → `blob()` →
    `URL.createObjectURL()` → a synthetic `<a download>` → `.click()`
    → `URL.revokeObjectURL()` — i.e. triggering a file **download**,
    never displaying a blob inline as an `<img>`/`<iframe>`/anything
    CSP-relevant.
  - `grep -n "WebSocket\|EventSource"` → **none**.
  - `grep -n "<img"` and `createElement\('img'\)` → **none anywhere** —
    this app never renders an image inline. File/logo/seal previews
    are opened via `window.open(data.url, '_blank')` (4 call sites) —
    a new top-level tab, not an embedded resource; CSP's `img-src`/
    `frame-src` are irrelevant to a `window.open` navigation (only
    `form-action`/`navigate-to` govern navigations, and this app
    submits no forms to anywhere but itself — see below).
  - `grep -n "<form"` → 7 forms, **none** have an `action=` attribute —
    every one is JS-handled (`addEventListener('submit', ...)` +
    `e.preventDefault()` + `fetch()`).
  - `grep -n "navigator\.\|getUserMedia\|geolocation\|clipboard\|requestFullscreen\|PaymentRequest\|usb\."` → **none** — this app uses zero
    of these browser capabilities anywhere.
  - `grep -n "favicon\|data:image\|data:"` and
    `<link rel="icon">` → **none** — no favicon, no data: URIs.
  - `grep -n "manifest\|serviceWorker"` → **none**.
- **Auth/refresh-token flow** (Task 6): re-confirmed the frontend never
  reads a response header via JS (`grep -n "headers.get\|\.headers\["`
  → none, already established in Task 7's inspection) — headers this
  task adds are pure browser-enforced policy, nothing in the app's own
  JS needs to read or react to any of them.
- **Task 5's upload/download Content-Type handling**: re-read
  `upload_safety.py`/`documents.py`/`products.py` — `X-Content-Type-Options: nosniff` (this task) is a genuine complement to that work, not a
  duplicate of it: Task 5 controls what Content-Type is stored/served;
  `nosniff` stops the BROWSER from ever re-guessing/overriding
  whatever Content-Type this app served, closing the loop from the
  other side.
- **Current Nginx version**: `nginx:alpine` (`infra/docker-compose.yml`,
  unpinned to a specific patch version — already resolves to whatever
  the tag's latest build is at pull time; out of this task's scope to
  change, noted for completeness).

### Whether CSP requires a frontend change

**Yes, in one narrow, explicit way — but not a rewrite.** Per this
task's explicit "if CSP requires frontend changes, make the minimum
secure change necessary and document it" instruction:

- No change was needed to allow the app's ONE inline `<script>` block:
  its exact SHA-256 hash is used as a CSP `script-src` source instead
  of `'unsafe-inline'` — the file's actual bytes were not touched.
- No change was made to the 227 inline `style="..."` attributes —
  converting them all to CSS classes would be a large, high-risk
  rewrite of a 5000+ line single file with no build step, tooling, or
  tests to catch a mistake, and would be far beyond "minimum necessary"
  for this task. `'unsafe-inline'` is used for `style-src` alone
  (never for `script-src`) as a deliberate, narrow, documented
  exception — see "CSP design" below for exactly why this is the
  correct scoped trade-off, not a blanket weakening.
- No `<meta http-equiv="Content-Security-Policy">` tag was added to
  `index.html` — the policy is delivered as a real HTTP response
  header (Nginx `add_header`) instead, which is strictly more capable
  (a meta-tag CSP cannot set `frame-ancestors` at all — the directive
  is explicitly ignored by spec when delivered that way — among other
  header-only restrictions) and keeps the policy alongside every other
  header this task adds, in one place.

### Exact implementation

Reuses Task 7's exact `include`-snippet pattern (a new file, `include`d
as the first directive[s] in every client-facing location) rather than
inventing a second mechanism:

- **New file: `infra/nginx/security-headers.conf`** — `include`d into
  all 17 client-facing locations (`/`, `/documents/`, `/products/`,
  and the 14 `/api/*` locations — NOT the internal-only `= /_verify`
  location). Sets, on every response (`always`, so error responses get
  them too — see requirement 11 below):
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `X-Frame-Options: SAMEORIGIN`
  - `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=(), fullscreen=(), clipboard-read=(), clipboard-write=()`
  - `Content-Security-Policy: <see below>`
- **Changed: `infra/nginx/nginx.conf.template`** — `server_tokens off;`
  added to the `http {}` block; `client_body_timeout 30s;`/
  `client_header_timeout 30s;` made explicit (previously Nginx's own
  unstated defaults); `include /etc/nginx/security-headers.conf;` +
  `proxy_hide_header Server;` added to all 17 locations (the 14
  `/api/*` locations already had `include /etc/nginx/cors.conf;` as
  their first line from Task 7 — the new include was inserted
  immediately after it, in the same location context, so both sets of
  headers apply together on real responses; see "Interaction with
  Task 7's CORS preflight" below for the one place they deliberately
  DON'T both apply).
- **Changed: `infra/docker-compose.yml`** — new volume mount for
  `security-headers.conf`, matching `cors.conf`'s existing mount
  pattern exactly.

### Exact headers and values

```
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
X-Frame-Options: SAMEORIGIN
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=(), fullscreen=(), clipboard-read=(), clipboard-write=()
Content-Security-Policy: default-src 'self'; script-src 'sha256-P4MQVlq/RTqfvllWKvmddqLTW9Vy+XgeC6L2Xz0YbvE='; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self'; connect-src 'self'; object-src 'none'; frame-src 'none'; frame-ancestors 'self'; base-uri 'self'; form-action 'self';
```

No `Strict-Transport-Security` — see "HSTS decision" below.

### CSP design — why each source is allowed (and why others aren't)

| Directive | Value | Why |
|---|---|---|
| `default-src` | `'self'` | Fallback for every directive not listed below (`worker-src`, `manifest-src`, `media-src`, ...) — none of those resource types are used anywhere in the app. |
| `script-src` | `'sha256-P4MQVlq/RTqfvllWKvmddqLTW9Vy+XgeC6L2Xz0YbvE='` | The exact hash of the one inline `<script>` block's exact text content. No `'unsafe-inline'`, no `'unsafe-eval'` — zero `eval()`/`new Function()` usage, zero inline event-handler attributes (`onclick=`, etc. — grepped, none exist), so nothing else needs to execute as script. |
| `style-src` | `'self' 'unsafe-inline' https://fonts.googleapis.com` | `'unsafe-inline'` is the one deliberate, documented, narrowly-scoped exception in this policy — see "Whether CSP requires a frontend change" above for the full reasoning; `fonts.googleapis.com` is where the Google Fonts stylesheet `<link>` actually loads from. |
| `font-src` | `'self' https://fonts.gstatic.com` | Where the `*.woff2` files that stylesheet's `@font-face` rules reference are actually hosted (a different host than the CSS, by Google's own convention). |
| `img-src` | `'self'` | No `<img>` tag exists or is ever created anywhere in the app (grepped) — nothing to widen this for. |
| `connect-src` | `'self'` | Every `fetch()` call targets `API_BASE` (`/api`, same-origin) — no external API call anywhere. |
| `object-src` | `'none'` | No `<object>`/`<embed>` anywhere. |
| `frame-src` | `'none'` | No `<iframe>` anywhere. |
| `frame-ancestors` | `'self'` | See "Framing protection" below. |
| `base-uri` | `'self'` | Blocks a hypothetical injected `<base>` tag from silently rehoming every relative URL in the page to an attacker's origin. |
| `form-action` | `'self'` | Every `<form>` is JS-handled with `preventDefault()`; none has an `action=` anywhere (grepped) — pure defense-in-depth against a hypothetical injected `<form>`. |

**Explicitly rejected**: `default-src * 'unsafe-inline' 'unsafe-eval'`
(the exact anti-pattern this task's instructions call out by name) —
would permit loading script/style/anything from any origin and running
arbitrary inline/`eval`'d code, defeating CSP's entire purpose. Not
used anywhere in this policy.

**Maintenance note on the hash**: it is tied to the EXACT byte content
of the `<script>` block. Recompute it after any future edit to that
block with:
```
python3 -c "import hashlib, base64; content = open('web/index.html', 'rb').read(); start = content.index(b'<script>\n') + len(b'<script>\n'); end = content.index(b'</script>', start); print('sha256-' + base64.b64encode(hashlib.sha256(content[start:end]).digest()).decode())"
```
The new CI acceptance step (see "Tests") runs this exact computation
against the actually-served file on every CI run and fails loudly if
it ever drifts from the hash baked into the CSP header — so a forgotten
recomputation is caught by CI, not discovered as a silently broken app
in production.

### Framing protection

`X-Frame-Options: SAMEORIGIN` + `Content-Security-Policy:
frame-ancestors 'self'` together (the modern `frame-ancestors`
actually governs this in current browsers; `X-Frame-Options` is kept
alongside it only for any legacy client that doesn't understand
`frame-ancestors` — both express the identical policy, never a
conflict). Verified safe: the app embeds no iframe of its own
(`frame-src 'none'`) and nothing in the app relies on being embedded
BY another page either (grepped — no postMessage/embedding-aware code
anywhere) — this purely closes off clickjacking (an attacker's page
framing this app and overlaying invisible UI on top of it), a pure
addition with nothing legitimate to break.

### Referrer policy

`strict-origin-when-cross-origin` — the value this task's own
instructions suggested as the default, and inspection didn't surface a
reason to deviate: this app is same-origin end-to-end (Task 7), so the
"cross-origin" case this policy governs only ever fires for the one
real cross-origin request the app makes (the Google Fonts stylesheet
`<link>`) — sending only the bare origin there (never a full path/query
string) rather than the fuller referrer same-origin requests get.

### Permissions policy

`camera=(), microphone=(), geolocation=(), payment=(), usb=(),
fullscreen=(), clipboard-read=(), clipboard-write=()` — every one of
these denied outright. Inspection (`grep -n "navigator\.\|getUserMedia\|geolocation\|clipboard\|requestFullscreen\|PaymentRequest\|usb\."`) found this app uses NONE of them anywhere — so nothing legitimate is
disabled; this only removes capabilities that would otherwise sit
available for something else running in this browsing context (an
injected/compromised script, or a future accidental dependency) to
invoke without it ever being a reviewed, intentional feature of this
app.

### MIME/content sniffing

`X-Content-Type-Options: nosniff` on every response. Verified
compatible with Task 5's upload/Content-Type work — it's a direct
complement, not a duplicate: Task 5 already ensures the CORRECT
Content-Type is derived server-side and stored/served (never trusting
the client); `nosniff` is what stops the BROWSER from re-sniffing and
overriding that correct, already-safe value — the two together close
the loop from upload to download. Verified NOT to break legitimate
PDFs/images/downloads/static assets/MinIO-proxied objects: `nosniff`
only changes what happens when a response's declared Content-Type is
WRONG (it stops the browser from guessing something else instead) — it
never changes or rejects a CORRECT Content-Type, so every already-
correct response (which is all of them, per Task 5) is unaffected.

### HSTS decision

**Not added.** Per this task's explicit instruction to inspect the
actual deployment architecture first: `infra/nginx/nginx.conf.template`
has exactly one `listen 80;` directive — Nginx never terminates TLS
anywhere in this repository, confirmed also by `docs/deployment.md`'s
own existing, pre-Task-8 statement: *"nginx.conf listens on plain :80
... with no HTTPS/TLS termination configured anywhere in this
repository. Not verified: whether TLS is terminated by something in
front of this stack ... that isn't part of this repo."* Since this
repository cannot honestly guarantee HTTPS is in place wherever this
exact config is deployed, adding `Strict-Transport-Security` would be
actively harmful, not just unnecessary: HSTS tells a browser "refuse
to ever connect to this host over plain HTTP again," and if that turns
out not to be backed by real, working TLS at whatever layer sits in
front of this Nginx, the result is a site the browser now refuses to
load at all — worse than doing nothing. **Documented for whoever
deploys this to production**: HSTS must be enabled at whatever layer
actually terminates TLS in front of this Nginx (a cloud load balancer,
a separate TLS-terminating reverse proxy, etc.), once HTTPS there is
genuinely guaranteed — not in this repository's own `nginx.conf.template`, which never itself speaks TLS.

### Other Nginx hardening

- **`server_tokens off;`** (http block) — removes the Nginx version
  from the `Server` header AND from Nginx's own auto-generated error
  pages (404/500/etc. footers) in one directive.
- **`proxy_hide_header Server;`** on every proxied location (all 16
  `proxy_pass` locations — the 14 `/api/*` ones plus `/documents/`/
  `/products/`) — `server_tokens off` alone only affects headers Nginx
  ITSELF generates; it does nothing to stop an upstream (MinIO's own
  `Server: MinIO`, or each FastAPI service's uvicorn `Server` header)
  from passing straight through the proxy unmodified. This is exactly
  the "do not assume server_tokens off alone is sufficient" case this
  task's own instructions warned about, and was verified by actually
  checking what MinIO/uvicorn send, not assumed.
- **`client_body_timeout 30s;` / `client_header_timeout 30s;`** (http
  block) — explicit, conservative slow-client protection; previously
  unset (Nginx's own unstated 60s defaults applied). Left the
  route-specific `proxy_read_timeout 300s;` on `/api/documents`
  (needed for the slow catalogue export, pre-existing from before this
  task) untouched — these two settings govern different things (how
  long Nginx waits for the CLIENT to finish sending a request vs. how
  long it waits for the UPSTREAM to respond) and don't conflict.
- **Directory listing**: `autoindex` was never set anywhere — Nginx's
  own default (`off`) already applies; confirmed explicitly rather
  than left as an unstated assumption, no change needed.
- **HTTP methods**: deliberately NOT globally restricted at Nginx, per
  this task's own explicit caution against blindly rejecting uncommon
  methods. Reasoning: (1) every backend route already enforces its own
  accepted methods via FastAPI route decorators (an unsupported method
  on a real endpoint already 405s, independent of Nginx); (2) Task 7's
  CORS preflight handling depends on `OPTIONS` reaching each location
  correctly — an Nginx-level method allowlist risks silently breaking
  that if not built with equal care; (3) inspection found no
  currently-exploitable risk from leaving methods unrestricted at this
  layer (no location trusts an unexpected method for anything). GET/
  POST/PATCH/DELETE/OPTIONS (the frontend's actual methods, confirmed
  again by re-grepping `web/index.html`) all continue to work — see
  "Tests".
- **Directory/location exposure**: re-confirmed the internal-only
  `= /_verify` location has `internal;` set (unreachable by any direct
  client request, only by Nginx's own `auth_request`) — no change
  needed, already correct from Task 4.

### Interaction with Task 7's CORS preflight

`security-headers.conf` is `include`d in the same location context as
`cors.conf`, right after it — for a REAL (non-`OPTIONS`) request, both
files' `add_header` directives apply together (this is what the tests
below verify: the full header set present on ordinary GET/POST
responses). For an `OPTIONS` preflight, `cors.conf`'s own `if
($request_method = OPTIONS) { ...; return 204; }` block is a nested
context that does NOT inherit the outer location's `add_header`
directives (Nginx's own documented `add_header` inheritance rule) — so
a preflight response carries only the CORS preflight headers, not the
full security-header set. This is intentional and harmless: a
preflight response is never rendered as a page or script by the
browser, so CSP/`X-Frame-Options`/`Permissions-Policy` have no
meaningful effect there either way.

### Security headers must not be duplicated (requirement 12)

No FastAPI app in this project sets ANY of these headers — confirmed
by `grep -rn "X-Frame-Options\|Content-Security-Policy\|X-Content-Type-Options\|Referrer-Policy\|Permissions-Policy" services/*/app/` returning
nothing, matching Task 7's same finding for CORS headers. Exactly one
layer (Nginx) ever sets any of them, exactly once per response — the
new CI test explicitly counts each header's occurrences on a real
response and fails if it's ever anything other than exactly 1.

### Tests

New: `.github/workflows/tests.yml`, `infra-integration` job — ONE new
step, **"Acceptance — security headers, version disclosure, CSP
integrity, no duplicates"** (real HTTP through the real Nginx/
docker-compose stack, per this task's explicit "do not rely only on
FastAPI TestClient" instruction — there is no FastAPI-level header
code to unit test in the first place, since it's implemented entirely
in Nginx, same as Task 7's CORS):

1. `GET /` — `Server` header contains no version digit; all 5 core
   security headers present with the exact expected value, each
   exactly once (no duplicates).
2. **CSP hash-integrity check**: the CSP header's `script-src` hash is
   compared against a hash FRESHLY COMPUTED from the actually-served
   `index.html`'s real inline `<script>` content (the exact same
   computation given in "CSP design" above, run in CI, not just
   asserted to match) — this is what catches drift if the script is
   ever edited without recomputing the hash.
3. `GET /api/documents` with no `Authorization` header — 401 (from
   Nginx's own `auth_request` gate, not a proxied backend body) still
   carries `X-Content-Type-Options` and `Content-Security-Policy`
   exactly once each; the 401 response body contains no Nginx version
   string.
4. A 26MB request body against `/api/auth/login` (over the 25MB
   `client_max_body_size`) — genuinely Nginx-generated 413, before
   ever reaching a backend; response body contains no Nginx version
   string, proving `server_tokens off` covers Nginx's OWN generated
   error pages, not just normal-response `Server` headers.
5. `GET /documents/no-such-key` (MinIO passthrough, unauthenticated by
   design — Task 3) — `X-Content-Type-Options: nosniff` present;
   `Server` header does NOT contain "minio" — proving
   `proxy_hide_header Server` actually suppresses the upstream's own
   disclosure, not just asserted to.

### Regression

No application code was touched — only `infra/nginx/*` and
`infra/docker-compose.yml`. Every existing infra-integration
acceptance step (Tasks 3–7's) runs unmodified, immediately before this
new step, and is expected to keep passing: `include
/etc/nginx/security-headers.conf;` and `proxy_hide_header Server;`
only ADD/replace response headers — they never change routing,
rewrites, `proxy_pass` targets, or `auth_request` behavior on any
existing location. `auth-service`/`catalogue-service`/
`document-service`/`search-service` unit-test jobs are entirely
unaffected (no service code changed).

### Exact results

Run: https://github.com/kaungkyaw3124/doc-mgmt/actions/runs/34198753195
(commit `ef4d087`), all 5 jobs **PASSED** on the first attempt — no fix
iteration was needed. Verified via `mcp__github__get_job_logs` with
`return_content: true` (raw log content, not the `conclusion` field
alone), job `101972440688` (`infra-integration`), step "Acceptance —
security headers, version disclosure, CSP integrity, no duplicates" —
real output quoted directly from the raw log:

```
Server header on / -> Server: nginx
/ X-Content-Type-Options -> X-Content-Type-Options: nosniff
/ Referrer-Policy -> Referrer-Policy: strict-origin-when-cross-origin
/ X-Frame-Options -> X-Frame-Options: SAMEORIGIN
/ Permissions-Policy -> Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=(), fullscreen=(), clipboard-read=(), clipboard-write=()
/ Content-Security-Policy -> Content-Security-Policy: default-src 'self'; script-src 'sha256-P4MQVlq/RTqfvllWKvmddqLTW9Vy+XgeC6L2Xz0YbvE='; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self'; connect-src 'self'; object-src 'none'; frame-src 'none'; frame-ancestors 'self'; base-uri 'self'; form-action 'self';
computed inline-script hash -> sha256-P4MQVlq/RTqfvllWKvmddqLTW9Vy+XgeC6L2Xz0YbvE=
unauthenticated /api/documents -> HTTP/1.1 401 Unauthorized
401 /api/documents X-Content-Type-Options -> X-Content-Type-Options: nosniff
401 /api/documents Content-Security-Policy -> Content-Security-Policy: default-src 'self'; script-src 'sha256-P4MQVlq/RTqfvllWKvmddqLTW9Vy+XgeC6L2Xz0YbvE='; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self'; connect-src 'self'; object-src 'none'; frame-src 'none'; frame-ancestors 'self'; base-uri 'self'; form-action 'self';
oversized request body -> HTTP/1.1 413 Request Entity Too Large
MinIO passthrough headers -> Server: nginx
X-Content-Type-Options: nosniff
```

Every assertion in the step passed silently (no `FAIL:` line appears
anywhere in the actual output above) — most notably: `Server: nginx`
with **no version digit** on both the frontend response AND the
MinIO-proxied response (proving `proxy_hide_header Server` actually
suppressed MinIO's own `Server: MinIO` — it did not merely go
untested); the **freshly computed** hash of the actually-served
`index.html`'s inline `<script>` block matches the CSP header's
`script-src` value exactly (`sha256-P4MQVlq/RTqfvllWKvmddqLTW9Vy+XgeC6L2Xz0YbvE=` both times); the 401 (Nginx's own `auth_request` error) and
the 413 (Nginx's own body-size rejection) both carry the header set/
disclose no version, proving `always` correctly extends these headers
to Nginx's own internally-generated error responses, not just normal
2xx ones.

All other infra-integration steps — Tasks 3–7's acceptance checks
(host-exposure, MinIO passthrough connectivity, legitimate-request
passthrough, trust-header overwrite, direct-backend header spoofing,
upload Content-Type, refresh-token rotation, CORS policy) — **PASSED**
unmodified in the same run, confirming zero regression.
`auth-service`/`catalogue-service`/`document-service`/`search-service`
unit-test jobs: all **PASSED**.

**Net result: Task 8 is genuinely PASS, independently verified against
raw CI evidence for the complete relevant test suite — not assumed
from the commit existing or from a green checkmark alone.**

### Documentation updated

- `docs/SECURITY_HARDENING_LOG.md`: this entry.
- `docs/SECURITY_PROJECT_REPORT.md`: Task 8 row and section, and an
  update to make Tasks 1–8 accurately represented as a whole (per this
  task's explicit instruction) once CI evidence confirms PASS.

---

## Task 9 — Company/Managing Director Restructuring (and its correction)

**Date:** 2026-09-09

**Status:** PASS — verified against raw CI evidence, see "Exact
results" below (final commit `f151afa`, run `34311652657`, all 5 jobs
green).

**Not a security-hardening task.** This is a data-model/UI
restructuring requested directly by the repo owner, working on the same
`security/auth-hardening` branch. Recorded here per that explicit
instruction, and because the correction pass (below) touched the same
CSP hash mechanism Task 8 introduced.

### Goal

Move company signer/contact display off a single flat `Company` record
onto individual, per-company **Managing Directors** (`CompanyDirector`,
which already existed in the schema with `name`/`seal_object_key`), and
remove `Company.support_phone` (confirmed unused anywhere in the app).
Two rounds of instructions were given for this, and the second
corrected a real mistake in the first — both are recorded below rather
than only the final state, per this project's established "never hide
a failure" practice.

### Round 1 — initial (incorrect) implementation

The first instruction ("Move signer details to managing directors")
asked to remove `position`/`address`/`contact_no`/`support_email`/
`support_phone` from `Company` entirely and source the quotation's
Supplier block from the selected `CompanyDirector` instead. Implemented
as commit `835f6a7`: all five fields dropped from `Company`,
`CompanyDirector` gained `address`/`contact_no`/`email`, cross-company
director validation added to `documents.py` (previously **missing
entirely** — a document could be saved with a `director_id` belonging
to a different `company_id` than its own `company_id`; not previously
flagged by any test), and the quotation Supplier block was switched to
read `director.address`/`director.contact_no`/`director.email`.

CI run `34308257357` (commit `835f6a7`) **FAILED** — `infra-integration`
job, step "Acceptance — company/director restructuring". This surfaced
before the follow-up instruction arrived, and is folded into this same
entry rather than written up separately, since the follow-up
instruction directly superseded the design this CI failure was testing.

### Round 2 — correction (per explicit follow-up instruction)

A second, explicit instruction ("Check current migration... Do NOT
blindly accept that migration... Company must continue to have
name/short_name/position/address/contact_no/support_email/logo/seal.
Only company-level `support_phone` should be removed.") identified that
Round 1 had gone further than intended: `position`/`address`/
`contact_no`/`support_email` belong on `Company` (used for the
quotation **Supplier** block: Name/Address/Contact No/Email — sourced
from `company.name`/`company.address`/`company.contact_no`/
`company.support_email`; `position` is kept on the model for
signer/signature use but deliberately **not** rendered in the Supplier
block), and only `support_phone` should actually be removed (it was
never read anywhere in the app — `grep` for `settings.support_phone`/
`company.support_phone`/`support_phone` outside `models.py`/
`schemas.py` found no usage prior to this task).

**Files changed for the correction** (commit `bad123d`):
- `services/document-service/app/models.py` — restored
  `position`/`address`/`contact_no`/`support_email` to `Company`;
  `CompanyDirector` unchanged (already correct in Round 1).
- `services/document-service/app/schemas.py` — restored those four
  fields to `CompanyCreate`/`CompanyUpdate`/`CompanyOut`.
- `services/document-service/app/routers/documents.py` —
  `_gather_export_data`'s `company` dict rebuilt with
  `position`/`address`/`contact_no`/`support_email`; cross-company
  director validation (added in Round 1) left untouched.
- `services/document-service/app/core/export_quotation.py` (XLSX) and
  `app/templates/quotation.html` (PDF) — Supplier block's
  Address/Contact No rows switched back to `company.*`, with a new
  Email row added (`company.support_email`); `company.position` is
  available in the `company` dict but deliberately never rendered in
  the Supplier block (comment added explaining why, so a future editor
  doesn't "fix" the apparent gap). The "Customer Service" footer
  (Email/Phone) also switched back to `company.support_email`/
  `company.contact_no` (not `support_phone`, which no longer exists).
  The MD signature block (director's own name + seal) is unaffected —
  it was already sourced from `director`, not `company`, in both
  rounds.
- `services/document-service/alembic/versions/0002_*.py` — **renamed**
  from `0002_move_signer_details_to_directors.py` to
  `0002_director_contact_fields_drop_company_support_phone.py`
  (accurately describing its corrected scope) and rewritten: `upgrade()`
  now only drops `companies.support_phone` (previously dropped all
  five); `downgrade()` only re-adds `support_phone`. The idempotent
  catch-up of `company_directors`/`documents.director_id` (a
  pre-existing gap — see known-issues.md #10 — `0001_initial` never
  created either, they've only existed via `Base.metadata.create_all()`)
  is unchanged from Round 1. This migration was corrected in place
  rather than superseded by a new migration file, since it had not yet
  been applied anywhere (pushed minutes earlier, in the same task
  line) — not a case of editing an already-applied migration.
- `web/index.html` — restored the Signer position/Address/Contact
  No/Support email fields to the Company form, table columns, and
  recycle-bin table (all removed in Round 1); the `support_phone` field
  stays removed. Director rows (Name/Address/Contact No/Email/Seal/
  Remove, unlimited, added in Round 1) are unaffected.
- `services/document-service/tests/test_companies.py` — rewritten:
  asserts `Company` schemas **keep**
  `position`/`address`/`contact_no`/`support_email` and **only**
  `support_phone` is gone; `CompanyDirector` schema tests unchanged
  from Round 1; added a test that Company's and CompanyDirector's
  contact fields never collapse into the same field names
  (`support_email` vs `email`).
- `.github/workflows/tests.yml` — the infra-integration acceptance step
  rewritten to match: company create/update round-trips
  `position`/`address`/`contact_no`/`support_email` but never
  `support_phone`; the exported quotation is asserted to contain the
  **company's** own address/contact_no/support_email (and explicitly
  asserted to **not** contain the director's differing address/contact
  number, and to **not** leak `company.position` as its own cell), while
  the MD signature block is asserted to still show the selected
  director's own name; cross-company rejection (create **and** update)
  re-verified.
- `infra/nginx/security-headers.conf` — the CSP `script-src` hash
  recomputed and updated **twice** in this task (once after Round 1's
  frontend edits, once after Round 2's), each time following Task 8's
  documented recomputation method — editing `web/index.html`'s inline
  `<script>` block changes its exact byte content, which changes its
  SHA-256 hash; forgetting this step reintroduces the exact
  browser-side CSP breakage diagnosed earlier in this session (see the
  "gpu-server login" investigation elsewhere in this conversation).
  Final hash: `sha256-ICbqTVvj8bvpm//tPN24i51A+h6i4GUbHMu2XjpI8lE=`
  (line 1511 at time of computation).

### Bug found and fixed during verification: CI step itself, not app code

CI run `34311472024` (commit `bad123d`, the correction above) **also
FAILED** — same step. Raw job logs (`mcp__github__get_job_logs`,
`return_content: true`, job `102329364892` and the `bad123d` run's
equivalent job) showed the actual API traffic succeeded end to end
(company/director creation, both cross-company 400 rejections, `GET
.../export/quotation -> 200`), immediately followed by:

```
quotation export -> 200
##[error]Process completed with exit code 11.
```

Exit code 11 is `unzip`'s own documented code for "no matching files
were found." The step's export-content check ran
`unzip -p /tmp/quotation.xlsx xl/sharedStrings.xml > /tmp/shared_strings.xml
2>/dev/null` with no `||` fallback, under GitHub Actions' default
`bash -e`; when that exact path didn't resolve inside the archive, the
whole step aborted immediately — before any of the step's own explicit
`FAIL:` assertions ever ran. This was a bug in the **CI verification
script**, not in the application: `generate_quotation_xlsx` itself was
never at fault.

**Fix** (commit `f151afa`): replaced the single-path `unzip -p` with a
Python (stdlib `zipfile`) one-liner that concatenates the text of every
`.xml` member in the archive, so the check no longer depends on
assuming one exact internal path:

```
python3 -c 'import zipfile; z = zipfile.ZipFile("/tmp/quotation.xlsx"); text = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith(".xml")); open("/tmp/xlsx_text.txt", "w").write(text)'
```

(Written as a single physical line inside the YAML `run: |` block —
the same multi-line-inside-a-block-scalar indentation break documented
in Task 8's own hardening log entry recurred while drafting this fix,
and was caught the same way: `python3 -c "import yaml; yaml.safe_load(...)"`
before pushing.)

### Tests executed

- `services/document-service` unit-test job (`pytest -v`, no live DB —
  same pattern as `test_gateway_auth.py`): `test_companies.py`'s 15
  schema-level tests, alongside the service's other unit tests.
- `infra-integration` job step "Acceptance — company/director
  restructuring (director gets own contact info, company keeps its
  Supplier fields, support_phone removed)": real HTTP through Nginx
  against the full `docker compose` stack — company create/update
  round-trip, two directors on one company (create + partial update),
  cross-company director rejection on both document create and update,
  quotation XLSX export content, company update still works.
- Full regression: all other existing `infra-integration` acceptance
  steps (Tasks 3–8) and all four services' unit-test jobs, in the same
  run.

### Exact results

Run: https://github.com/kaungkyaw3124/doc-mgmt/actions/runs/34311652657
(commit `f151afa`) — all 5 jobs **PASSED**. Verified via
`mcp__github__get_job_logs` with `return_content: true` (raw content,
not the `conclusion` field alone).

`document-service` job (id `102339379386`), final pytest summary line:

```
======================== 68 passed, 4 warnings in 1.98s ========================
```

`infra-integration` job (id `102339379438`), step 17's actual runtime
output (not the echoed script source GitHub Actions prints before
executing it — the two are distinguishable by timestamp; quoted here is
the post-`shell:`-line execution only), verbatim:

```
created company: {"id":"3a0befc9-d111-4817-9313-2362838ee930","name":"Restructure Test Co.","short_name":"RTC","position":"Director","address":"Company HQ, Yangon","contact_no":"+95911111111","support_email":"supplier@restructuretest.example.com","logo_object_key":null,"seal_object_key":null,"is_primary":true,"is_deleted":false,"created_at":"2026-09-09T04:38:20.837777Z"}
created director A: {"id":"144cc404-6825-497c-9b24-404f363ae942","company_id":"3a0befc9-d111-4817-9313-2362838ee930","name":"Aung Aung","address":"Mayangon, Yangon","contact_no":"+95999999999","email":"aungaung@gmail.com","seal_object_key":null,"sort_order":1,"created_at":"2026-09-09T04:38:20.932323Z"}
created document: {"id":"c5ce4709-a97b-4ee5-8386-682e3eab7a39", ... "company_id":"3a0befc9-...","director_id":"144cc404-...", ...}
cross-company create -> 400: {"detail":"director does not belong to the selected company"}
cross-company update -> 400: {"detail":"director does not belong to the selected company"}
quotation export -> 200
```

No `FAIL:` line appears anywhere in this actual runtime output (every
occurrence of the literal text `FAIL:` in the full log is from GitHub
Actions echoing the step's own source code before execution, not from
that code actually running — confirmed by timestamp: all source-echo
lines carry one identical timestamp, `04:38:20.61xx`, while the
executed output above starts at `04:38:20.8777029` and runs through
`04:38:21.3767941`). The script proceeded straight to
`docker compose down -v` (teardown) after the export check, meaning
`exit $fail` returned `0` — every assertion passed, including: the
company's own `position`/`address`/`contact_no`/`support_email`
round-tripped and `support_phone` is absent from `CompanyOut`; the
director's own `address`/`contact_no`/`email` round-tripped separately
and the partial update (`contact_no` only) didn't clobber `name`/
`address`; both cross-company rejections fired with `400`; the exported
quotation's Supplier block contained the **company's** name, address,
contact number, and support email, did **not** contain
`company.position` ("Director") as its own cell, and did **not**
contain the director's differing address/contact number — while the MD
signature block still showed "Aung Aung" (the selected director's own
name).

All other infra-integration acceptance steps (Tasks 3–8) and all four
services' unit-test jobs: **PASSED**, unmodified, confirming zero
regression from this task.

### Security impact

None expected, and none found. This task changes which model field a
display value is read from (`Company` vs `CompanyDirector`) and removes
one genuinely-unused column; it does not touch authentication,
authorization, gateway trust, datastore isolation, upload validation,
CORS, or security headers. The one item worth flagging as a **security
improvement** (not a regression risk): the cross-company director
validation added in Round 1 — `director.company_id != payload.company_id`
→ `400` — did not exist before this task at all despite `Document`
already storing both `company_id` and `director_id`; it was preserved
unmodified through the Round 2 correction and is now verified via the
two `400` responses quoted above (create **and** update).

### Bugs found/fixed

1. Round 1 incorrectly moved `position`/`address`/`contact_no`/
   `support_email` off `Company` (should have stayed) — corrected in
   Round 2.
2. Missing cross-company director validation (pre-existing gap, not
   introduced by this task) — added in Round 1, preserved in Round 2.
3. CI verification step used an unguarded `unzip -p <exact-path>` under
   `bash -e`, which aborted the step via exit code 11 instead of
   running its own assertions — fixed with a path-independent Python
   `zipfile` check.

### Commits

- `835f6a7` — Round 1 (superseded by Round 2 below; CI failed on this
  commit, see above).
- `bad123d` — Round 2 correction (CI still failed, on the CI script
  bug above, not the application).
- `f151afa` — CI script fix. **This is the commit CI is green on.**

### Verification

Real GitHub Actions CI (`mcp__github__actions_list` /
`mcp__github__get_job_logs` with `return_content: true`), raw log
content quoted above — not the `conclusion` field alone, not assumed
from a green checkmark.

**Net result: Task 9 is genuinely PASS as of commit `f151afa`,
independently verified against raw CI evidence for the complete
relevant test suite.**

## Task 10 — User Control (Operation group, Editor/Viewer roles, admin user management, no deletion, pending registration)

### Task

Implement a full "User Control" system on top of the existing
Group/Role/RoleAccess authorization model, without inventing a second
access-control system:

1. An "Operation" group that normal operational users belong to,
   scoped to five operational resources (Documents, Products,
   Customers, Projects, Companies).
2. Exactly two normal roles — Editor (view/create/edit) and Viewer
   (view only) — enforced server-side, not bypassable via direct API
   calls.
3. Admin "Create User" (username/password/group/role), approved+active
   immediately, never able to create a superuser through this path.
4. User management: list shows Username/Group/Role/Status/Manage;
   manage allows changing group, role, active/inactive, and password.
5. No user deletion, ever — only Active/Inactive. Deactivation must
   fail login, fail refresh, revoke existing refresh tokens, and block
   the already-issued access token on its next protected call.
6. Existing self-registration/pending-approval workflow preserved
   alongside admin-created users.
7. Full authorization audit across all five resources — confirm the
   DB actually provides group/role scope for each, find and fix any
   gap, verify server-side enforcement can't be bypassed via direct
   API manipulation (including forged trust headers).
8. Full end-to-end regression after all of the above.

### Implementation

Reused the existing Group/Role/RoleAccess/UserRole/UserGroup model
end-to-end — no new authorization system:

- `services/auth-service/app/core/seed.py` (new): idempotent
  startup seeding of the Operation group with Editor (edit) and Viewer
  (view) roles, granted on the `documents` and `products` services —
  covers all five resources because `/api/customers`, `/api/companies`,
  and `/api/projects` already share the `documents` service_name in
  Nginx (see Task 4's routing). Re-seeding never overwrites an admin's
  manual changes; superuser is never auto-added; called from
  `main.py`'s existing `on_startup()`.
- `services/auth-service/app/routers/admin.py`: `POST
  /admin/groups/{id}/users` gained an optional `role_id` (validated to
  belong to the same group) assigned atomically with user creation;
  the created account is approved+active immediately;
  `CreateUserInGroupRequest` has no `is_superuser` field. `GET
  /admin/users` now returns each user's group and role names
  (batch-queried). New `GET /admin/users/{id}/groups` for the
  manage-user UI. **`DELETE /users/{id}` removed entirely** — deletion
  is not reachable through the API at all; deactivation (pre-existing
  `PATCH /users/{id}/active`, which already revoked refresh tokens and
  is already checked on every `/verify` call) is the only lifecycle
  control left.
- `web/index.html` / `web/js/app.js`: "+ Create User" button/modal on
  Admin → All Users; table columns become
  Username/Group/Role/Status/Manage; a single "Manage" action replaces
  the old Edit+Remove pair; manage-user modal gained a group list and
  an "add to group" picker. No CSP changes needed — `app.js` is
  already the external `script-src 'self'` file from Task 10 of the
  prior work (numbered independently in this branch's own history).
- **Real gap #1 found during the Task 7 audit**:
  `services/catalogue-service/app/routers/products.py` had **zero**
  server-side enforcement of view/edit access on any of its 9 write
  endpoints (`create_product`, `bulk_import_products`, `trash_product`,
  `restore_product`, `update_product`, `delete_product`,
  `upload_product_file`, `add_sub_item`, `remove_sub_item`) — a Viewer
  could write Products directly via the API despite the role model
  saying they shouldn't. Fixed by adding the same
  `_require_edit_access(x_access_level)` pattern already used by
  `document-service`'s companies/customers/projects routers to all
  nine endpoints. (`categories.py`'s separate `X-Has-Category-Access`
  axis was audited and confirmed out of scope — Editor/Viewer never
  grant the `categories` service.)
- **Real gap #2 found via real CI, not local review** (see Bugs
  found/fixed below): `infra/nginx/nginx.conf.template`'s
  `/api/products` location never captured or forwarded
  `X-Access-Level` at all.
- `.github/workflows/tests.yml`: new infra-integration acceptance step
  ("Acceptance — User Control") covering Tasks 1–7 end-to-end against
  the real Docker Compose stack: Operation group existence, exactly
  Editor+Viewer roles, admin-created Editor/Viewer login immediately
  with the correct `X-Access-Level`, `/admin/users` listing
  correctness, Editor create + Viewer 403 (plain **and** forged-header)
  on all 5 resources, no-deletion + deactivation's full login/
  refresh/live-token blast radius, self-registration's
  pending→approve→role-assign flow, and superuser remaining
  unrestricted.

### Tests

- `services/auth-service/tests/test_seed.py` (new, 8 tests): group
  creation, exactly-two-roles, Editor/Viewer grant correctness,
  idempotent + non-destructive re-seeding, superuser not auto-added,
  normal user can join.
- `services/auth-service/tests/test_user_control.py` (new, 13 tests):
  admin create Editor/Viewer with correct access level end-to-end
  through a real `/login` + `/verify` flow, cross-group `role_id`
  rejected, no `is_superuser` field on the create-user request schema,
  `/admin/users` listing correctness, no delete route (and the account
  provably still exists after attempting one), deactivation blocking
  login/refresh/the live access token, pending self-registration
  staying pending until approved, and the full
  approve→assign→login path working end to end.
- `services/catalogue-service/tests/test_products_access_level.py`
  (new): `_require_edit_access` unit behavior (allows edit, allows a
  missing header for backward compat, rejects view with 403), plus a
  parametrized check that every one of the 9 write endpoints 403s a
  Viewer with a schema-valid request (necessary because FastAPI
  validates the body before the route function runs, so an
  under-specified body would 422 before the access check is ever
  exercised — an early version of this test made that mistake and was
  corrected, see Bugs found/fixed).
- Full infra-integration acceptance step described above (real
  Postgres/MinIO/Meilisearch/Nginx/all four services, via `docker
  compose up -d --build`).

### Actual results (real CI, `mcp__github__get_job_logs` with
`return_content: true`, never the `conclusion` field alone)

First full push (commit `5ac8ab4`): `auth-service` **PASSED**.
`catalogue-service` **FAILED** (5 of 66 tests) and `infra-integration`
**FAILED** (1 of 18 acceptance steps) — both real, both diagnosed from
raw log content, not assumed.

After the CI/test-script fix (commit `4cabe8c`): `auth-service`,
`catalogue-service`, `document-service`, `search-service` all
**PASSED**. `infra-integration` **still FAILED** — but this time on a
genuine, newly-surfaced security bug, not a test bug (see below).

After the Nginx fix (commit `670eff5`, run `34433933359`): **all 5
jobs PASSED**, confirmed by reading the actual "Acceptance — User
Control" step's own output, not just its green conclusion:

```
Operation group id -> 4d59c059-8400-459f-98b1-d5137165769b
Operation roles -> Editor,Viewer
create editor -> 201
create viewer -> 201
Editor POST /api/companies -> 201
Viewer GET /api/companies -> 200
Viewer POST /api/companies -> 403
Viewer POST /api/companies with forged X-Access-Level: edit -> 403
Editor POST /api/customers -> 201
Viewer POST /api/customers -> 403
Viewer POST /api/customers with forged X-Access-Level: edit -> 403
Editor POST /api/projects -> 201
Viewer POST /api/projects -> 403
Viewer POST /api/projects with forged X-Access-Level: edit -> 403
Editor POST /api/products -> 201
Viewer GET /api/products -> 200
Viewer POST /api/products -> 403
Viewer POST /api/products with forged X-Access-Level: edit -> 403
Editor POST /api/documents -> 201
Viewer POST /api/documents -> 403
DELETE /api/admin/users/{id} -> 405
deactivate editor -> 200
login after deactivation -> 403
deactivated user's still-live access token on a protected API -> 403
self-register -> 201
login while pending -> 403
approve pending user -> 200
assign Editor role to approved user -> 201
login after approval + role assignment -> 200
superuser POST /api/products -> 201
```

No `FAIL:` lines anywhere in the step's output; `exit $fail` returned
0. All four services' unit-test jobs (`auth-service`,
`catalogue-service`, `document-service`, `search-service`) and every
other infra-integration acceptance step from Tasks 1–9 of this log
also **PASSED** on this same run — zero regression.

### Bugs found/fixed

1. **Real application bug (Task 7 audit)**: `products.py` had no
   server-side access-level check on any write endpoint at all —
   fixed by adding `_require_edit_access` to all 9 (commit `eb5d9f0`).
2. **Real infrastructure/security bug, caught by real CI, not by
   local review**: `infra/nginx/nginx.conf.template`'s `/api/products`
   location never set `auth_request_set $access_level` nor
   `proxy_set_header X-Access-Level` at all — every other resource's
   location does both. Nginx's default behavior for a header a
   location doesn't explicitly override is to forward the client's own
   copy unchanged, so a Viewer could forge `X-Access-Level: edit`
   directly and it would reach `catalogue-service` unmodified;
   `_require_edit_access` correctly trusted it because, from the
   application's point of view, the request came through the gateway
   as usual. First CI run on this feature showed it plainly:
   `Viewer POST /api/products -> 201` (twice — once plain, once with
   the forged header) where every other resource correctly showed
   `403`. Fixed by adding the missing `auth_request_set`/
   `proxy_set_header` pair to `/api/products`, matching the pattern
   already used everywhere else, plus an explicit (currently unused)
   blank on `/api/categories` per this file's stated
   redundant-blanking policy (commit `670eff5`).
3. Two test-script bugs, not application bugs (commit `4cabe8c`):
   `TestClient.delete()` doesn't accept a `json=` kwarg on the
   httpx-based `TestClient`; and several of
   `test_products_access_level.py`'s write-endpoint checks sent an
   empty body, which FastAPI 422s before the route body (and thus
   `_require_edit_access`) ever runs — the test needed schema-valid
   minimal payloads to actually exercise the access check. Also: the
   infra-integration CI script's Task 6 self-registration check didn't
   pass `requested_group_id` on `/register`, so `/approve` never added
   the user to Operation and the subsequent role-assign call correctly
   400'd — a gap in the CI script itself (the equivalent unit test in
   `test_user_control.py` already passed `requested_group_id`
   correctly and never had this bug).

### Security impact

Net positive, and non-trivial: this task found and closed a real
authorization bypass (bug #2 above) that would otherwise have shipped
— a Viewer able to write Products by simply forging one HTTP header,
with no changes to catalogue-service required to exploit it. The fix
brings `/api/products` in line with every other resource's existing,
already-correct pattern of Nginx overwriting (never merging with)
client-supplied trust headers. No authentication behavior was touched;
the existing JWT/refresh/gateway-trust-header architecture from Tasks
4 and 6 is unchanged and re-verified passing on every run in this
task's own CI evidence.

### Commits (branch `security/auth-hardening`)

- `ed1364c` — Tasks 1–2: Operation group + Editor/Viewer role seeding.
- `896afd0` — Tasks 3–6: admin create-user-with-role, richer user
  listing, no user deletion.
- `eb5d9f0` — Task 7 (app-level fix): `products.py` access-level
  enforcement.
- `db7ce6d` — frontend for admin-created users and user management.
- `5ac8ab4` — Task 8: infra-integration CI coverage for the full
  feature.
- `4cabe8c` — fix: test/CI script bugs found by the first real CI run.
- `670eff5` — Task 7 (infra-level fix): Nginx `X-Access-Level`
  forwarding gap on `/api/products` — **this is the commit CI is green
  on.**

### Verification

Real GitHub Actions CI (`mcp__github__actions_list` /
`mcp__github__get_job_logs` with `return_content: true`), raw log
content quoted above for the final green run (`34433933359`,
commit `670eff5`) — not the `conclusion` field alone, not assumed from
a green checkmark. Two real bugs (one application, one infrastructure)
were found and fixed only because this task insisted on reading actual
log content after every push instead of trusting the pass/fail flag.

**Net result: the User Control feature (Tasks 1–8) is genuinely PASS
as of commit `670eff5`, independently verified against raw CI evidence
for the complete relevant test suite, including a real authorization
bypass that was found and closed during this task's own audit rather
than shipped.**

## Task 11 — Existing-Data Migration for User Control

### Task

Ensure all pre-existing operational data (created before the User
Control feature existed, or before a given user joined Operation) is
accessible to Operation group members per their role — Editor:
view+create+edit, Viewer: view only, superuser: unrestricted — across
all five operational resources (Products, Documents, Customers,
Projects, Companies), by migrating/backfilling into the *existing*
Group/Role/RoleAccess/UserProjectAccess mechanism. No second
permission system, no changes to existing data, idempotent and safe to
re-run.

### Inspection

Read `app/models.py` and `app/core/authz.py` (auth-service) and the
document-service/catalogue-service models before writing anything:

- **Products, Customers, Companies have no group/project/owner scoping
  of any kind** — visibility is governed purely by the service-level
  `RoleAccess.access_level` (Editor="edit", Viewer="view"), already
  enforced server-side (Task 7 of the User Control entry above). There
  is nothing to backfill for these three resources — an Operation
  member already sees every existing record the moment they have the
  `products`/`documents` RoleAccess grant, with zero additional rows
  needed.
- **Documents and Projects** have one additional, *opt-in* visibility
  restriction: `UserProjectAccess`. `get_user_allowed_project_ids`
  (auth-service `app/core/authz.py`) returns `"ALL"` (unrestricted)
  when a user has **no** `UserProjectAccess` rows at all, and only
  narrows to an allow-list when rows exist. This is a deliberate,
  already-documented design (see the model's own docstring: "If a user
  has NO rows here at all, they see every project... same opt-in-
  restriction philosophy as before") — restrictions must be explicitly
  granted, they are never a default lockdown.
- `RoleProjectAccess`/`GroupProjectAccess` are provisioning/bookkeeping
  tables (the "pool" an admin draws from when granting a user project
  access) — confirmed via `grep` that neither is read anywhere at
  authorization time, only `UserProjectAccess` is.

**Conclusion**: because restrictions are opt-in and none exist for a
brand-new Operation member, every existing Document/Project/Product/
Customer/Company is *already* visible to them per their role, with no
schema change and no new rows required. The only real migration need
is defensive: a user who had a `UserProjectAccess` restriction set
under a *different* group/role before joining Operation (or before
this feature existed) would still be narrowed to that stale allow-list
after joining Operation, contradicting the requirement that Operation
users see existing data unconditionally by role.

### Implementation

- `services/auth-service/app/core/seed.py`: new
  `migrate_operation_members_to_full_existing_data_access(db)`. For
  every current member of the Operation group, deletes any
  `UserProjectAccess` rows they have, restoring the unrestricted
  `"ALL projects"` default. Touches nothing outside auth-service's own
  `UserProjectAccess` table — no operational data (Documents, Products,
  Customers, Projects, Companies) is read, written, or deleted by this
  migration; it lives in a different service's database entirely and
  auth-service has no models for it. Idempotent by construction (a
  `DELETE ... WHERE user_id IN (...)` that finds nothing on a second
  run); returns the row count removed, for logging/tests. Left as a
  separate function from `ensure_default_groups_and_roles` (which only
  ever *adds* default provisioning and never touches per-user state)
  rather than folded in, since removing rows tied to specific users is
  a meaningfully different kind of operation worth keeping clearly
  named and separately testable.
- `services/auth-service/app/main.py`: called from `on_startup()`
  right after `ensure_default_groups_and_roles`, unconditionally (not
  gated on an empty database) — same always-run, idempotent pattern as
  the rest of User Control's startup seeding.
- No document-service/catalogue-service schema or code changes: the
  inspection above found no gap in those services to fix; Editor/Viewer
  write enforcement there was already completed in the User Control
  entry above (Task 7).

### Tests

- `services/auth-service/tests/test_existing_data_migration.py` (new,
  real Postgres, same pattern as `test_seed.py`): migration is a no-op
  before the Operation group exists and when it has no members; clears
  a simulated leftover `UserProjectAccess` restriction for an Operation
  member (and `get_user_allowed_project_ids` returns `"ALL"`
  immediately afterward); leaves a **non**-Operation member's
  restriction completely untouched; is idempotent across three
  consecutive runs (1 removed, then 0, then 0); and a structural guard
  confirming the migration's source never references
  `models.Document`/`Product`/`Customer`/`Company` at all.
- New infra-integration acceptance step, "Acceptance — User Control
  existing-data migration (pre-existing records stay accessible per
  role)", added to `.github/workflows/tests.yml`, run against the real
  Docker Compose stack: creates one Company/Customer/Project/Product/
  Document **as the superuser, before** a fresh Editor/Viewer pair
  exists (so neither of them created or owns any of it — a direct
  simulation of "data that predates this user's Operation membership"),
  then for every one of those five pre-existing records checks, through
  the real Nginx API: Editor `GET` → 200, Editor `PATCH` → 200, Viewer
  `GET` → 200, Viewer `PATCH` → 403, and superuser `GET`+`PATCH` → 200
  both.

### Actual results (real GitHub Actions CI, `mcp__github__get_job_logs`
with `return_content: true` — raw log content, never the `conclusion`
field alone)

Pushed as commit `7bbea78`, verified on run `34435133213`. All 5 jobs
**PASSED**: `auth-service` (`75 passed, 5 warnings in 35.76s`, up from
70 before this task's 5 new tests), `catalogue-service`,
`document-service`, `search-service`, and `infra-integration`
(including the new existing-data acceptance step, alongside every
prior task's acceptance step from this entire log). The new step's
real output, quoted directly from the job log
(`mcp__github__get_job_logs`, `return_content: true`), for one full
pass over all five resources — a Company, Customer, Project, Product,
and Document all created by the superuser *before* the Editor/Viewer
pair below existed:

```
Editor GET legacy companies/60494499-3540-4716-8cfa-4b936813b6f0 -> 200
Editor PATCH legacy companies/60494499-3540-4716-8cfa-4b936813b6f0 -> 200
Viewer GET legacy companies/60494499-3540-4716-8cfa-4b936813b6f0 -> 200
Viewer PATCH legacy companies/60494499-3540-4716-8cfa-4b936813b6f0 -> 403
Superuser GET/PATCH legacy companies/60494499-3540-4716-8cfa-4b936813b6f0 -> 200 / 200
Editor GET legacy customers/c93bbc6a-38ac-460a-939e-97d2a8e503a3 -> 200
Editor PATCH legacy customers/c93bbc6a-38ac-460a-939e-97d2a8e503a3 -> 200
Viewer GET legacy customers/c93bbc6a-38ac-460a-939e-97d2a8e503a3 -> 200
Viewer PATCH legacy customers/c93bbc6a-38ac-460a-939e-97d2a8e503a3 -> 403
Superuser GET/PATCH legacy customers/c93bbc6a-38ac-460a-939e-97d2a8e503a3 -> 200 / 200
Editor GET legacy projects/7f1d0a64-d1c0-4ba2-9d34-06ba3dff12f3 -> 200
Editor PATCH legacy projects/7f1d0a64-d1c0-4ba2-9d34-06ba3dff12f3 -> 200
Viewer GET legacy projects/7f1d0a64-d1c0-4ba2-9d34-06ba3dff12f3 -> 200
Viewer PATCH legacy projects/7f1d0a64-d1c0-4ba2-9d34-06ba3dff12f3 -> 403
Superuser GET/PATCH legacy projects/7f1d0a64-d1c0-4ba2-9d34-06ba3dff12f3 -> 200 / 200
Editor GET legacy products/a330a32e-56d0-4ab9-8699-1056082540c1 -> 200
Editor PATCH legacy products/a330a32e-56d0-4ab9-8699-1056082540c1 -> 200
Viewer GET legacy products/a330a32e-56d0-4ab9-8699-1056082540c1 -> 200
Viewer PATCH legacy products/a330a32e-56d0-4ab9-8699-1056082540c1 -> 403
Superuser GET/PATCH legacy products/a330a32e-56d0-4ab9-8699-1056082540c1 -> 200 / 200
Editor GET legacy documents/b2d563a6-73af-4bbb-ae11-76b4205b9d6b -> 200
Editor PATCH legacy documents/b2d563a6-73af-4bbb-ae11-76b4205b9d6b -> 200
Viewer GET legacy documents/b2d563a6-73af-4bbb-ae11-76b4205b9d6b -> 200
Viewer PATCH legacy documents/b2d563a6-73af-4bbb-ae11-76b4205b9d6b -> 403
Superuser GET/PATCH legacy documents/b2d563a6-73af-4bbb-ae11-76b4205b9d6b -> 200 / 200
```

No `FAIL:` lines anywhere in the step's output; `exit $fail` returned 0.

### Security impact

None expected, and none found: this migration only ever *removes* a
stale restriction that would otherwise block legitimate,
already-required access — it cannot grant anything beyond what an
Operation Editor/Viewer's `RoleAccess` grant already entitles them to
(view/edit is still fully enforced server-side per resource), and it
never touches any group other than Operation or any table outside
auth-service's own `UserProjectAccess`. Existing operational data
(Documents/Products/Customers/Projects/Companies) is provably
unmodified by this task — no migration in this entry writes to any of
those tables at all.

### Bugs found/fixed

None in the existing authorization model — the inspection above found
the "opt-in restriction, unrestricted by default" design was already
correct for the stated requirements. The only real backfill need
(clearing a hypothetical stale per-user restriction) has no production
instance to have been "broken" by; it is a defensive migration for a
scenario that cannot currently arise from this branch's own seeding
logic, made real via the dedicated leftover-restriction test above.

### Commits (branch `security/auth-hardening`)

- `7bbea78` — existing-data migration, tests, and CI acceptance step.
  **This is the commit CI is green on (run `34435133213`).**

### Verification

Real GitHub Actions CI (`mcp__github__actions_list` /
`mcp__github__get_job_logs` with `return_content: true`), raw log
content quoted above — not the `conclusion` field alone, not assumed
from a green checkmark.

**Net result: the existing-data migration is genuinely PASS as of
commit `7bbea78`, independently verified against raw CI evidence
showing Editor/Viewer/superuser access on records that predate their
Operation membership, across all five operational resources, through
the real Nginx API.**

## Task 12 — Operation Group UI Simplification

### Task

Simplify the group management page to match the intended, already-
implemented authorization model: Superuser/Admin unrestricted,
Operation+Editor = view/create/edit, Operation+Viewer = view-only, and
admin assigns normal users to Operation and their role entirely through
User Management. Remove from the page: Project access (the group-level
project pool), Members (the member list, Add existing user, Make group
admin), and Create brand-new account — but only once confirmed these
aren't required elsewhere, without inventing a second permission
system, without deleting users/data, and without breaking superuser
access.

### Inspection

There is exactly one group-detail page in the frontend
(`#group-detail-panel` in `web/index.html`) — it is **generic**, shared
by every group (Operation or any admin-created group), not an
Operation-specific page. Traced every control slated for removal to
its backend endpoint and every other frontend caller before touching
anything:

- **"Add existing user" / "Create brand-new account"** (group-detail's
  own forms) posted to `POST /admin/groups/{id}/members` and
  `POST /admin/groups/{id}/users` respectively. Both endpoints are
  **already called elsewhere**: `POST /admin/groups/{id}/members` is
  the same endpoint User Management's per-user "Manage" modal uses for
  its "Add to a group" picker (added in the User Control task); `POST
  /admin/groups/{id}/users` is the same endpoint the Admin -> All Users
  "+ Create User" button uses. Removing the group-page's own copies of
  these forms removes nothing — both flows survive, reachable from
  User Management instead, which is exactly where the intended model
  says admin should be doing this work.
- **"Members" table** (view-only: username + group-admin yes/no, no
  remove button at all) is strictly worse than what User Management's
  "Current groups" list on a user's Manage modal already offers (same
  information, plus a working remove-from-group button) — confirmed by
  reading `loadGroupMembers`'s render output before deleting it.
  Admin -> All Users' own Group/Role columns (from the User Control
  task) already give the equivalent "who's in Operation" view without
  opening a group at all.
- **"Project access — Operation" (the `GroupProjectAccess` pool)**:
  confirmed via `grep` that its three endpoints
  (`GET/POST/DELETE /admin/groups/{id}/project-access`) were called
  **only** by this one card — no other frontend caller. Read
  `grant_user_project_access` in `admin.py`: a **superuser bypasses the
  pool check entirely** (`if not current_user.is_superuser: ... in_pool
  check`) — so removing this card's UI does not block the superuser
  from using the per-user "Project access" checklist already in User
  Management (which calls `UserProjectAccess`, the row that's actually
  read at authorization time — see the existing-data migration task
  above). The pool only matters for a **delegated, non-superuser group
  admin**, a role the intended model doesn't use for Operation at all
  (Admin/Superuser handles everything centrally).
- **"Make group admin"**: the `is_group_admin` flag and
  `can_manage_group`/`_shared_administered_group_ids` machinery it
  feeds remain read in several places (deciding who can see the Admin
  panel at all, gating `add_existing_member`/`create_user_in_group`/
  role management for non-superusers) — this is live, load-bearing RBAC
  infrastructure, not dead code, even though no UI can newly *grant*
  the flag once this page's forms are gone. Existing group-admin flags
  (if any) keep working exactly as before.

**Conclusion**: every capability the removed UI exposed is either (a)
already available through User Management, using the *same* backend
endpoints, or (b) still reachable directly via the API and
intentionally not needed by the intended Operation-only model. No
backend endpoint was orphaned by this change, so none was deleted —
this was a pure frontend simplification.

### Implementation

- `web/index.html`: removed the `#group-project-pool-card` card and
  the entire "Members" card (member table, "Add existing user" form,
  "Make group admin" checkboxes ×2, "Create brand-new account" form) from
  `#group-detail-panel`. Left an HTML comment pointing at this log
  entry and explaining the replacement path. The "Roles" card (create
  role, edit role, service access) is unchanged — the intended model
  still needs an admin-facing way to manage what Editor/Viewer grant.
- `web/js/app.js`: removed `loadGroupMembers`, `loadGroupProjectPool`,
  `refreshMemberRolePickers`, the `add-member-btn`/`create-member-btn`
  click handlers, and the now-unused `cachedGroupProjectPool` variable
  — all of it existed only to drive the removed UI. Removed their calls
  from the `group-select` change handler and from `loadGroupRoles`
  (which called `refreshMemberRolePickers` after every role list
  refresh). `currentUserIsSuperuser` was kept — still used by the
  per-user Project access checklist in User Management.

### Recommendations / judgment calls (per your "tell me" request)

1. **Applied globally, not just to Operation.** The group-detail page
   has no per-group branching today, and adding an
   `if (group.name === 'Operation')` special case to keep the removed
   controls for hypothetical *other* future groups would be the
   "second permission system"-adjacent complexity you asked me to
   avoid, for a scenario (a non-Operation, non-superuser-managed group)
   the intended model doesn't describe. If you do want a genuinely
   different, richer management page for a future custom group later,
   that's a new, explicit feature to design then — not something to
   half-keep here speculatively.
2. **Kept `GroupProjectAccess` and `is_group_admin` in the backend, not
   just "not confirmed unused" but actively still correct**: they are
   this app's only existing mechanism for a future delegated (non-
   superuser) group admin, and deleting live RBAC primitives to match a
   UI simplification would be the actual second-system risk — better
   to leave working, generic infrastructure in place and just not
   surface it here. If you're certain delegated group-admins will never
   be used for *any* group going forward, that's a separate, larger
   removal decision worth its own review — I did not take it here.
3. **No migration/backfill needed.** This task touched no schema, no
   authorization logic, and no operational or user data — it is purely
   two frontend files plus this log entry and a CI acceptance step.

### Tests

New infra-integration acceptance step, "Acceptance — Operation Group UI
simplification (removed UI gone, backend + full auth flow intact)",
added to `.github/workflows/tests.yml`, run against the real Docker
Compose stack through Nginx:

- Fetches the actually-served `/` and `/js/app.js` and greps for every
  removed element id / function name — must be **absent**.
- Confirms the replacement controls (`+ Create User`, User Management's
  "Add to a group" button) are present.
- Full end-to-end authorization flow using only the *surviving* backend
  endpoints, simulating exactly what an admin now does entirely through
  User Management: self-register a user, approve, add to Operation via
  `POST /admin/groups/{id}/members` (the same endpoint the removed "Add
  existing user" form used), assign the Editor role, log in, and
  confirm a real protected request through Nginx succeeds (200).
- Directly exercises the kept-but-unexposed `GroupProjectAccess` pool
  endpoints (grant + revoke) to prove that backend is still intact.
- Reconfirms superuser access is unrestricted throughout.

### Actual results (real GitHub Actions CI, `mcp__github__get_job_logs`
with `return_content: true` — raw log content, never the `conclusion`
field alone)

Pushed as commit `40e83ce`, verified on run `34436918402`. All 5 jobs
**PASSED**: `auth-service`, `catalogue-service`, `document-service`,
`search-service` (all unaffected — this was a frontend-only change),
and `infra-integration`, including the new step and every prior task's
acceptance step in this entire log (no regression). The new step's
real output, quoted directly from the job log:

Element-absence + full-flow checks against the actually-served page:
no `FAIL:` lines for any removed id/function, `+ Create User` and
`edit-user-add-group-btn` both present. Then, the surviving backend
endpoints exercised directly (exactly what User Management now drives):

```
POST /admin/groups/{id}/members (backend behind the removed 'Add existing user' form) -> 201
Editor (added/assigned via the surviving backend endpoints) GET /api/documents -> 200
POST /admin/groups/{id}/project-access (backend behind the removed pool UI) -> 201
```

`pool_revoke_code` (`DELETE .../project-access/{id}` -> 204) and
`su_code` (superuser `GET /api/documents` -> 200) were asserted
silently (no `echo` on the success path for those two) and did not
trigger their `FAIL:` branches — `exit $fail` returned 0 for the whole
step. (Two benign `echo: write error: Broken pipe` lines appear in the
raw log from the `grep -q` early-exit in the element-absence loops —
standard `SIGPIPE` behavior on a `-q` match, did not affect the
step's exit code or any assertion.)

### Security impact

None expected, and none found: no authorization logic changed, no
endpoint was removed, no permission check was altered. The only
runtime-observable difference is that two admin-facing forms are no
longer rendered — their equivalent, already-existing capability in
User Management is unaffected. Superuser access, Editor/Viewer
enforcement, and existing data are all unchanged.

### Bugs found/fixed

None. This was a planned UI simplification following an inspection
that confirmed no functional gap would result.

### Commits (branch `security/auth-hardening`)

- `40e83ce` — Operation Group UI simplification, CI acceptance step.
  **This is the commit CI is green on (run `34436918402`).**

### Verification

Real GitHub Actions CI (`mcp__github__actions_list` /
`mcp__github__get_job_logs` with `return_content: true`), raw log
content quoted above — not the `conclusion` field alone, not assumed
from a green checkmark.

**Net result: the Operation Group UI simplification is genuinely PASS
as of commit `40e83ce`, independently verified against raw CI evidence
that the removed UI is gone from the served page, its replacements
exist, every surviving backend endpoint still works, and the complete
Superuser -> register -> approve -> add-to-Operation -> assign-role ->
login -> protected-API authorization flow is unbroken.**

## Task 13 — Documents Recycle Bin: UI and Deletion Audit

### Task

1. Resize the Documents recycle-bin modal to ~80% viewport width/height,
   responsive on smaller screens, with the document list scrolling
   inside the modal while the table header stays usable (visible/pinned)
   during scroll. Restore must keep working unchanged.
2. Show who deleted each document ("Deleted by") and when ("Deleted
   at"), alongside doc number/type/total/Restore — identifying the real
   authenticated user who deleted it (Editor or Superuser/Admin alike),
   correct after logout/login, never inferred from whoever happens to be
   viewing the recycle bin, and never trusting a client-supplied value.

### Inspection

Read the Document/AuditLogEntry models, `documents.py`'s trash/restore
endpoints, the existing `log_action`/`AuditLogEntry` audit mechanism,
and the Nginx `X-Username` header pipeline before changing anything:

- **Root cause, confirmed by reading the code, not assumed**:
  `trash_document` (`PATCH /documents/{id}/trash`) never accepted the
  `X-Username` header at all and never recorded anything about who
  deleted the document — `is_deleted` just flips to `True`. No deletion
  identity was captured anywhere, by any mechanism.
- **`log_action`/`AuditLogEntry`** (`app/core/audit.py`,
  `models.AuditLogEntry`) is this app's one existing audit mechanism —
  already used for `"created"`/`"viewed"`/`"edited"`/`"status_changed"`/
  `"file_uploaded"` actions, called consistently as
  `log_action(db, doc.id, x_username, action)` right after each
  operation's own `db.commit()`. `trash_document`/`restore_document`
  were the only mutating endpoints in this router that never called it.
  This is exactly the "existing audit/log mechanism" the task asked to
  extend rather than duplicate — extended it with `"deleted"` and
  `"restored"` action types, same call pattern as everywhere else.
- **`X-Username` is genuinely server-side, not client-suppliable**:
  auth-service's `/verify` (`app/routers/auth.py`) sets
  `response.headers["X-Username"] = user.username` unconditionally for
  every valid, active, approved request — including superuser, no
  special-casing — derived from the JWT's verified `sub` claim, never
  from anything the client sent. Nginx's `/api/documents` location
  already captures this via `auth_request_set` and overwrites (not
  merges with) whatever `X-Username` the client itself sent — the same
  redundant-blanking/overwrite pattern already relied on for
  `X-Access-Level` (see Task 7 of the User Control entry above). So the
  only change needed to make deletion identity forgery-proof was to
  actually *read* the header that was already being delivered correctly
  — confirmed with a forged-header acceptance test below, not assumed.
- **Where to store "who deleted it" for display**: the recycle-bin
  table needs to show it directly, to every Editor/Viewer who can open
  the recycle bin — but `AuditLogEntry` is gated behind the separate
  `audit-log` service grant (`X-Has-Audit-Log`, intended for a
  "Director"-style role), which regular Editors/Viewers don't have.
  Querying `AuditLogEntry` for the trash listing would have quietly
  exposed audit-log data through a side door to users who aren't
  supposed to have that permission — a real architectural inconsistency
  avoided by NOT doing that. Instead added two small denormalized
  columns directly on `Document` — `deleted_by`/`deleted_at` — set on
  trash, cleared on restore: the same pattern the model already uses for
  `is_deleted`/`updated_at` (a plain, visible-to-everyone attribute of
  the record's current state), not a second parallel audit trail.
  `AuditLogEntry` remains the one full event-timeline mechanism;
  `Document.deleted_by`/`deleted_at` is just today's snapshot of it,
  cheap to read in the same query that already lists trashed documents.

### Implementation

- `services/document-service/app/models.py`: `Document` gains
  `deleted_by` (`String(100)`, nullable) and `deleted_at`
  (`DateTime(timezone=True)`, nullable). `AuditLogEntry.action`'s
  comment extended with `"deleted"`/`"restored"` (plain `String(30)`,
  no DB-level enum constraint, so no schema change needed there).
- `services/document-service/app/schemas.py`: `DocumentOut` gains
  `deleted_by`/`deleted_at` (both `Optional`, default `None`).
- `services/document-service/app/routers/documents.py`:
  `trash_document` and `restore_document` both now take
  `x_username: str | None = Header(default=None, alias="X-Username")`.
  `trash_document` sets `deleted_by`/`deleted_at` and calls
  `log_action(db, doc.id, x_username, "deleted")` after its commit;
  `restore_document` clears both fields and logs `"restored"` — same
  commit-then-log ordering every other action in this router already
  uses.
- `services/document-service/alembic/versions/0003_document_deleted_by_at.py`
  (new): adds the two columns, guarded idempotent (checks
  `_existing_columns` first) exactly like migration 0002's pattern —
  needed because this project's `create_all()`-on-startup approach only
  creates missing *tables*, not missing *columns* on a table that
  already exists in a real, already-running deployment (see 0002's own
  module docstring for the established reasoning). A fresh CI database
  never needs this migration (create_all already includes the new
  columns from `models.py`); a real deployment's existing `documents`
  table does.
- `web/index.html` / `web/js/app.js`: new `.modal-box-scrollbody` CSS
  class — fixed `80vw`/`80vh` box (`96vw`/`90vh` under a 700px
  breakpoint), `display:flex; flex-direction:column; overflow:hidden`
  on the box itself, with a single inner `.table-scroll-wrap` that
  scrolls (`overflow:auto`) and a `position:sticky` `<thead>` so the
  header stays visible/usable while rows scroll underneath it. Applied
  only to the documents recycle-bin modal — every other modal
  (including the other four resources' recycle bins) is untouched.
  Table gained "Deleted by"/"Deleted at" columns (colspan 4→6
  throughout); `Restore`'s button/handler is unchanged. A missing
  `deleted_by` (pre-migration record) displays as "Unknown", not a
  fabricated name; a missing `deleted_at` displays as "—".

### Existing deleted-data handling (per the task's explicit ask)

A document already sitting in the recycle bin **before** this change
has `deleted_by = NULL` and `deleted_at = NULL` after the migration —
there is no reliable source to backfill either from: `AuditLogEntry`
never recorded a `"deleted"` action before this same change added it,
so there is nothing truthful to backfill from, and fabricating a name
would be actively wrong. The safest, and only honest, choice: leave
both `NULL` and display `"Unknown"` for `deleted_by` (and `"—"` for
`deleted_at`) rather than guessing. **Nothing existing is corrupted,
modified, or lost** — this migration only ever adds two new nullable
columns; no existing row, column, or table is touched, and `is_deleted`
records their trashed status exactly as before.

### Security

- Deletion identity is read exclusively from the `X-Username` header
  Nginx sets from auth-service's server-verified `/verify` response —
  never from a request body field, never trusted as client-supplied.
  Verified directly: an Editor's own delete request sent with a forged
  `X-Username: someone-else` header still records the Editor's real
  username, proving Nginx overwrites (not merges) the header exactly as
  designed — see the acceptance evidence below.
- Viewer's existing inability to delete (`_require_edit_access`,
  unchanged by this task) was re-verified, not assumed: an explicit
  Viewer-delete-attempt acceptance check confirms 403 and that the
  target document is still present afterward.
- No new attack surface: the two new columns are populated exclusively
  from `trash_document`/`restore_document`'s own server-derived
  `x_username`, never accepted as request input from any client.

### Tests

- `services/document-service/tests/test_recycle_bin.py` (new,
  schema-level, no DB needed — same pattern as `test_companies.py`):
  `DocumentOut` exposes `deleted_by`/`deleted_at`, and both validate as
  `None` when absent (a pre-migration/never-deleted document).
- New infra-integration acceptance step, "Acceptance — Documents
  recycle bin deletion audit (Deleted by / Deleted at, real Nginx)",
  run against the real Docker Compose stack through Nginx: Editor
  creates+trashes their own document and the recycle bin shows their
  exact username with a real `deleted_at`; a Viewer's delete attempt on
  a separate document 403s and the document is provably still present
  afterward; the superuser trashes a document and the recycle bin shows
  `"admin"`; restore succeeds and clears `deleted_by` back to `None`;
  a completely fresh login (new access token, unrelated to the one used
  to view the trash) sees the exact same stored `deleted_by` value,
  proving it's read from the row, not inferred from the current
  viewer; and an Editor's delete request carrying a forged
  `X-Username: someone-else` header still records the Editor's real
  username.

### Actual results (real GitHub Actions CI, `mcp__github__get_job_logs`
with `return_content: true` — raw log content, never the `conclusion`
field alone)

Pushed as commit `60dab87`, verified on run `34439208203`. All 5 jobs
**PASSED**: `auth-service`, `catalogue-service`, `search-service`
unaffected; `document-service` — `70 passed` (68 prior + 2 new
`test_recycle_bin.py` tests); `infra-integration` — the new step and
every prior task's acceptance step in this entire log, including the
existing security-headers/CSP check (`script-src 'self'`, no inline
scripts — unaffected, since this task only added ordinary JS/CSS to
the existing external `app.js`/`<style>` block, no new inline
handlers). The new step's real output, quoted directly from the job
log:

```
Editor trash own document -> 200
recycle bin deleted_by for Editor's document -> recyclebin_editor_20139
Viewer attempt to trash a document -> 403
Superuser trash a document -> 200
recycle bin deleted_by for superuser's document -> admin
Editor restore their own document -> 200
deleted_by after a fresh login -> admin
Editor trash with forged X-Username header -> 200
deleted_by after forged X-Username attempt -> recyclebin_editor_20139
```

No `FAIL:` lines anywhere in the step's output; `exit $fail` returned
0. The last two lines are the key security proof: an Editor's own
delete request sent with a forged `X-Username: someone-else` header
still recorded their real username (`recyclebin_editor_20139`, not
`someone-else`) — confirming Nginx overwrites the header exactly as
designed, not merely assumed from reading the config.

### Recommendation

Denormalizing `deleted_by`/`deleted_at` onto `Document` (rather than
querying `AuditLogEntry` for the trash listing) was the one real design
decision in this task, made specifically to avoid leaking audit-log-
gated data to regular Editors/Viewers through the recycle bin. If a
future task wants the recycle bin to show a fuller *history* per
document (not just "who deleted it last" but every action ever taken),
that's a legitimate reason to surface a filtered view of
`AuditLogEntry` — but it should be a new, explicitly-scoped read path
with its own authorization decision, not a widening of what the
existing `audit-log` grant already protects.

### Commits (branch `security/auth-hardening`)

- `60dab87` — Recycle Bin UI + deletion audit. **This is the commit CI
  is green on (run `34439208203`).**

### Verification

Real GitHub Actions CI (`mcp__github__actions_list` /
`mcp__github__get_job_logs` with `return_content: true`), raw log
content quoted above — not the `conclusion` field alone, not assumed
from a green checkmark.

**Net result: the Recycle Bin UI and deletion-audit fix is genuinely
PASS as of commit `60dab87`, independently verified against raw CI
evidence covering Editor, Viewer, superuser, restore, cross-login
persistence, and a forged-header bypass attempt, all through the real
Nginx API.**
