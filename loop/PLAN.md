# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **9** |
| Goal | Browsers always load the newest `index.html` / `js/app.js` after an update, with **no hard refresh needed** |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## Problem (seen by the user)
After pulling iteration 7 and rebuilding, the New document form showed the new
"Saved customer / Enter manually" radios (new `index.html`), but clicking
"Enter manually" did nothing: the browser was still running the **old cached
`js/app.js`**, so the manual fields never appeared until a hard refresh
(Ctrl+Shift+R).

Cause: `infra/nginx/nginx.conf.template` serves `web/` (`location / { try_files … }`,
volume `../web:/usr/share/nginx/html:ro`) with **no `Cache-Control` header**,
so browsers apply heuristic caching from `Last-Modified` and can reuse stale
files for hours or days.

## Tasks
1. In `nginx.conf.template`, make the static UI files revalidate on every load:
   - `index.html` and everything under `/js/` (plus any `.css` served from `web/`):
     `Cache-Control: no-cache` (the browser may keep a copy but must check
     with the server first; nginx's ETag / Last-Modified make that a cheap 304).
   - **nginx `add_header` gotcha**: `add_header` in a location replaces inherited
     ones, and `location /` already does `include /etc/nginx/security-headers.conf;`.
     Every location that serves these files must still send **all** the security
     headers plus the new `Cache-Control`. Check this with `curl -sI`.
   - Don't change caching for `/api/*` or presigned MinIO downloads.
2. Cache-bust the script URL too, so users with an **already cached** old
   `app.js` pick up the new one on their next normal page load:
   `<script src="/js/app.js?v=…">` in `web/index.html`. Use the smallest approach
   that works without a build step. One option: nginx `sub_filter` replacing a
   placeholder with a value that changes when the files change. A manually bumped
   `?v=` constant is not acceptable, because people will forget to bump it. Explain the choice in the report.
3. `scripts/verify.sh`: after `docker compose up -d --build`, run
   `docker compose restart nginx` (iteration 8 report §7, fix (a)). Add a
   `.dockerignore` to each service ignoring `__pycache__`, `.pytest_cache`,
   `tests/` (fix (c)).
4. Add the same restart step to `docs/operations.md` / deployment docs, with the
   server update commands:
   `git pull` → `docker compose -f infra/docker-compose.yml up -d --build` →
   `docker compose -f infra/docker-compose.yml restart nginx`.

## Tests
- Smoke (`tests/test_health.py` or a new `tests/test_static_caching.py`), through the gateway:
  1. `GET /` and `GET /js/app.js` → `Cache-Control` contains `no-cache`, plus an
     `ETag` or `Last-Modified`.
  2. The same responses still carry every security header the current config sends.
     Read the list from `security-headers.conf` instead of hard-coding it.
  3. Conditional GET with the returned `ETag` → 304.
  4. The `index.html` script tag has a version query that changes when `app.js`
     changes. Prove it by touching or modifying `web/js/app.js` in the test stack,
     reloading, and checking that the value changed. Restore the file afterwards.
  5. `GET /api/...` responses are unchanged (no new `Cache-Control` added by this change).
- UI (Playwright): load the app, change `web/js/app.js` on disk (e.g. append a
  harmless `window.__cacheProbe = 2;`), reload normally (**not** a hard reload),
  check that `window.__cacheProbe === 2`, then restore the file.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Headers | `curl -sI http://localhost:8080/` and `/js/app.js` pasted in the report: `Cache-Control: no-cache` plus all security headers |
| A2 | Normal reload picks up new JS | Playwright test passes |
| A3 | No regressions | all service unit suites pass |
| A4 | Smoke passes | `./scripts/verify.sh` exits 0 |

## Open question for the user (don't block on it)
Manual customer form: the user listed only Customer Name, Organization and
Address. Phone and Email stay for now, until the user decides.
