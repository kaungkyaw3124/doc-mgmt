# Security Hardening Development Log

Branch: `security/auth-hardening` (based on the testing branch `security/auth-hardening`, formerly `claude/security-hardening-testing`), to be merged to `main` once all tasks pass.

| Task | Goal | Status | Commit |
|---|---|---|---|
| 1 | Login rate limiting | PASS | bc3610f |
| 2 | Remove insecure default secrets | NOT STARTED | - |
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
