# report.md — Iteration 15 (revised: 5 pages)

## 1. Summary
- **Status**: DONE
- **Goal**: A one-click **"Delete ALL …"** button on each of the Products, Documents, Customers, Projects and Companies pages, for the user's development/testing stage. Temporary, and must be trivial to remove later
- **Ponytail skill loaded**: yes (the session-start hook loaded it, and this iteration followed it)
- **Branch / commit**: `dev` @ (this commit, see `git log`). Before this, I rebased the earlier unpushed products-only commit onto the planner's revised PLAN.md. The rebase was clean.
- **One-paragraph summary**: The products part was already built in the first pass (`DELETE /products`, `GET /products/dev-config`). This pass adds `DELETE /documents`, `/customers`, `/projects` and `/companies` to document-service. Each one permanently deletes every row, including rows in the recycle bin, and returns `{"deleted": N}`. Each returns 403 when `ENVIRONMENT=production` or the user has view-only access. Documents also needs the same `X-Has-Document-Delete` permission as the single delete.
  - **Documents** explicitly deletes their line items and audit log, removes each from Meilisearch, and makes a best-effort attempt to delete their stored files.
  - **Customers / Projects** set the reference on documents to NULL, then delete. The documents are kept.
  - **Companies** set `company_id`/`director_id` on documents to NULL, delete the MDs, then the companies. It makes a best-effort attempt to delete the logo, seal and MD-seal files.
  - The UI has five red "Delete ALL … (dev)" buttons, all driven by one generic JS block. One click, no confirm, then the list refreshes and a banner shows "Deleted N …". The buttons show when `/products/dev-config` says the feature is enabled. One `ENVIRONMENT` variable feeds both services through compose, and each service's endpoint still enforces its own guard.
  - Everything sits inside `DEV-ONLY: delete-all` markers. The first pass used `delete-all-products` markers; I renamed them to match the plan.

## 2. Baseline (before any change)
```
$ ./scripts/verify.sh --keep          (products-only iter-15 code, rebased)
======================== 24 passed, 9 skipped in 5.12s =========================
verify.sh: PASSED (stack left running: --keep)
```

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | Catalogue + document-service unit tests pass | PASS | in the `infra-<svc>-service` images + `requirements-dev.txt`: document-service `python -m pytest tests -q` → `239 passed in 168.50s` (18 new in `tests/test_delete_all.py`); catalogue → `80 passed in 2.04s` |
| A2 | UI test passes | PASS | `mcr.microsoft.com/playwright/python:v1.65.1-noble`, `--network host`, against the verify.sh stack: `pytest tests/test_ui_delete_all.py -v` → `6 passed in 21.52s` (one test per page + the production-hidden test) |
| A3 | No regressions | PASS | `./scripts/verify.sh --keep` → `24 passed, 9 skipped` (the skips are the Playwright UI tests, because Playwright isn't in verify.sh's venv) |
| A4 | Proof | PASS | `loop/evidence/iter-15-{products,documents,customers,projects,companies}-{before,after}.png`, taken by the UI test |
| A5 | Removal steps | PASS | §4 below |

Extra live-Postgres check: SQLite doesn't enforce FKs, so the unit tests can't prove the NULL-first ordering. Against the running stack I created a document linked to a customer, a project, a company and an MD, then called `DELETE /customers` → `200 {"deleted":1}`, `/projects` → 200, `/companies` → 200. The document survived with all four refs `None` and its 1 line item intact. `DELETE /documents` → `200 {"deleted":1}`.

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `services/document-service/app/routers/documents.py` L659–682 | added (DEV-ONLY block) | `DELETE /documents` |
| `services/document-service/app/routers/customers.py` L113–128 | added (DEV-ONLY block) | `DELETE /customers` |
| `services/document-service/app/routers/projects.py` L145–160 | added (DEV-ONLY block) | `DELETE /projects` |
| `services/document-service/app/routers/companies.py` L223–244 | added (DEV-ONLY block) | `DELETE /companies` |
| `services/document-service/app/core/dev_delete_all.py` | added (whole file DEV-ONLY) | shared production/view-only guard + best-effort file delete |
| `services/catalogue-service/app/routers/products.py` L427–469 | marker renamed only | `DELETE /products`, `GET /products/dev-config` (from the first pass) |
| `web/index.html` L617, 692, 744, 790, 837 | one `<!-- DEV-ONLY: delete-all -->` comment + the button on the next line, ×5 | the buttons, hidden by default |
| `web/js/app.js` L310 | one line | `initDeleteAll()` in `enterApp()` |
| `web/js/app.js` L4398–4433 | replaced the products-only block with a generic table of 5 | show/hide + click handlers |
| `services/document-service/tests/test_delete_all.py` | added | 18 unit tests |
| `tests/test_ui_delete_all.py` | added (replaces `tests/test_ui_delete_all_products.py`, deleted) | Playwright + screenshots |
| `loop/history/report-iter-15-products.md` | moved | the first pass's report |

Gateway/auth (Task 2): no change needed. In `infra/nginx/nginx.conf.template`, `/api/documents`, `/api/customers`, `/api/projects`, `/api/companies` and `/api/products` are prefix locations with no method rules. They get the same `_verify` RBAC check and the same headers as the single delete, and that includes `X-Has-Document-Delete` on `/api/documents`. The live UI test confirms this through nginx.

**To remove the feature later**:
1. `grep -rn "DEV-ONLY: delete-all" services web`
   - In `products.py`, `documents.py`, `customers.py`, `projects.py`, `companies.py` and `web/js/app.js`, delete each BEGIN…END block.
   - In `web/index.html`, delete each `<!-- DEV-ONLY: delete-all -->` line and the `<button id="delete-all-…-btn">` line right below it (5 places).
   - Delete the single `initDeleteAll(); // DEV-ONLY…` line in `web/js/app.js`.
2. Delete these files:
   - `services/document-service/app/core/dev_delete_all.py`
   - `services/document-service/tests/test_delete_all.py`
   - `services/catalogue-service/tests/test_delete_all_products.py`
   - `tests/test_ui_delete_all.py`
3. `grep -rn "delete-all\|delete_all\|dev-config\|dev_delete_all" services web tests` should return nothing. There is no migration, nginx or auth-service change to undo.

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | A1 (`test_delete_all.py`) | hung in `psycopg2.connect` from `app/core/audit.py:26` | `log_action` opens its own Postgres session, and the test didn't stub it | stubbed `documents_router.log_action`, the same as `test_supplier_position_e2e.py` does; audit rows are inserted directly in the seed instead |
| 2 | A2 (environment) | `ModuleNotFoundError: No module named 'httpx'` | forgot `httpx` in the Playwright container's pip install | added it |

## 6. Final verification output
```
$ ./scripts/verify.sh --keep
======================== 24 passed, 9 skipped in 4.92s =========================
verify.sh: PASSED (stack left running: --keep)

$ pytest tests/test_ui_delete_all.py -v     (Playwright image)
test_ui_delete_all.py::test_one_click_deletes_all[documents] PASSED      [ 16%]
test_ui_delete_all.py::test_one_click_deletes_all[products] PASSED       [ 33%]
test_ui_delete_all.py::test_one_click_deletes_all[customers] PASSED      [ 50%]
test_ui_delete_all.py::test_one_click_deletes_all[projects] PASSED       [ 66%]
test_ui_delete_all.py::test_one_click_deletes_all[companies] PASSED      [ 83%]
test_ui_delete_all.py::test_buttons_hidden_in_production PASSED          [100%]
============================== 6 passed in 21.52s ==============================
```

## 7. Found but not fixed (out of scope)
- **The UI decides visibility from catalogue's `/products/dev-config` only.** If the two services ever ran with different `ENVIRONMENT` values, the document-service buttons could show while that service answers 403. That can't happen with the current compose, which passes one variable to both. The server guard is the real rail.
- **Running `tests/test_ui_delete_all.py` wipes all five tables in the stack it targets.** That is the feature. verify.sh skips it today only because Playwright isn't in its venv.
- **The single `DELETE /products/{id}` sub-item FK issue and the leftover MinIO file** are still open (see `loop/history/report-iter-15-products.md` §7). Single deletes of documents and companies also leave their MinIO files behind. Delete-all removes them.
- **Delete-all ignores `X-Allowed-Projects`** (the plan says every row). A project-restricted user in dev can wipe rows they can't see.
- **The search-index error path is not separately unit-tested for documents**: `remove_document_from_index` already catches and logs, so it never raises. The storage-error path is tested.
- **The extra red button wraps the page toolbar onto a second line on narrower widths.** It goes away when the feature is removed.

## 8. Blockers / decisions needed
None.

## 9. Suggestions for the next iteration
- Remove this feature before the first production deploy (steps in §4).

## App
App running at http://localhost:8080 (login: admin / CHANGE_ME, local only).

**Push**: `git push origin dev` fails in this environment (`could not read Username for 'https://github.com'`, meaning no GitHub credentials here). Commits are on local `dev` only.
