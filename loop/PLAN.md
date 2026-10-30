# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **10** |
| Goal | Sessions end: **30 min idle timeout** (with warning), **10 h max session**, admin **active sessions + force logout**, user **log out all devices** |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## Problem
`services/auth-service`: access JWT 15 min (memory only, good); refresh token
in an HttpOnly cookie, 14 days, **rotated on every /refresh with a fresh 14-day
expiry**. So a user who opens the app once every 14 days never logs in again.
The user wants sessions to end.

Already in place (keep, don't rewrite): HttpOnly + SameSite cookie, hashed
refresh tokens, single-use rotation, replay → revoke all
(`app/core/refresh_tokens.py`), revoke all on admin password change / deactivate
(`app/routers/admin.py` ~lines 359, 431). Verify these still work; don't redo them.

## Rules (all values in `app/core/config.py` + `.env.example`, overridable by env)
| Setting | Default | Meaning |
|---|---|---|
| `session_idle_timeout_minutes` | 30 | No user activity for this long → session ends |
| `session_max_hours` | 10 | Hard limit from the **original login**, activity doesn't extend it |
| `session_idle_warning_seconds` | 60 | UI warning shown this long before idle logout |

**Activity** = the user doing something in the UI (click, key, scroll,
mouse move, throttled), **not** background timers or polling. This matters:
the front end must not refresh tokens just because a tab is open.

## Backend (auth-service)
1. **Session identity**: each login starts a session. Add to `refresh_tokens`:
   `session_id` (UUID, same for every token rotated from one login),
   `session_started_at` (copied through rotations), `last_activity_at`,
   `user_agent` (String 255), `ip` (String 64). Rotation copies
   `session_id` / `session_started_at` / `user_agent`, and sets `last_activity_at = now`.
   - auth-service uses `create_all` (no Alembic), which does **not** add columns
     to an existing table. Add a small idempotent startup step that
     `ALTER TABLE refresh_tokens ADD COLUMN IF NOT EXISTS …` for each column (Postgres).
     Existing rows have NULLs; treat a NULL `session_started_at` as **expired**,
     so old 14-day cookies stop working once (everyone logs in once after the update).
     Say so in the report.
2. **Refresh token expiry** = `min(now + idle_timeout, session_started_at + max_hours)`.
   Set the cookie's `max_age` to the same, so the browser drops it too.
3. `/refresh` rejects (401, cookie cleared) when idle or max is exceeded. Use the
   existing "token expired" path.
4. **Access token** expiry must not outlive the session: `exp = min(now + 15 min, session max end)`.
   Put `sid` (session_id) in the JWT. `/verify` (the gateway's auth_request) rejects
   tokens whose session is revoked: check that the session has an un-revoked token
   row. Keep it a single indexed query; `/verify` runs on every request.
5. **Endpoints** (under the existing auth / admin routers, same auth patterns):
   - `GET  /api/auth/sessions`: the current user's active sessions
     (id, started, last activity, user agent, ip, `current: bool`).
   - `POST /api/auth/logout-all`: revoke every session of the current user (including this one).
   - `GET  /api/admin/users/{id}/sessions`: an admin's view of a user's sessions.
   - `POST /api/admin/users/{id}/sessions/{session_id}/revoke` and
     `POST /api/admin/users/{id}/logout-all`.
   - Admin scoping: same rules as the existing user-management endpoints
     (superuser: anyone; group admin: only members of groups they administer).
   - Check nginx routes them through the existing `/api/auth` and `/api/admin` locations.
6. **Self password change** (if there is a user-facing one): revoke all *other*
   sessions and keep the current one. If there isn't one, note it, don't add one.
7. The rate limiter, login flow and error messages are otherwise unchanged.

## Frontend (`web/js/app.js`, `web/index.html`)
1. **Idle tracking**: record the last activity time on user input events (throttled to
   ~1 per 10 s). Silent token refresh (`refreshAccessToken`) only runs as today,
   on a 401 or an expiring token, **and only if there was activity within the
   idle window**. Otherwise go to logout.
2. **Warning dialog** `session_idle_warning_seconds` before the idle end: "You'll be
   logged out in 60 s due to inactivity", with a countdown and **Stay logged in**
   (counts as activity and refreshes) / **Log out**. On timeout → call
   `/auth/logout`, clear the in-memory token, show the login screen with the
   message "Logged out after 30 minutes of inactivity".
3. **Max session**: when `/refresh` fails because the 10 h limit is hit → login
   screen with "Your session expired, please log in again". Don't lose unsaved
   form data silently: if a document/product modal is open and dirty, the warning
   dialog says so.
4. **Multiple tabs**: activity in one tab counts for all tabs (use `localStorage`
   storage events for the **timestamp only**, never tokens), so a user working in
   tab A isn't logged out by idle tab B. Logout in one tab logs out all tabs.
5. The idle/max values come from the server: add them to the login / refresh
   response (e.g. `session: {idle_timeout_s, warning_s, expires_at}`) so they
   aren't hard-coded in JS.
6. **"My sessions"**: in the user menu, a small view listing the user's sessions
   (browser from user agent, IP, started, last active, "this device") with a
   **Log out all devices** button.
7. **Admin**: in the existing user management screen, a **Sessions** button per
   user, opening the list with **Revoke** per session and **Log out everywhere**.
8. Existing UI style; no new libraries.

## Tests
- `services/auth-service/tests/test_sessions.py` (follow `test_refresh_tokens.py`;
  use time injection / freezing instead of sleeping):
  1. Refresh within 30 min keeps working; after 30 min idle → 401.
  2. Active every 20 min → still ends at 10 h from login.
  3. Rotation keeps `session_id` / `session_started_at`; the cookie max-age equals the computed expiry.
  4. Access token `exp` ≤ session max end; contains `sid`.
  5. `/verify` rejects an access token after its session is revoked (logout-all, admin revoke).
  6. Pre-existing token rows with NULL `session_started_at` are rejected.
  7. `GET /sessions` lists only own sessions and flags the current one; logout-all ends all.
  8. Admin scoping: a group admin can't see or revoke sessions of users outside their groups (403).
  9. The existing replay / password-change / deactivate revocations still pass.
  10. Startup `ADD COLUMN IF NOT EXISTS` runs twice safely.
- Smoke (gateway): login → `/auth/sessions` shows 1 → `/auth/logout-all` → old access token gets 401 on `/api/documents`.
- UI (Playwright), with the stack started using **short test values** (e.g. idle 20 s,
  warning 10 s, max 60 s via env, test stack only):
  1. Idle → the warning appears → it auto-logs out with the inactivity message.
  2. Clicking **Stay logged in** keeps the session.
  3. Activity in tab A keeps tab B logged in; logout in A logs out B.
  4. Max limit reached while active → the "session expired" message.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | New tests pass | `pytest services/auth-service/tests/test_sessions.py -v` |
| A2 | No regressions | all service unit suites pass |
| A3 | Smoke passes | `./scripts/verify.sh` exits 0 (normal default values) |
| A4 | UI proof | `loop/evidence/iter-10-idle-warning.png`, `iter-10-logged-out.png`, `iter-10-my-sessions.png`, `iter-10-admin-sessions.png` |
| A5 | Performance | `/verify` timing before vs after (e.g. 200 requests through the gateway, average ms) in the report; the added cost should be small |
| A6 | Security notes | A short section in the report listing what an attacker with a stolen cookie can and can't do now, plus anything left open |
