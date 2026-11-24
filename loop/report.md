# report.md — Iteration 29

## 1. Summary
- **Status**: DONE
- **Goal**: (A) One global group switcher for the admin; (B) dev tools (Delete ALL) only for the admin.
- **Ponytail skill loaded**: yes
- **Branch / commit**: `dev` @ (this commit, `loop iter 29: …`)
- **One-paragraph summary**: **A.** The sidebar now has a superuser-only **Viewing** select: All groups / each group / Unassigned. It is one extra `extraFilters` entry (`selectId: 'group-switcher'`) on Products, Documents, Customers and Projects, so search, category, date, sorting, Select all, Add to cart and Move all work on the filtered rows. Changing it clears the selection, because the filter key changes (iteration 24 rule). The four per-list `<table>-group-filter` dropdowns are gone, and Reset filters no longer touches the group. Option labels count the rows of the list that is open (e.g. `Ops … (1)`); there are no counts on other pages. The choice is saved in `localStorage` `ledger_group_view` as `{user, id}`, with every access in try/catch. It falls back to All groups if the group no longer exists or belongs to another user, and it is cleared on logout. Each list header shows "Showing: <group>" while a filter is on. With a group chosen, the superuser's New product/document/customer/project forms pre-select that group (still changeable). **B.** All five `DELETE /products|/documents|/customers|/projects|/companies` endpoints now return **403** to anyone who is not a superuser. The production block, the view-only rule and `X-Has-Document-Delete` are unchanged. The four group-scoped endpoints accept `?group_id=<uuid>|none`; a malformed id returns 422 and deletes nothing. `GET /products/dev-config` returns `delete_all_enabled` only for a superuser outside production. Button labels follow the switcher: "Delete ALL (dev)", "Delete ALL in <Group> (dev)" or "Delete ALL unassigned (dev)"; companies keep "Delete ALL companies (dev)" and ignore the switcher. Every button now asks first, e.g. "Delete ALL products in City Mart? This cannot be undone."; Cancel does nothing.

## 2. Baseline (before any change)
```
======================= 98 passed, 23 skipped in 46.26s ========================
verify.sh: PASSED (stack left running: --keep)
```
(The loop-test stack.) Note: the images for this run were built while I had already started editing the server, so the baseline may include part of the change. It passed either way.

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | Switcher filters all four lists at once; All / group / Unassigned; remembered per user; admin only | PASS | `tests/test_ui_group_switcher.py`: choosing Ops makes all four lists show only Ops rows (the City row is absent, every row carries the Ops tag). Unassigned → no group-tag other than "Unassigned". All → both rows. After a reload the value is still Ops. After logout `localStorage.ledger_group_view` is null. The ops user sees no switcher; the admin logs back in on All groups. Evidence: `iter-29-switcher-sidebar.png`, `iter-29-switcher-products-citymart.png` (shows the Ops group) |
| A2 | Per-list dropdowns removed; search/date/select/cart/move still work | PASS | `[id$='-group-filter']` count is 0 for both users. Switching groups hides the selection bar. The updated `test_ui_group_move.py` and `test_ui_select_all.py` (now driving `#group-switcher`) and `test_ui_bulk_cart.py` pass |
| A3 | Create forms pre-select the chosen group; header label | PASS | Switcher on City → `#product-form-group` has City's id and can be changed to Ops. `#<table>-group-showing` reads "Showing: <name>" on all four lists and is hidden on All groups |
| A4 | Delete ALL visible only to a superuser; non-superuser 403 | PASS | API `tests/test_delete_all_superuser.py`: an ops editor with documents-delete gets 403 on all five through nginx, the counts are unchanged, and dev-config is false for them. The UI test shows the group user none of the five buttons. Unit tests: `AS_OPS`/`AS_BOTH`/`AS_NOBODY` get 403 in both services |
| A5 | Delete ALL respects `group_id`; confirm names the group; production blocked | PASS | API: `?group_id=<ops>` → ops lists empty, city counts unchanged; `not-a-uuid` → 422. Unit tests: `=none` deletes only unassigned rows and no parameter deletes all. The production 403 tests still pass (`test_refused_in_production` ×4, catalogue equivalent). UI: dialog text `Delete ALL products in Ops …? This cannot be undone.`; Cancel leaves 3 rows; OK → "Deleted 3 …"; City's products stay. `iter-29-delete-all-confirm.png` shows the group-named button; a native `confirm()` is not captured in screenshots, so the test asserts the dialog text instead |
| A6 | No regressions; data safety | PASS | verify.sh `100 passed, 24 skipped`; catalogue unit `107 passed`; document unit `353 passed, 13 skipped` (+ re-run of the changed files: 40 passed); UI `45 passed, 8 skipped`. **Data safety: every test ran on the `loop-test` stack (port 18080) or in-process SQLite; the stack was removed afterwards with `scripts/loop-test-down.sh`. The user's stack was only backed up and rebuilt.** |

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `web/index.html` | modified | `#group-switcher-wrap` in the sidebar |
| `web/js/app.js` | modified | switcher (filter, counts, remember, header label, create default), per-list dropdowns removed, delete-all labels + confirm + `group_id` |
| `services/document-service/app/core/dev_delete_all.py` | modified | `refuse_in_production_or_view_only(x_access_level, scope)` now requires a superuser; new `only_group()` for `?group_id=` |
| `services/document-service/app/routers/{documents,customers,projects,companies}.py` | modified | pass the scope; `group_id` parameter (not on companies) |
| `services/catalogue-service/app/routers/products.py` | modified | superuser-only delete-all + `group_id`; dev-config checks superuser |
| `infra/nginx/nginx.conf.template` | modified | `/api/companies` now forwards `X-Is-Superuser` from `/verify` (it was blanked, so the companies 403 rule could never pass for the admin). Group ids stay blank there |
| `services/*/tests/test_group_scope.py`, `test_delete_all*.py` | modified | new rules: 403, group_id, =none, 422, dev-config; the old tests call as superuser |
| `tests/test_delete_all_superuser.py` | added | API through nginx |
| `tests/test_ui_group_switcher.py` | added | A1–A3 |
| `tests/test_ui_delete_all.py` | modified | confirm/cancel, labels, group wipe, group user sees no buttons; now `destructive`-gated (it always wiped everything) |
| `tests/test_ui_group_move.py`, `tests/test_ui_select_all.py` | modified | `#…-group-filter` → `#group-switcher` |

Inspected (Task 1): `refreshMoveControls` and the per-list filter creator (`app.js` ~3820–3950), `tableConfig` / `applyFilterAndSort` (~3700), the sidebar (`index.html` ~595), `DELETE_ALL` / `initDeleteAll` (~5180), the five DELETE endpoints and `/products/dev-config` (catalogue only; document-service has none).

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | catalogue `test_dev_config_enabled_outside_production` | expected True | old test called as a group user | asserts True for a superuser and False for a group user |
| 2 | document `test_delete_all_is_superuser_only_and_group_aware` | `cannot start a transaction within a transaction` | my test helper left SQLite sessions open | `with Session()` in the helper |
| 3 | `test_ui_select_all`, `test_ui_bulk_cart` | `FileNotFoundError: 'docker'`, then `unknown shorthand flag: 'f'` | the Playwright container had no docker CLI or compose plugin (test environment, not the app) | mounted `/usr/bin/docker` and `/usr/libexec/docker/cli-plugins`; both pass |
| 4 | (visual) | "Showing:" tag wrapped inside the heading | placed inside the `h2` | moved under the subtitle, `nowrap` |

## 6. Final verification output
```
==> [verify] pytest tests/ -v (BASE_URL=http://localhost:18080/api)
tests/test_delete_all_superuser.py::test_non_superuser_gets_403_and_nothing_changes PASSED
tests/test_delete_all_superuser.py::test_superuser_wipes_only_the_chosen_group PASSED
======================= 100 passed, 24 skipped in 48.79s =======================
verify.sh: PASSED (stack left running: --keep)

catalogue-service unit (loop-test image + pytest/httpx): 107 passed in 2.94s
document-service unit (loop-test image + pytest/httpx/pypdf): 1 failed, 353 passed, 13 skipped → fixed (attempt 2); changed files re-run: 40 passed

tests/test_ui_*.py (mcr.microsoft.com/playwright/python:v1.65.1-noble, --network host, docker socket + CLI, loop-test):
43 passed, 8 skipped (+2 env failures, see attempt 3) → test_ui_bulk_cart + test_ui_select_all re-run: 5 passed
after the header-label tweak: test_ui_group_switcher + test_ui_delete_all: 8 passed
```

## 7. Found but not fixed (out of scope)
- Removing the Delete ALL buttons entirely (BACKLOG #6).
- Idea: a switcher for non-superusers who belong to several groups. They may want one too; today they see all their groups mixed together.
- Counts in the switcher cover only the open list, not all four at once.
- With All groups or Unassigned chosen, a superuser who belongs to a group still gets that group pre-selected in the create forms (the existing `defaultGroupId`, unchanged). The switcher itself pre-selects nothing in those cases.
- Bulk import's group select does not follow the switcher (not asked for).
- Deleting one group's products also removes sub-item links to that group's products from other groups' bundles. The links can't stay, because the products are gone.
- The document-service unit tests need `pypdf`, which the runtime image lacks (installed ad hoc for the run, as before).

## 8. Blockers / decisions needed
None.

## 9. Suggestions for the next iteration
- Decide on a switcher for multi-group non-superusers (see §7).

App running at http://localhost:8080 (login: admin / CHANGE_ME, local only)
