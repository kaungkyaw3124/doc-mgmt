# report.md — Iteration 19

## 1. Summary
- **Status**: DONE
- **Goal**: **Department data separation.** Documents, products, customers and projects each belong to ONE group (department: Operations, CityMart, …). A user sees and can touch only their own group's records; anything a user creates is automatically owned by their group. Superusers see all.
- **Ponytail skill loaded**: yes
- **Branch / commit**: `dev` @ (this commit, "loop iter 19: …")
- **One-paragraph summary**: `documents`, `customers`, `projects` and `products` now have a nullable, indexed `group_id`.
  - **Headers:** auth-service `/verify` returns `X-Group-Ids` (the user's *active* groups; every active group for a superuser) and `X-Is-Superuser`. Nginx always overwrites both on the data routes, and blanks them on the shared companies and categories routes.
  - **One helper per service:** `app/core/group_scope.py` in document-service and in catalogue-service. Every endpoint on the four tables goes through `scoped()`, `get_or_404()` or `group_for_create()`.
  - **Visibility:** a superuser sees everything, including unassigned rows. Everyone else sees only their own groups' rows. Missing headers mean "no groups", so the services fail closed. Rows of another group answer 404.
  - **Create:** a new record goes to the creator's only group. A user in several groups, or a superuser, must send `group_id` (422 if missing; 403 for a group the user isn't in).
  - **No cross-group links:** a document's customer and project must be in the document's group (422).
  - **Search:** filters on the indexed `group_id` server-side.
  - **Shared and per-group data:** companies, categories, document numbering and SKU numbering stay shared. Duplicate checks are now per group.
  - **Existing data:** the migrations leave old rows NULL ("unassigned", superusers only). The superuser endpoints `POST /documents/assign-unassigned` and `POST /products/assign-unassigned`, wrapped by `scripts/assign-unassigned.sh <group>`, assign them and rebuild both search indexes.
  - **UI:** a required Group select on the four create forms, shown only to superusers and multi-group users. A group label sits next to each row in the four lists. The document form's product, customer and project pickers only offer the chosen group's rows.
  - **Docs:** `docs/GROUPS.md`.

## 2. Baseline (before any change)
```
$ COMPOSE_PROJECT_NAME=loop-test COMPOSE_OVERRIDE=infra/docker-compose.loop-test.yml GATEWAY=http://localhost:18080 ALLOW_DESTRUCTIVE_TESTS=1 ./scripts/verify.sh --keep
======================= 42 passed, 13 skipped in 17.xx s =======================
verify.sh: PASSED (stack left running: --keep)
document-service units 275 passed · catalogue 78 passed · search 12 passed · auth 93 passed (against a throwaway postgres:16 container)
```
(In the baseline, auth-service's suite shows 93 errors without a Postgres on localhost:5432. That is how it is run, not a code issue. I ran it against a disposable `postgres:16` container.)

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | Isolation matrix (4 entities × all operations incl. search/export/file URLs) | PASS | `tests/test_group_isolation.py` **19 passed** through Nginx on loop-test. It covers: lists, get, patch, trash, restore and delete (404) × 4 entities × both directions; trash; PDF/XLSX export, document file-url, audit-log, product file-url, file-content and sub-items; duplicate-check, duplicates, by-customer and by-product; search in both directions; superuser and multi-group users see both. Plus document-service `test_group_scope.py` (21) and catalogue `test_group_scope.py` (9) at the API level. |
| A2 | Auto-assign + validation | PASS | Creator's group is stored. Multi-group without `group_id` → 422; a foreign group → 403; a superuser without `group_id` → 422; no group → 403 "You are not in any group — ask an admin". A cross-group customer or project → 422. A document whose product was deleted or is in another group still saves and exports from its line-item snapshot (`test_document_with_a_deleted_or_foreign_product_still_saves_and_exports`). Bulk import assigns the importer's group. |
| A3 | Headers can't be spoofed; fail closed | PASS | `tests/test_gateway_group_headers.py`: the admin sends `X-Is-Superuser: 0` plus a stranger group and still sees its rows, so Nginx overwrote the headers. `test_client_cannot_spoof_the_scope_headers`: an Operations user sending CityMart's id plus `X-Is-Superuser: 1` still gets 404 and nothing in search. Fail-closed unit tests: missing headers → empty lists, 404, 403 on create, empty search without calling Meilisearch. The existing gateway-secret tests still pass. |
| A4 | Migration + assign-unassigned + re-index | PASS | `test_0009_migration_keeps_rows_null_and_runs_twice_and_down`, `test_alembic_single_head_and_0001_migration`, `test_unassigned_rows_are_superuser_only_until_assigned`, catalogue `test_assign_unassigned`. Script output is below. |
| A5 | UI: Group select + label; two-user flow | PASS | `tests/test_ui_group_isolation.py` 1 passed: two departments create a product, customer, project and document in the UI, and each sees only its own in all four lists and in Search. The superuser's Group select is visible and required: an empty choice is blocked, and the chosen group is stored. Screenshots: `loop/evidence/iter-19-ops-view.png`, `iter-19-citymart-view.png`, `iter-19-superuser-create-form.png`. |
| A6 | No regressions | PASS | verify.sh **63 passed / 14 skipped**. Units: document-service **296**, catalogue **89**, search **16**, auth **94**. UI suite **31 passed / 6 skipped** (the usual short-session skips). |
| A7 | Endpoint inventory, nothing unscoped | PASS | Inventory and grep below. |

**Data safety:** every test ran on the throwaway `loop-test` stack (ports 18080/18443, `infra/data-loop-test/`), on SQLite/TestClient, or (auth-service) on a disposable `postgres:16` container that was removed afterwards. Nothing ran against the user's stack. The migrations only add nullable columns; nothing is backfilled or deleted.

### assign-unassigned on loop-test (old rows from earlier iterations)
```
$ COMPOSE_PROJECT_NAME=loop-test COMPOSE_OVERRIDE=infra/docker-compose.loop-test.yml ./scripts/assign-unassigned.sh "No Such Group"
No group named 'No Such Group'. Groups: Operation          (exit 1)
$ ... ./scripts/assign-unassigned.sh "Operation"
{"group": "Operation", "group_id": "6e55de2c-…",
 "document-service": {"documents": 1, "customers": 1, "projects": 1, "reindexed": 1},
 "catalogue-service": {"products": 1, "reindexed": 1}}
$ ... ./scripts/assign-unassigned.sh "Operation"      # second run: harmless
 "document-service": {"documents": 0, "customers": 0, "projects": 0, "reindexed": 1},
 "catalogue-service": {"products": 0, "reindexed": 1}
```

### alembic heads (A5 of the plan's Tasks: one clean head per service)
```
document-service:  0009_group_id (head)          # chained on 0008_company_letterhead
catalogue-service: 0001_product_group_id (head)  # first Alembic revision of this service
```

### A7 — endpoint inventory (every route touching the four tables uses the helper)
- **documents** (`group_scope.get_scope` on each route):
  - create, list, trash list, get, patch, status, trash, restore, delete, delete-all (DEV);
  - file upload, file-url, audit-log, export xlsx/pdf/catalogue;
  - by-customer, by-product, assign-unassigned.
- **customers / projects**: create, list, trash list, get, patch, trash, restore, delete, delete-all (DEV); and `projects/{id}/documents`.
- **products**:
  - create, list, trash list, get, patch, trash, restore, delete, delete-all (DEV);
  - duplicate-check, duplicates, bulk-import, assign-unassigned;
  - file upload, file-url, file-content, download-bundle;
  - sub-items list, add, remove.
- **search**: `/search` (documents and products, filtered on `group_id`).
- **Explicitly ALL scope** (on purpose, each with a code comment):
  - document numbering and the `doc_number` uniqueness check;
  - SKU numbering and the SKU uniqueness check;
  - `visible-product-ids` (an internal project-rule lookup returning product ids only, which the caller then filters by its own group);
  - shared-category rename and sync;
  - the dev delete-all of shared companies;
  - document-service's own export lookups to catalogue (`X-Is-Superuser: 1`, internal only; the document was already authorized).
```
$ grep -rnE "db\.query\(models\.(Document|Customer|Project|Product)\b" services/document-service/app/routers services/catalogue-service/app/routers services/*/app/core
(no output)
```
(The only direct `db.query(...)` uses left on these models are in `main.py` `after_restore`, which rebuilds the index after a backup restore, internal only.)

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `services/auth-service/app/core/authz.py`, `routers/auth.py` | `get_user_group_ids`; `/verify` sets `X-Group-Ids`, `X-Is-Superuser` (`get_user_allowed_project_ids` unchanged) | Task 2 |
| `infra/nginx/nginx.conf.template` | `auth_request_set` + `proxy_set_header` for both headers on documents/customers/projects/products/search; blanked on companies/categories | Task 2 |
| `services/document-service/app/core/group_scope.py`, `services/catalogue-service/app/core/group_scope.py` | added: the one scope helper per service | Task 4 |
| `services/document-service/app/models.py`, `schemas.py`, `alembic/versions/0009_group_id.py` | `group_id` on 3 tables (nullable, indexed); `group_id` on Create/Out schemas (not Update — no moving yet); `AssignUnassigned` | Task 3 |
| `services/catalogue-service/app/models.py`, `schemas.py`, `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_product_group_id.py`, `requirements.txt` (+alembic), `app/main.py` | `Product.group_id`; minimal Alembic for this service, run after `create_all` at startup; env keeps existing loggers | Task 3 |
| `services/document-service/app/routers/{documents,customers,projects,companies}.py` | every query through the helper; auto-assign; `_check_links`; scoped delete-all; `assign-unassigned`; product lookup on create as the caller (only when the item has no snapshot) | Task 4 |
| `services/document-service/app/core/catalogue_client.py` | internal calls explicitly superuser-scoped; `get_product(..., scope=)` forwards the caller's scope | Task 4 |
| `services/catalogue-service/app/routers/{products,categories}.py`, `core/duplicates.py` | scoped queries; per-group duplicates; bulk import `group_id`; sub-items same group; `assign-unassigned`; shared-category queries explicit ALL | Task 4 |
| `services/*/app/core/search_client.py`, both `main.py`, `services/search-service/app/routers/search.py` | `group_id` indexed + filterable; `/search` filters by group, fails closed | Task 5 |
| `web/js/app.js` | Group select (create only, when there is a choice; required), bulk-import group, group label in 4 lists, document pickers filtered by group | Task 6 |
| `scripts/assign-unassigned.sh`, `docs/GROUPS.md`, `docs/api.md`, `docs/data-model.md` | added/updated | Task 7 |
| tests: `services/*/tests/test_group_*.py`, `tests/test_group_isolation.py`, `tests/test_gateway_group_headers.py`, `tests/test_ui_group_isolation.py` | added | Task 8 |
| tests: existing fixtures (`scope_headers.py` one-group headers; `conftest.py` "Smoke Tests" group, admin made a member; `group_id` on admin's create calls; a few direct ORM inserts got the test group; head test and a fake query updated) | updated | existing tests assumed "everyone sees everything"; none weakened |

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | catalogue `test_duplicates` (2) | `product not found` / empty groups | test inserted products directly with no group → invisible to a one-group caller | inserted rows get the test group |
| 2 | doc-service `test_customer_organization`, `test_quotation_director_position`, `test_letterhead_header` head test | KeyError / missing `scope` / head ≠ 0008 | same as above; new helper signature; a new migration on top | updated fixtures; head test now asserts "exactly one head, 0008 in the chain" |
| 3 | `assign-unassigned.sh` | `string indices must be integers` | auth-service routes have no `/auth` prefix (Nginx strips it) | `http://auth-service:8000/groups-public` |
| 4 | `test_group_isolation` fixture | `POST /documents` 500 | pre-existing bug: a line item from a product **without a price** crashes (`Decimal("None")`), see §7 | test products get a price; bug recorded, not fixed (out of scope) |
| 5 | `test_ui_group_isolation` | list text empty | read the table before it finished loading | wait for the user's own row first |
| 6 | `test_ui_delete_all` (4) | 422 "Choose a group" | the superuser admin's seed rows had no `group_id` | pass the test group |

## 6. Final verification output
```
$ COMPOSE_PROJECT_NAME=loop-test COMPOSE_OVERRIDE=infra/docker-compose.loop-test.yml GATEWAY=http://localhost:18080 ALLOW_DESTRUCTIVE_TESTS=1 ./scripts/verify.sh --keep
======================= 63 passed, 14 skipped in 23.93s ========================
verify.sh: PASSED (stack left running: --keep)

document-service: python -m pytest tests   → 296 passed in 181.58s
catalogue-service: python -m pytest tests  → 89 passed in 2.60s
search-service: python -m pytest tests     → 16 passed in 0.67s
auth-service: python -m pytest tests       → 94 passed in 47.41s   (throwaway postgres:16)
tests/test_ui_*.py (Playwright 1.65.1, loop-test, destructive allowed) → 31 passed, 6 skipped
```
The 14th skip in verify.sh is the new UI test, which runs in the Playwright container instead.

## 7. Found but not fixed (out of scope)
- **500 on a line item for a product with no price.** At `services/document-service/app/routers/documents.py`, in `_process_items`, `Decimal(str(product["unit_price"]))` runs when `unit_price` is None. I found it with the isolation fixture; it existed before this change. Fix: only take the product's price when it is not None (the request then gets the normal 422).
- **document-service logs go silent after startup.** Alembic's `fileConfig` (in `alembic/env.py`) disables uvicorn's loggers, so request lines and tracebacks are never logged after startup migrations. I fixed this in the new catalogue env.py with `disable_existing_loggers=False`. document-service needs the same one-line change.
- **Dev-only "Delete ALL companies".** Any editor can still wipe the *shared* companies, which affects every group. It is refused in production; it was out of scope.
- **Bulk import as a superuser.** The import uses the Group select shown in the import dialog, which defaults to the superuser's first own group.
- **The VM must discard its local, uncommitted `services/auth-service/app/core/authz.py` edit before pulling.** This iteration changes the same file (adds `get_user_group_ids`, keeps `get_user_allowed_project_ids` as in the repo). The VM-only scripts in `seed-data-setup/scripts/` were not seen and are not affected.
- **After deploying, unassigned data is visible to superusers only** until someone runs `./scripts/assign-unassigned.sh "<group>"`. This is by design: nothing is auto-assigned.

## 8. Blockers / decisions needed
None.

## 9. Suggestions for the next iteration
- Iteration 20 (queued): move records between groups, plus the "Unassigned data" admin card. The endpoints and the helper are ready for it; `group_id` is deliberately not on the Update schemas yet.
- Fix the two small items in §7 (product-without-price 500, document-service logging).

App running at http://localhost:8080 (login: admin / CHANGE_ME, local only)
