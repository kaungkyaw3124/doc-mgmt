# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **26** |
| Goal | **Bug: a group user with project access sees NO projects; and the Manage-user "Project access" list is broken.** Reproduce, find the root cause, fix, add regression tests |
| Attempt limit | 5 per failing check |
| Status | READY FOR CLAUDE CODE |

## What the user reported (3 screenshots, VM, http://192.168.100.38:8080)
1. Admin → Manage user **ets**: Current groups = **City Mart**; role = Editor (City Mart); **Project access** has "City Mart Store Renovation" and "City Mart Test Project" **ticked**. The project list under it is also broken: it lists **every** project (AI-2, AI-2, BE, RE belong to other groups) and the checkboxes run **inline on one line** instead of one per row.
2. Admin (superuser) → Projects: 6 projects, group tags: City Mart Store Renovation = City Mart, City Mart Test Project = City Mart, AI-2 (Aug 18) = City Mart, AI-2 (Aug 13) = Operation, BE = Operation, RE = Operation.
3. Logged in as **ets** → Projects: **"No projects match."** Expected: ets sees the two City Mart projects it was granted.

## Suspects (verify each, do not assume)
- ets has `UserProjectAccess` rows (→ `X-Allowed-Projects` = those ids) AND is in group City Mart (→ `X-Group-Ids`). `list_projects` in `services/document-service/app/routers/projects.py` applies BOTH `scoped()` and `project_id IN allowed`. Check the ids really match what document-service stores (format/case, `str(uuid)` vs the UUID column on **Postgres**, not only SQLite), and that both headers actually reach document-service through nginx for a non-superuser.
- The grant list the superuser edits (`/admin/users/{id}/available-projects`, `web/js/app.js` ~4242) may be returning/ticking project ids that no longer exist or are from a different id space (e.g. ids from before projects were re-created/moved), so the grants point at nothing visible.
- City Mart group **inactive**? (`get_user_group_ids` returns ACTIVE groups only → empty scope → everything hidden.) Check the Groups card / DB.
- Stale JWT/session or cached response: confirm `/verify` is evaluated per request.
- Same symptom on Documents/Customers for ets? Check and report.

## Tasks
1. **Reproduce on the throwaway `loop-test` stack ONLY** with real Postgres and the real gateway: two groups, user in group A with project grants to 2 of A's 3 projects → GET `/projects` through nginx must return exactly those 2. Write the failing test first (`tests/test_project_grants_group_scope.py`, API level through the gateway, plus a unit test in document-service for the header combination).
2. Find the root cause (state it in the report in one sentence, with the evidence), fix it in the right place.
3. **Manage-user Project access UI**: one checkbox per row (stacked), and list ONLY projects the target user may be granted = projects belonging to the target's groups (superuser: those groups' projects, not every project in the system). Show the project's group tag next to the name. Empty state text if none. Keep ticking/unticking behaviour as today.
4. Server side: granting a project to a user must reject (403/422) a project outside the target user's groups; list endpoint for the grant UI returns only the allowed pool.
5. Check the three data cases also work end to end: user with grants → sees only granted projects (in own group); user with NO grants → sees all projects of own group (existing opt-in rule); user whose grants are all outside own groups → clear result (empty) and the report says so.
6. Tests + evidence.

## Tests
- Regression test from task 1 (fails before the fix, passes after).
- Unit: `list_projects` with `X-Group-Ids` + `X-Allowed-Projects` combinations (granted ∩ group, granted ⊄ group, no grants, `ALL`, `NONE`).
- API: grant outside the target's groups → rejected; available-projects returns only group projects.
- Playwright `tests/test_ui_project_access.py` on `loop-test`: superuser opens Manage user → checkboxes stacked, only that user's groups' projects; tick one → log in as that user → Projects shows exactly it.
- Existing suites pass; `./scripts/verify.sh` passes.
- Evidence: `loop/evidence/iter-26-manage-user-projects.png`, `iter-26-group-user-projects.png`.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Root cause identified and explained with evidence | report §Summary |
| A2 | Group user with grants sees exactly the granted projects of own group | regression + Playwright |
| A3 | Manage-user list: stacked rows, only the user's groups' projects, group tags | Playwright + screenshot |
| A4 | Server rejects granting a project outside the user's groups | API test |
| A5 | No regressions; data safety (tests only on `loop-test`) | `./scripts/verify.sh`, report states which stack |

## Out of scope — record under "Found but not fixed"
Redesigning project access (e.g. per-group instead of per-user grants).
