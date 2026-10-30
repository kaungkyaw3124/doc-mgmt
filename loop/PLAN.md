# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **11** |
| Goal | Logging out wipes **all** of the previous user's UI state, so the next user who logs in sees only their own menu and data |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## Bug (reported by the user, with screenshots)
1. Logged in as `admin` (superuser), on the Admin page.
2. Signed out, then logged in as `KTZH` (Operation group, Editor role, not an admin)
   **without reloading the page**.
3. KTZH saw the **Admin** menu item and the Admin page with the full user list
   still on screen. After a browser refresh, it was gone.

Cause (`web/js/app.js`): `doLogout()` (~line 235) only hides `#app` and shows
the login screen. `enterApp()` (~line 259) only **shows** things the new user
may see (`admin-nav-item`, `groups-card`, …) and never hides what the previous
user had. The active view, rendered tables and in-memory caches
(`cachedCustomers`, products, `currentUserIsSuperuser`,
`currentUserCanDeleteDocuments`, …) all survive the logout.

## Fix
1. **Primary**: after logout (every path: button, idle, max session, other-tab
   broadcast, failed refresh), **reload the page** (`location.replace('/')` or
   `location.reload()`), so no JS memory or DOM from the previous user can
   survive. Carry the logout reason message across the reload with
   `sessionStorage` (message text only), then show it on the login screen and
   delete it. Make sure:
   - the server-side `/auth/logout` call **finishes (or fails) before** the reload
     (await it with a short timeout ~2 s), so the cookie is really revoked;
   - the other-tab broadcast still works (the receiving tab reloads too);
   - there are no reload loops (a page load with no valid session just shows the
     login screen once).
2. **Defence in depth**: in `enterApp()`, explicitly set every permission-gated
   element to hidden first, then show what `/admin/me` allows (admin nav, groups
   card, category management, delete buttons, …). Always start on the
   **Documents** view after login, never on whatever view was active. Grep for every
   `style.display = 'block'` / permission flag set in `enterApp()` and give each a reset.
3. **Server check (verify, fix only if broken)**: as a non-admin user (no
   superuser, not a group admin), call `GET /api/admin/users`,
   `GET /api/admin/users/pending`, `GET /api/admin/users/{id}/sessions`,
   group/role endpoints → expect **403** for each. If any returns data, that's a
   real security bug: fix it in auth-service and list it in the report.

## Tests
- Playwright `tests/test_ui_logout_clears_state.py`:
  1. Log in as admin, open Admin (user list visible), sign out, log in as a plain
     Editor user **in the same page** → the Admin nav item is hidden, the Admin
     view is not shown, the visible view is Documents, and the page text doesn't
     contain the admin-only user list (another user's username, e.g. `admin`, in
     an admin table).
  2. Same with idle logout (use the short test session values from iteration 10).
  3. The logout message still appears after the reload ("Logged out after …").
  4. Two tabs: log out in tab A → tab B reloads to the login screen.
- Unit / smoke (`tests/`): a non-admin user gets 403 on every admin endpoint
  listed in Fix 3 (through the gateway).

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | UI tests pass | `pytest tests/test_ui_logout_clears_state.py -v` against the running stack |
| A2 | Reproduce first | Test 1 **fails on the old `app.js`** (paste the failure) and passes after the fix |
| A3 | Server 403s | the non-admin admin-endpoint test passes; list each endpoint and its status code in the report |
| A4 | No regressions | all service unit suites + `./scripts/verify.sh` pass, including the iteration 10 session UI tests |
| A5 | Proof | `loop/evidence/iter-11-after-switch.png`: the screen right after logging in as the Editor, following an admin logout |
