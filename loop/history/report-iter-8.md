# report.md — Iteration 8

## 1. Summary
- **Status**: DONE
- **Goal**: Stop duplicate products: **block** exact duplicates, **warn** on same/similar names, **skip + report** duplicates in bulk import, and a **"Possible duplicates"** review page
- **Ponytail skill loaded**: yes. Loaded with the Skill tool (`.claude/skills/ponytail`, level full) before any work
- **Branch / commit**: `loop/iter-8-duplicate-products` @ the commit that adds this report (`loop iter 8: duplicate product detection [DONE]`), merged into `dev`
- **One-paragraph summary**: One shared helper, `catalogue-service/app/core/duplicates.py` (stdlib `difflib`, no new dependency), defines `norm` (casefold + collapse whitespace), exact / same-name / similar (ratio ≥ 0.88, top 5), and the review groups.
  - **Blocking**: create, edit (only when name or description changes) and restore return **409** `{"message": "This item already exists", "existing": {id, sku, name}}` on an exact duplicate of an active product.
  - **New routes**: `GET /products/duplicate-check` and `GET /products/duplicates`, declared before `/{product_id}`, with the same visibility filter as the product list.
  - **Bulk import**: skips existing and within-file duplicates into `skipped`, and warns on same-name rows. Row/file numbering is unchanged.
  - **UI**: the product form pre-checks before saving. An exact match is blocked with a link to the existing product; same or similar names get a dialog with **Use existing** / **Create anyway**. The bulk-import result shows counts, skipped rows and warnings. A **Possible duplicates** view lists exact groups (red), then same-name groups, each row with **Move to trash**.

  All five checks pass: catalogue unit tests 66 → 74, other services unchanged and green, smoke 16 → 18.

## 2. Baseline (before any change)
```
$ ./scripts/verify.sh --keep
============================= 16 passed in 12.65s ==============================
verify.sh: PASSED (stack left running: --keep)

Unit suites (one Python 3.12 venv per service):
auth-service       82 errors   -> psycopg2 "connection to localhost:5432 refused": its conftest needs a real Postgres
                                  (auth_test DB; CI provides one). Started a throwaway postgres:16 on :55432 with
                                  TEST_DATABASE_URL -> 82 passed. Removed afterwards.
catalogue-service  66 passed
search-service     12 passed
document-service  195 passed
```

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | New unit tests pass | PASS | `pytest services/catalogue-service/tests/test_duplicates.py -v` → 8 passed. Against the old app code: `6 failed, 2 passed` (the 2 are `test_norm`, whose new helper file is untracked so it stays, and "same name allowed", which was old behaviour too) |
| A2 | No regressions | PASS | auth `82 passed` (with the throwaway Postgres), catalogue `74 passed` (66 + 8), search `12 passed`, document `195 passed` |
| A3 | Smoke passes | PASS | `./scripts/verify.sh` → `18 passed in 8.51s` … `verify.sh: PASSED`, exit 0. New: `test_duplicate_product_is_blocked_and_reported`, `test_ui_duplicate_products.py`. The first final run failed on a harness issue (§5 attempt 2, §7) |
| A4 | UI proof | PASS | Four screenshots in `loop/evidence/`, listed below |
| A5 | Existing data | PASS | output below |

**A4 screenshots** (`loop/evidence/`):
- `iter-8-exact-block.png`: the product form with a red banner "This item already exists: PRD-0003 UI Dup Shelf-…", the item a link to the existing product. The typed description was `steel,  GREY` against `Steel, grey`, so normalization works.
- `iter-8-warning.png`: the "Similar products already exist" dialog over the form, listing PRD-0003 with **Use existing**, plus **Cancel** / **Create anyway**.
- `iter-8-import-result.png`: "Created 2 products · Skipped 2 duplicates · 1 warning", the created table, a Skipped table (row 1 `already exists` → PRD-0003; row 3 `duplicate of row 2` → PRD-0005), and the warning "Row 4 (…): same name as PRD-0004".
- `iter-8-duplicates-page.png`: "Exact duplicates (1)" in red with the A5 legacy pair PRD-0001 / PRD-0002, then "Same name (1)" with the three UI-test shelves; every row has **Move to trash**.

**A5: pre-existing duplicates.** Two exact duplicates were created on the **old** code, which accepted both (`[(201, 'PRD-0001'), (201, 'PRD-0002')]`; old `/products/duplicates` → 422, treated as an id). Then catalogue-service was rebuilt with the new code against the same DB:
```
list: 200 contains both: True
duplicates exact group: [['PRD-0001', 'PRD-0002']]
edit price: 200 19.99
third copy now: 409 {'message': 'This item already exists', 'existing': {..., 'sku': 'PRD-0001', 'name': 'A5 Legacy Shelf e26003'}}
```
Both also appear on the duplicates page in the UI (`iter-8-duplicates-page.png`). The two rows were deleted afterwards (204, 204).

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `services/catalogue-service/app/core/duplicates.py` | added | Shared helper (Definitions in the plan): `norm`, `key`, `active_products`, `find_exact` (catalogue-wide), `check` (exact / same_name / similar top 5), `groups`, `brief`. The `shortcut:` comment notes the ~10k-product limit and the upgrade path |
| `services/catalogue-service/app/routers/products.py` | `_raise_if_exact_duplicate` (409 shape from the plan) used in create, edit (only if name/description changed; self excluded) and restore. New `GET /duplicate-check` and `GET /duplicates`, placed right after the list route, so before `/{product_id}`. Bulk import: dict lookups built once, then updated per created row; `skipped` added; `warnings` keeps its existing strings and gets `{row, name, reason}` entries appended | Plan backend 1–6 |
| `infra/nginx/nginx.conf.template` | **not changed** | Checked: `location /api/products` (prefix) already covers both new routes with the same `auth_request` / `X-Allowed-Projects` / `X-Access-Level` forwarding |
| `web/js/app.js` | `formatApiErrorDetail` understands `{message, existing}`; `apiFetch` keeps the raw `detail` on the thrown error. The product form calls `duplicate-check` first, only when name/description is new or changed (`exclude_id` when editing). Exact → banner with a link; same/similar → warning dialog; a 409 race → same banner. New `openExistingProduct`, `showProductExistsBanner`, `openProductDupWarning`, `loadProductDuplicates`. The bulk-import result renders counts, a skipped table and both warning shapes | Plan frontend 1–3 |
| `web/index.html` | the warning dialog and Possible-duplicates modal (same markup as the recycle-bin modal); a **Possible duplicates** link above the products table | Plan frontend 3–4. A link, not a header button: a 6th header button wrapped the toolbar at 1280 px (measured: "+ New product" dropped from y=54 to y=79) |
| `services/catalogue-service/tests/test_duplicates.py` | added | Plan tests 1–7, on the SQLite + TestClient pattern from document-service. The bulk test also checks that file 5 still attaches to row 5's product when rows 1 and 3 are skipped |
| `tests/test_catalogue_smoke.py` | added `test_duplicate_product_is_blocked_and_reported` | Plan smoke. Uses the existing `product` fixture, which removes the product |
| `tests/test_ui_duplicate_products.py` | added | Plan UI test (skips without Python Playwright); `UI_SCREENSHOT_DIR` saves the A4 shots |
| `tests/requirements.txt` | `openpyxl==3.1.5` | The UI test builds the bulk-import .xlsx; same pin as the services |
| `loop/history/report-iter-7.md`, `loop/evidence/iter-8-*.png` | moved / added | LOOP 1.2, A4 |

**Plan conflict, resolved.** The plan asks for `warnings: [{row, name, reason}]` and also says to keep the existing response fields exactly. `warnings` was already a list of **strings** (unmatched catalogue files). Both are kept: the strings are unchanged, and the same-name objects are appended to the same list. The only consumer, the web UI, renders both shapes. A file whose row was skipped as a duplicate ends up in the existing "didn't match any spreadsheet row" warning, exactly as for blank rows (the plan's rule).

**Visibility.** The exact-duplicate **block** is catalogue-wide, but the 409's `existing` details are included only when the caller may see that product; otherwise `existing: null` and the message alone. This avoids leaking a hidden product's sku/name to project-restricted users. `duplicate-check` and `duplicates` use the same filter as `GET /products`.

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | Baseline: auth-service unit suite | `connection to server at "localhost" … port 5432 failed: Connection refused` (82 errors) | Its conftest needs a real Postgres `auth_test`, which CI provides; none here | Throwaway `postgres:16` on :55432 + `TEST_DATABASE_URL` → 82 passed. No code change; container removed |
| 2 | A3, first final `verify.sh` | `FAILED at step: wait for health … not healthy after 180s: gateway`; `/api/auth/groups-public` → 401 `missing or invalid gateway credential` | `up -d --build` **recreated** auth-service and catalogue-service (my unit-test runs had put `__pycache__`/`.pytest_cache` into their build contexts). They got swapped IPs (auth .5, catalogue .7), but the long-running nginx had resolved the hostnames at its start, so `/api/auth` went to catalogue-service's gateway check. Confirmed with `docker inspect` + `getent` | `docker compose restart nginx` (gateway → 200), then re-ran `verify.sh`: 18 passed. No code change; the harness fix is proposed in §7 |
| 3 | UI cosmetics (not a failing check) | header wrapped at 1280 px; import-result screenshot cut off | a 6th header button; the results are taller than the modal | Moved the button to a link above the table; scroll the results' last line into view before the shot |

## 6. Final verification output
```
tests/test_auth.py::test_admin_login_ok PASSED                           [  5%]
tests/test_auth.py::test_wrong_password_rejected PASSED                  [ 11%]
tests/test_auth.py::test_protected_route_without_token_rejected PASSED   [ 16%]
tests/test_auth.py::test_protected_route_with_garbage_token_rejected PASSED [ 22%]
tests/test_catalogue_smoke.py::test_category_and_product PASSED          [ 27%]
tests/test_catalogue_smoke.py::test_duplicate_product_is_blocked_and_reported PASSED [ 33%]
tests/test_documents_smoke.py::test_quotation_lifecycle PASSED           [ 38%]
tests/test_documents_smoke.py::test_quotation_pdf_supplier_block_shows_company_heading_and_selected_md PASSED [ 44%]
tests/test_documents_smoke.py::test_quotation_with_one_time_manual_customer PASSED [ 50%]
tests/test_health.py::test_service_reachable_via_gateway[auth-service] PASSED [ 55%]
tests/test_health.py::test_service_reachable_via_gateway[document-service] PASSED [ 61%]
tests/test_health.py::test_service_reachable_via_gateway[catalogue-service] PASSED [ 66%]
tests/test_health.py::test_service_reachable_via_gateway[search-service] PASSED [ 72%]
tests/test_health.py::test_web_ui_served PASSED                          [ 77%]
tests/test_search_smoke.py::test_product_becomes_searchable PASSED       [ 83%]
tests/test_search_smoke.py::test_document_becomes_searchable PASSED      [ 88%]
tests/test_ui_duplicate_products.py::test_duplicate_products_in_the_ui PASSED [ 94%]
tests/test_ui_manual_customer.py::test_create_document_with_manual_customer PASSED [100%]
============================== 18 passed in 8.51s ==============================
verify.sh: PASSED
```
Unit suites: auth 82, catalogue 74, search 12, document 195, all passed. The stack is down.

## 7. Found but not fixed (out of scope)
- **`verify.sh` can fail when a backend is recreated while nginx keeps running** (§5 attempt 2). nginx resolves `proxy_pass http://<service>:8000` once at startup, so after a backend is recreated with a new IP, requests go to whichever container now has the old IP. In production this same staleness would follow any single-service redeploy. Fixes:
  - (a) in `verify.sh`, `docker compose restart nginx` after `up -d --build`, which is the smallest;
  - (b) in `nginx.conf.template`, `resolver 127.0.0.11 valid=10s;` with variable upstreams, which also fixes it at runtime;
  - (c) a `.dockerignore` (`__pycache__`, `.pytest_cache`, `tests/`) in each service, so running unit tests doesn't change the images at all.

  I recommend (a) + (c).
- **auth-service unit tests need a Postgres** at `localhost:5432/auth_test`. `verify.sh` doesn't provide one; CI does.
- **Similar-name search is O(catalogue)** per check, and bulk import uses dict lookups only (no similar check there, as the plan said). This is noted in the `shortcut:` comment.
- **The same-name warning names one existing sku** (the first found), even when several products share the name.
- Still open from earlier iterations: the Customer column confirmation (iteration 7), the footer margin assumption, the Total-row orphan, the MinIO image decision.

## 8. Blockers / decisions needed
None blocking. One to note:
- **Question**: should a project-restricted user's 409 reveal the existing product? It's implemented as no: the block still applies, and the message has no sku/name for a product they can't see.
- **Options**: A) keep (safer); B) always show sku/name.
- **My recommendation**: A.

## 9. Suggestions for the next iteration
- Harden the harness: `docker compose restart nginx` in `verify.sh` plus per-service `.dockerignore` (§7).
- The user reviews the four `iter-8-*.png` screenshots, especially whether the "Possible duplicates" link above the table is visible enough.
