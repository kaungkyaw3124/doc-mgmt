# Security Hardening Development Log

Branch: `security/auth-hardening` (based on the testing branch `security/auth-hardening`, formerly `claude/security-hardening-testing`), to be merged to `main` once all tasks pass.

| Task | Goal | Status | Commit |
|---|---|---|---|
| 1 | Login rate limiting | PASS | bc3610f |
| 2 | Remove insecure default secrets | PASS | 59d09ce |
| 3 | Remove host exposure of internal datastores | NOT STARTED | - |
| 4 | Authenticate gateway trust headers | NOT STARTED | - |
| 5 | Secure uploaded content type | NOT STARTED | - |
| 6 | JWT storage and rotation | NOT STARTED | - |
| 7 | CORS policy | NOT STARTED | - |
| 8 | Security headers / Nginx hardening | NOT STARTED | - |

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
