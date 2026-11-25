# report.md — Iteration 26

## 1. Summary
- **Status**: DONE
- **Goal**: Bug: a group user with project access sees NO projects, and the Manage-user "Project access" list is broken. Reproduce, find the root cause, fix, add regression tests.
- **Ponytail skill loaded**: yes
- **Branch / commit**: `dev` @ see the commit `loop iter 26: ...`
- **Root cause (one sentence)**: ets's project grants pointed at projects **outside ets's own group**. The superuser's grant list offered every project in the system, as checkboxes running inline on one line, and the server accepted any project. document-service correctly returns *group scope ∩ grants*, and that was empty.
- **Evidence**:
  1. On real Postgres through nginx, the backend already did the right thing for the case in the plan. The new regression test was run on the **old** code: the two data tests passed (user in A granted 2 of A's 3 projects → exactly those 2; a user with no grants → all of A's). The 3 grant tests failed: the superuser could grant group B's project and an unknown id, and `available-projects` returned `{"all": true}`. So the id format, the headers through nginx, and `/verify` per request are all fine. Only the *which projects can be granted* part was wrong.
  2. I went through the other suspects. A **disabled** group or role gives **403** at the gateway: I disabled group A on loop-test and `GET /api/projects` returned `403`. The UI then shows "You don't have access to this…", not "No projects match.". So for a member of an active group with access, an empty list (200 `[]`) can only mean *no granted project is in the user's groups*. A unit test shows this (`granted ⊄ group → []`).
  3. Screenshot 1 fits this. The list showed every project as inline `☐ name ☐ name …` chips (`.checkbox-list` is `flex-wrap`), so it is easy to read a tick as belonging to the neighbouring name. Nothing on the server stopped a tick on AI-2/BE/RE (Operation projects).
  - **I could not inspect the VM's data** (no access to 192.168.100.38). The local stack has no grants for its `ets`. To confirm on the VM after the update: open Manage user → ets. Any grant outside City Mart now appears in red as "… not in this user's groups; untick to remove".
- **One-paragraph summary**: `POST /admin/users/{id}/project-access` now refuses (403) any project that is not a live project of one of the target user's active groups. This applies to superusers too; group admins keep their pool check on top. `GET /admin/users/{id}/available-projects` now returns only that pool for everyone (it used to return `{"all": true}` for a superuser). auth-service gets the answer by asking document-service `GET /projects` with the target's group scope. It reuses document-service's own group filter and fails closed (503, no grant) if document-service cannot answer. The Manage-user list now has one checkbox per row, only the pool, and a group tag next to each name. Old grants outside the pool are listed in red so they can be unticked; otherwise they would keep hiding the user's projects with nothing visible in the UI. The empty state reads "No projects in this user's groups yet.". The list also refreshes when a group is added or removed. While writing the unit test I found and fixed a latent bug: `Project.id.in_(<strings>)` crashed on non-native UUID columns, and a malformed id gave a 500 on Postgres. Allowed ids are now parsed to UUIDs, and malformed ones are dropped (fail closed).
- **Same symptom on Documents/Customers?** Customers are not project-scoped (nginx blanks `X-Allowed-Projects` for `/api/customers`), so ets sees all City Mart customers. Documents use the same *scope ∩ (no project or granted project)* rule. With stray grants, ets would see City Mart documents that have **no** project, and none of the documents of City Mart projects.

## 2. Baseline (before any change)
`loop-test` stack, `./scripts/verify.sh --keep`:
```
======================= 87 passed, 20 skipped in 40.58s ========================
verify.sh: PASSED (stack left running: --keep)
```

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | Root cause identified and explained with evidence | PASS (by elimination; VM data not inspected) | §1: the regression test on the old code (2 data tests pass, 3 grant tests fail), disabled group → 403 not `[]`, unit test `granted ⊄ group → []` |
| A2 | Group user with grants sees exactly the granted projects of own group | PASS | `tests/test_project_grants_group_scope.py::test_user_with_grants_sees_exactly_them_and_no_grants_sees_whole_group`; Playwright `test_ui_project_access.py` (tick one → user's Projects shows exactly 1 row); `iter-26-group-user-projects.png` |
| A3 | Manage-user list: stacked rows, only the user's groups' projects, group tags | PASS | Playwright asserts 2 rows (the other group's project absent), group tag text in every row, equal x and increasing y; `loop/evidence/iter-26-manage-user-projects.png` |
| A4 | Server rejects granting a project outside the user's groups | PASS | `test_grant_outside_users_groups_is_rejected` (403, nothing stored), `test_unknown_project_grant_is_rejected` (403), `test_grantable_projects_are_only_the_users_groups` |
| A5 | No regressions; data safety | PASS | verify.sh on loop-test: `92 passed, 21 skipped`, PASSED. document-service unit: `366 passed`. auth-service unit: `98 passed`. UI: `39 passed, 8 skipped` + the 2 compose-dependent ones `5 passed` (see §5). **Data safety: every test ran on the `loop-test` stack (ports 18080) or in-process SQLite / a throwaway Postgres container; the user's stack was only rebuilt, never wiped.** |

The three data cases from Task 5, end to end on loop-test:
- with grants → only the granted projects of the own group (`…sees_exactly_them…`)
- no grants → every project of the own group (same test, `plain` user)
- grants all outside own groups → **empty list**. Grants made that way are now refused, so the test produces one the only way still possible: grant in-group, then move the project to group B. The stray grant still shows in `project-access`, is not in `available-projects`, and removing it gives the user the whole group back (`test_grants_left_outside_users_groups_show_nothing`).

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `services/auth-service/app/core/document_client.py` | added | `project_ids_in_groups(group_ids)`: GET document-service `/projects` with that group scope; fails closed with 503 |
| `services/auth-service/app/routers/admin.py` | modified | grant: 403 unless the project is in the target's groups (all granters). `available-projects`: returns only that pool (group admin: ∩ their administered pools) |
| `services/auth-service/app/core/config.py`, `app/main.py` | modified | `document_service_url`, `internal_shared_secret` settings; the default secret is refused in production like the other services |
| `infra/docker-compose.yml` | modified | auth-service gets `INTERNAL_SHARED_SECRET` from the same compose variable as nginx/backup-service |
| `services/document-service/app/routers/projects.py` | modified | allowed project ids parsed to UUIDs (malformed dropped); compare UUIDs |
| `web/js/app.js` | modified | Manage-user list from the server's pool for everyone, group tag per row, stray grants listed in red, new empty text, refresh after group add/remove |
| `web/index.html` | modified | project-access list stacked (`flex-direction: column`) |
| `tests/test_project_grants_group_scope.py` | added | gateway regression tests (5) |
| `tests/test_ui_project_access.py` | added | Playwright: stacked list, only own-group projects with tags, tick → user sees exactly it |
| `services/document-service/tests/test_project_grants_scope.py` | added | unit: granted ∩ group, granted ⊄ group, no grants/ALL, NONE, superuser, malformed id, single GET |
| `services/auth-service/tests/test_document_client.py` | added | unit: no groups → no call; unreachable → 503; headers sent |
| `services/auth-service/tests/test_secrets_check.py` | modified | valid production config now sets the internal secret; new test: default internal secret refused in production |

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | new unit test (SQLite) | `AttributeError: 'str' object has no attribute 'hex'` | `Project.id.in_(set of str)` on a UUID column | parse allowed ids with `group_scope._parse_ids` |
| 2 | auth `test_valid_production_configuration_starts_cleanly` | `RuntimeError … INTERNAL_SHARED_SECRET` | new production check; the test fixture had no internal secret | the fixture sets and restores a real-looking secret |
| 3 | `test_ui_project_access.py` | `#whoami-username` is "admin (superuser)" | test used exact text | `to_contain_text` |
| 4 | `test_ui_select_all` / `test_ui_bulk_cart` | `unknown shorthand flag: 'f'` | harness: the Playwright container had the docker CLI but not the compose plugin | mounted `/usr/libexec/docker/cli-plugins` → `5 passed` (no code change) |

## 6. Final verification output
```
COMPOSE_PROJECT_NAME=loop-test COMPOSE_OVERRIDE=infra/docker-compose.loop-test.yml GATEWAY=http://localhost:18080 ALLOW_DESTRUCTIVE_TESTS=1 ./scripts/verify.sh --keep
======================= 92 passed, 21 skipped in 44.07s ========================
verify.sh: PASSED (stack left running: --keep)

regression test on the OLD code (services stashed, rebuilt):
FAILED tests/test_project_grants_group_scope.py::test_grantable_projects_are_only_the_users_groups
FAILED tests/test_project_grants_group_scope.py::test_grant_outside_users_groups_is_rejected
FAILED tests/test_project_grants_group_scope.py::test_unknown_project_grant_is_rejected
3 failed, 2 passed in 2.41s
… after the fix: 5 passed in 2.52s

document-service (doc-test-lo, SQLite): 366 passed in 232.35s
auth-service (throwaway postgres:16 container): 98 passed in 54.34s
tests/test_ui_*.py (playwright v1.65.1, --network host, loop-test): 2 failed, 39 passed, 8 skipped
  -> the 2 re-run with the compose plugin mounted: 5 passed in 35.59s
```
(The 21st verify.sh skip is the new `test_ui_project_access.py`: the gateway venv has no Playwright. It passed in the Playwright container.)

## 7. Found but not fixed (out of scope)
- `services/document-service/app/routers/documents.py:284` has its own copy of `_parse_allowed_projects` that still returns strings. It works on Postgres for valid ids, but a malformed id in `X-Allowed-Projects` would give a 500 instead of failing closed. The same two-line fix as `projects.py` applies. Nginx always sets this header from `/verify`, so a client cannot trigger it.
- Moving a project to another group (`/projects/{id}/move`) leaves existing user grants on it in place. Those users lose that project, and if it was their only grant they see nothing. The Manage-user list now shows such grants in red so they can be removed. Clearing them automatically on move would need document-service → auth-service calls.
- Redesigning project access (per-group instead of per-user grants), as the plan says.
- The Manage-user evidence screenshot is clipped at the bottom of the modal (the modal scrolls). Both rows are visible.

## 8. Blockers / decisions needed
None. One thing to know for deploy: auth-service now also refuses to start in `ENVIRONMENT=production` with the default `INTERNAL_SHARED_SECRET`. It reads the same compose variable nginx uses, so a server that already sets it (required for nginx/document-service in production) needs no change.

## 9. Suggestions for the next iteration
- On the VM after deploy: open Manage user → ets, untick any red "not in this user's groups" rows, tick the City Mart projects, and log in as ets.
- A `scripts/ui-tests.sh` wrapping the Playwright container command (with the compose plugin mount) would avoid attempt 4 next time.

App running at http://localhost:8080 (login: admin / CHANGE_ME, local only)
