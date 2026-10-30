# report.md — Iteration 11

## 1. Summary
- **Status**: DONE
- **Goal**: Logging out wipes **all** of the previous user's UI state, so the next user who logs in sees only their own menu and data
- **Ponytail skill loaded**: yes. Loaded with the Skill tool (`.claude/skills/ponytail`, level full) before any work
- **Branch / commit**: `loop/iter-11-logout-clears-state` @ the commit that adds this report (`loop iter 11: logout reloads the page, wiping the previous user's UI state [DONE]`), merged into `dev`
- **One-paragraph summary**: The reported bug is **reproduced and fixed**. After an admin signed out and an Editor signed in on the same page, the Editor saw the **Admin** menu item, still active, with the admin's user list.
  - **Primary fix**: every logout path goes through one `doLogout()`. That covers the button, idle, max session, a failed refresh, the warning's Log out, "log out all devices", and another tab's broadcast. It now
    1. revokes the refresh cookie on the server and **waits** for it (2 s cap);
    2. tells the other tabs only **after** that, so they reload into a dead session;
    3. stores the reason message in `sessionStorage`;
    4. **reloads the page** (`location.replace('/')`), so no JS memory or DOM from the previous user survives.

    The login screen shows the message once after the reload. A page load without a session only shows the login screen, so it can't loop.
  - **Defence in depth**: `enterApp()` first hides every permission-gated element, resets the permission flags and switches to **Documents**, and only then applies `/admin/me`.
  - **Server check**: a plain Editor gets **403** on all 12 admin endpoints probed, and no data from the 2 that answer 200 by design. The server was not broken, so auth-service is unchanged.
  - **Results**: all five checks pass.

## 2. Baseline (before any change)
The first attempt couldn't start Docker: `dockerd` exited because a stale managed `containerd` (pid 239) was still around. I cleared it and started `dockerd` (ready after 2 s). Then:
```
$ ./scripts/verify.sh --keep
======================== 27 passed, 5 skipped in 17.14s ========================
verify.sh: PASSED (stack left running: --keep)

auth-service 93 passed (throwaway Postgres, as before) · catalogue 74 · search 12 · document 201
```

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | UI tests pass | PASS | `pytest tests/test_ui_logout_clears_state.py -v`: on the default stack, tests 1 (sign out → Editor) and 4 (two tabs) `PASSED`; test 2/3 (idle logout → Editor, message survives the reload) `PASSED` on the short-value stack (idle 20 s, warning 10 s, max 60 s; local gitignored `.env` only, restored afterwards) and skips on the default stack |
| A2 | Reproduce first | PASS | Run against the **old** `app.js`, before any change; failure output below |
| A3 | Server 403s | PASS | `tests/test_admin_authz.py` passed; endpoint table below |
| A4 | No regressions | PASS | auth `93`, catalogue `74`, search `12`, document `201`; `./scripts/verify.sh` → `30 passed, 6 skipped` … `verify.sh: PASSED`. The 6 skips are the short-value UI tests; with short values they **all passed** (the iteration 10 tests included): `8 passed` |
| A5 | Proof | PASS | `loop/evidence/iter-11-after-switch.png`: right after the Editor (`ui-editor-…`) logs in following the admin's logout. The sidebar has **no Admin** item, the **Documents** view is shown (its table still loading at the moment of the shot), and the footer says "Signed in as ui-editor-…" |

**A2: failure on the old `app.js`:**
```
tests/test_ui_logout_clears_state.py::test_sign_out_then_editor_sees_no_admin_state FAILED
E       AssertionError: Locator expected to be hidden
E       Actual value: visible
E         - Expect "to_be_hidden" locator("#admin-nav-item") with timeout 5000ms
E           14 × locator resolved to <div id="admin-nav-item" data-view="adminpanel" class="nav-item active">…</div>
tests/test_ui_logout_clears_state.py::test_logout_in_one_tab_reloads_the_other FAILED
E       AssertionError: assert True is None      (tab B was never reloaded: its JS state survived)
```
After the fix, both pass.

**A3: a plain Editor** (Operation group, Editor role; not a superuser, not a group admin) through the gateway. "the other group" is a temporary group the Editor is **not** in, with one role; it is deleted afterwards.
| Call | Status |
|---|---|
| `GET /api/admin/users` | **403** |
| `GET /api/admin/users/{admin}/sessions` | **403** |
| `GET /api/admin/users/{admin}/roles` | **403** |
| `GET /api/admin/users/{admin}/groups` | **403** |
| `GET /api/admin/users/{admin}/project-access` | **403** |
| `GET /api/admin/users/{admin}/available-projects` | **403** |
| `GET /api/admin/groups/{other group}/members` | **403** |
| `GET /api/admin/groups/{other group}/roles` | **403** |
| `GET /api/admin/groups/{other group}/project-access` | **403** |
| `GET /api/admin/roles/{role in other group}` | **403** |
| `POST /api/admin/users/{admin}/logout-all` | **403** |
| `PATCH /api/admin/users/{admin}/active` | **403** |
| `GET /api/admin/users/pending` | 200 `[]`: by design a non-admin sees an empty list, no data |
| `GET /api/admin/groups` | 200 `["Operation"]`: by design only the caller's own groups |

Members can read their **own** group's members/roles/project access by design (`is_member_of` checks), so those calls were tested against a group the Editor isn't in. No auth-service change was needed.

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `web/js/app.js` | `doLogout(message, broadcast)` is now async: a `loggingOut` guard, await `/auth/logout` (AbortController, 2 s), then broadcast to tabs, put the message in `sessionStorage`, then `location.replace('/')`. A receiving tab (`broadcast=false`) skips the server call and reloads too. `bootstrap()` reads and deletes the message and shows it on the login screen only when there's no live session; there's no reload in that path, so no loop. `enterApp()` resets `admin-nav-item`, `groups-card`, `group-select-wrap`, `all-users-card`, `new-group-form-wrap`, `category-split-btn-wrap` and `currentUserIsSuperuser` / `currentUserCanDeleteDocuments`, and clicks the Documents nav item before applying `/admin/me` | Fix 1 and 2. These are every `style.display`/permission flag `enterApp()` sets; `category-split-btn-wrap` and the delete flag were already set both ways, now also reset first |
| `tests/conftest.py` | `editor_user` fixture: a real Operation Editor created via the admin API, deactivated afterwards (users can't be deleted) | Needed by both new test files |
| `tests/test_ui_logout_clears_state.py` | added: plan tests 1, 4 and 2+3 (combined: idle logout → message after the reload → Editor screen clean) | Tests |
| `tests/test_admin_authz.py` | added: Fix 3 / A3 | Tests |
| `loop/history/report-iter-10.md`, `loop/evidence/iter-11-after-switch.png` | moved / added | LOOP 1.2, A5 |

No changes to `index.html`, the backend or nginx.

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | Baseline (environment) | `Cannot connect to the Docker daemon` | `dockerd` exited on a stale managed `containerd`. My first cleanup's `pkill -f` pattern also matched its own shell, so it was re-run | Killed the stale `containerd`, started `dockerd`; baseline green |
| 2 | A2 (intended) | `#admin-nav-item` visible for the Editor; tab B not reloaded | The reported bug | Fix 1 + 2 above; both tests pass |

## 6. Final verification output
```
tests/test_admin_authz.py::test_editor_gets_no_admin_data PASSED         [  2%]
tests/test_auth.py::test_admin_login_ok PASSED                           [  5%]
tests/test_auth.py::test_wrong_password_rejected PASSED                  [  8%]
tests/test_auth.py::test_protected_route_without_token_rejected PASSED   [ 11%]
tests/test_auth.py::test_protected_route_with_garbage_token_rejected PASSED [ 13%]
tests/test_auth.py::test_sessions_and_logout_everywhere_through_the_gateway PASSED [ 16%]
tests/test_catalogue_smoke.py::test_category_and_product PASSED          [ 19%]
tests/test_catalogue_smoke.py::test_duplicate_product_is_blocked_and_reported PASSED [ 22%]
tests/test_documents_smoke.py::test_quotation_lifecycle PASSED           [ 25%]
tests/test_documents_smoke.py::test_quotation_pdf_supplier_block_shows_company_heading_and_selected_md PASSED [ 27%]
tests/test_documents_smoke.py::test_quotation_with_one_time_manual_customer PASSED [ 30%]
tests/test_documents_smoke.py::test_quotation_for_a_saved_customer_with_an_organization PASSED [ 33%]
tests/test_health.py::test_service_reachable_via_gateway[auth-service] PASSED [ 36%]
tests/test_health.py::test_service_reachable_via_gateway[document-service] PASSED [ 38%]
tests/test_health.py::test_service_reachable_via_gateway[catalogue-service] PASSED [ 41%]
tests/test_health.py::test_service_reachable_via_gateway[search-service] PASSED [ 44%]
tests/test_health.py::test_web_ui_served PASSED                          [ 47%]
tests/test_search_smoke.py::test_product_becomes_searchable PASSED       [ 50%]
tests/test_search_smoke.py::test_document_becomes_searchable PASSED      [ 52%]
tests/test_static_caching.py::test_ui_files_revalidate_and_keep_every_security_header[/] PASSED [ 55%]
tests/test_static_caching.py::test_ui_files_revalidate_and_keep_every_security_header[/js/app.js] PASSED [ 58%]
tests/test_static_caching.py::test_index_is_served_with_the_current_app_js_version PASSED [ 61%]
tests/test_static_caching.py::test_script_version_changes_when_app_js_changes PASSED [ 63%]
tests/test_static_caching.py::test_api_responses_get_no_new_cache_control PASSED [ 66%]
tests/test_ui_cache_reload.py::test_normal_reload_runs_the_new_app_js PASSED [ 69%]
tests/test_ui_customer_organization.py::test_customer_form_and_table_show_the_organization PASSED [ 72%]
tests/test_ui_duplicate_products.py::test_duplicate_products_in_the_ui PASSED [ 75%]
tests/test_ui_logout_clears_state.py::test_sign_out_then_editor_sees_no_admin_state PASSED [ 77%]
tests/test_ui_logout_clears_state.py::test_logout_in_one_tab_reloads_the_other PASSED [ 80%]
tests/test_ui_logout_clears_state.py::test_idle_logout_then_editor_sees_no_admin_state_and_the_message_survives SKIPPED [ 83%]
tests/test_ui_manual_customer.py::test_create_document_with_manual_customer PASSED [ 86%]
tests/test_ui_sessions.py::test_idle_warning_then_logout_with_the_inactivity_message SKIPPED [ 88%]
tests/test_ui_sessions.py::test_stay_logged_in_keeps_the_session SKIPPED [ 91%]
tests/test_ui_sessions.py::test_activity_in_one_tab_keeps_the_other_and_logout_reaches_both SKIPPED [ 94%]
tests/test_ui_sessions.py::test_the_hard_limit_ends_an_active_session SKIPPED [ 97%]
tests/test_ui_sessions.py::test_session_lists_for_the_user_and_the_admin SKIPPED [100%]
======================== 30 passed, 6 skipped in 22.01s ========================
verify.sh: PASSED
```
Short-value run (the 6 skipped above + the 2 default-value logout tests): `8 passed`. Unit suites: auth 93, catalogue 74, search 12, document 201. The stack is down, and the auth-test Postgres was removed.

## 7. Found but not fixed (out of scope)
- **Each run leaves 2 deactivated users** (`ui-editor-…` from the two tests using `editor_user`), plus iteration 10's `sess-user-…`. Users can't be deleted by design. A seeded, reusable test Editor would stop the growth.
- **Logout now waits up to 2 s for the server** before the page reloads. On a slow or broken network the user sees the app for up to 2 s after clicking Sign out. That's the price of revoking the cookie before the reload; the reload happens whether or not the request succeeds.
- **Group members can read their own group's member list** (usernames, via `GET /api/admin/groups/{own}/members`) and its roles. That is by design (`is_member_of`), but "can an Editor see who else is in Operation" may be worth confirming with the user.
- Still open: the auth-service unit tests need a Postgres; the manual-customer Phone/Email question; the footer margin assumption; the Total-row orphan; the MinIO image decision.

## 8. Blockers / decisions needed
None. One question for the user: should plain members see their own group's member list (§7, third bullet)? Today they can; the admin UI doesn't show it to them.

## 9. Suggestions for the next iteration
- A seeded test Editor account for the smoke/UI suites, so test users stop accumulating.
