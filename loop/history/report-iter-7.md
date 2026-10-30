# report.md — Iteration 7

## 1. Summary
- **Status**: DONE
- **Goal**: When creating/editing a document, the user can **either pick a saved customer or type the customer info by hand (one-time, not saved to the Customers list)**
- **Ponytail skill loaded**: yes. Loaded with the Skill tool (`.claude/skills/ponytail`, level full) before any work, and followed throughout
- **Branch / commit**: `loop/iter-7-manual-customer` @ the commit that adds this report (`loop iter 7: one-time manual customer on documents [DONE]`), merged into `dev`
- **One-paragraph summary**: Documents can now carry a one-time customer stored on the document only. It goes in five new nullable columns, `documents.end_user_name / _organization / _address / _phone / _email`, added by idempotent migration `0005`. No Customer row is ever created.
  - **API**: create and full edit validate the input. Sending both `customer_id` and a name gives 422. Values are trimmed, and a blank name counts as not sent. Switching to a saved customer clears the manual fields; switching to manual leaves `customer_id` null. The manual fields are returned in the document list and detail.
  - **Exports**: the PDF and XLSX END USER block prints the manual name, organization (falling back to the name) and address. Phone and email are stored only. Saved-customer output is byte-for-byte the same text as before.
  - **Search**: the search index gets the manual name and organization.
  - **UI**: the document form has a **Saved customer / Enter manually** radio switch, using the existing `.checkbox-list` radio pattern. Edit opens in the right mode. The detail view shows the manual name.
  - **Deviation (see §4)**: the documents list had no customer column at all, so I added one to meet A4. It shows saved or manual names.

  All six acceptance checks pass: unit tests 187 → 195 passed, smoke 14 → 16 passed (including the new Playwright UI test).

## 2. Baseline (before any change)
The first attempt failed at `docker compose up` with `Cannot connect to the Docker daemon`: the daemon wasn't running. My `pgrep … ||` guard skipped starting it. I started `dockerd` explicitly (ready after 1 s) and re-ran:
```
$ ./scripts/verify.sh --keep
============================== 14 passed in 5.08s ==============================
verify.sh: PASSED (stack left running: --keep)

$ pytest services/document-service/tests
187 passed in 105.46s (0:01:45)
```
The stack was kept up on the **old** code to seed the A6 document (§3).

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | New unit tests pass | PASS | `pytest services/document-service/tests/test_manual_end_user.py -v` → 8 passed. Against the old app code: `6 failed, 2 passed` (the 2 are "saved-customer output unchanged" and the migration test, which should pass either way) |
| A2 | No regressions | PASS | `pytest services/document-service/tests` → `195 passed in 114.14s` (187 + 8). One fixture line updated (§4) |
| A3 | Smoke passes | PASS | `./scripts/verify.sh` → `16 passed in 5.25s` … `verify.sh: PASSED`, exit 0. Includes `test_quotation_with_one_time_manual_customer` (PDF text, not in `/customers`, searchable by name within 10 s) and `test_ui_manual_customer.py` |
| A4 | UI proof | PASS | Five screenshots in `loop/evidence/`, listed below |
| A5 | PDF proof | PASS | `loop/evidence/iter-7-pdf-manual.png`: END USER block reading Customer Name `Daw Hla Hla`, Quotation ID, Organization `Golden Lotus Trading Co., Ltd.`, and Address `No. 5, Pansodan Road, Kyauktada Township, Yangon` (wrapped). No phone or email is printed |
| A6 | Existing data safe | PASS | output below |

**A4 screenshots** (`loop/evidence/`):
- `iter-7-form-saved.png`: the switch on "Saved customer", with today's input and "+ New customer".
- `iter-7-form-manual.png`: "Enter manually" selected; the saved input is hidden and the 5 fields are filled.
- `iter-7-list.png`: the list's new Customer column shows `UI Manual Buyer-…` for the new document and the saved name for another.
- `iter-7-detail.png` (extra): the detail view shows `Customer: UI Manual Buyer-…`.

**A6: old data, upgraded in place.** The stack ran the old code. Through the API I created a saved customer (with a billing address) and a quotation for it, and recorded its PDF and XLSX END USER output. Then I ran `docker compose up -d --build document-service` with the new code, against the same Postgres volume:
```
document-service-1 | INFO [alembic.runtime.migration] Running upgrade 0004_director_position -> 0005_document_manual_end_user, add documents.end_user_* (one-time manual customer)
alembic_version = 0005_document_manual_end_user; \d documents shows the 5 new nullable columns

$ a6.py check
detail customer_id: 32e5381a-470d-434a-9256-21401b6a4d23 end_user_name: None
SAME        <- PDF END USER text and XLSX END USER cells identical to before the upgrade

# restarted document-service (startup runs `alembic upgrade head` again):
boots (Context impl lines): 2
Running upgrade lines: 1      <- second upgrade on real Postgres was a no-op
traceback/error lines: 0
SAME
```
The recorded "before" output was PDF `Customer Name A6 Legacy Customer 180963 / Quotation ID DOC-20261029/001 / Organization A6 Legacy Customer 180963 / Address Yangon, No. 12, Legacy Road`, and XLSX had the same values in E/F. The seed rows were deleted afterwards (both 204).

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `services/document-service/app/models.py` | +5 nullable columns on `Document` | Plan backend 1 |
| `services/document-service/alembic/versions/0005_document_manual_end_user.py` | added | Plan backend 1. Each `add_column` is guarded by an existence check (same pattern as 0004); revision id is 29 chars (≤ 32) |
| `services/document-service/app/schemas.py` | the 5 fields on `DocumentCreate`, `DocumentUpdate`, `DocumentOut` | Plan backend 2 and 6 (list and detail return them) |
| `services/document-service/app/routers/documents.py` | `_manual_end_user(payload)`: trims; blank name → all five None; name plus `customer_id` → 422 `send either customer_id (saved customer) or end_user_name (one-time customer), not both`. Used in create and full edit; edit assigns all five, so switching modes clears the other side. `_gather_export_data` builds `{"id": None, "name", "organization", "address"}` when there is no `customer_id` and a name is set | Plan backend 3 and 4. A document with neither is still allowed (unchanged) |
| `services/document-service/app/core/export_pdf.py`, `app/templates/quotation.html` | Organization prints `organization or name`; Address prints the manual address, else the saved billing address as before | Plan backend 4 |
| `services/document-service/app/core/export_quotation.py` | same Organization/Address rule in the XLSX, still via `_safe_str` | Plan backend 4 |
| `services/document-service/app/core/search_client.py` | `end_user_name`, `end_user_organization` in the indexed record and in `search_text` | Plan backend 5. The index has no `searchableAttributes` limit, so adding the fields makes them searchable |
| `services/document-service/tests/test_quotation_director_position.py` | added `end_user_name=None` to one `SimpleNamespace` fake Document | `_gather_export_data` now reads that column; real ORM rows always have it. This is fixture upkeep, not an assertion change |
| `services/document-service/tests/test_manual_end_user.py` | added | Plan tests 1–6, plus blank name / no customer still allowed. The migration test runs 0005's `upgrade()` twice on SQLite against an existing `documents` row |
| `tests/test_documents_smoke.py` | added `test_quotation_with_one_time_manual_customer` | Plan smoke test |
| `tests/test_ui_manual_customer.py` | added | Plan UI proof. `pytest.importorskip("playwright.sync_api")`, so it is skipped where Python Playwright isn't installed (it is **not** in `tests/requirements.txt`). Set `UI_SCREENSHOT_DIR` to save screenshots |
| `web/index.html` | the radio switch, a wrapper around today's saved-customer input/hint/button (unchanged), the 5 manual inputs and a "not saved to Customers" hint; **Customer column** in the documents table (colspan 10 → 11); tighter side padding (11 px instead of 16 px) for that table only | Plan frontend 1–3 and 6. Phone and email use `type="text"` with `inputmode`, because only text/password inputs get the form styling |
| `web/js/app.js` | `setDocCustomerMode` / `docCustomerMode`. Submit sends `customer_id: null` + `end_user_*` in manual mode (banner if the name is empty), else `end_user_*: null`. Edit fills the fields and picks the mode. `docCustomerName(doc)` (saved name, else `end_user_name`) is used by the list and the detail view. The list re-renders once customers load | Plan frontend 3–5 |
| `loop/history/report-iter-6.md`, `loop/evidence/iter-7-*.png` | moved / added | LOOP 1.2, A4, A5 |

**Deviation, with reason: a Customer column in the documents list.** Plan frontend 5 says to show the manual name "where it now shows the saved customer's name", and A4 and the UI test require the list to show it. The list had **no** customer column for any document; only the edit form and detail view showed customer names. To meet A4 I added one column, which shows saved and manual names alike. At 1280 px the 11-column table then overflowed its container by 48 px (measured 1012 vs 964 px), clipping Delete. Trimming that table's cell side padding to 11 px makes it fit exactly (964 = 964); at 1440 px it is 1124 = 1124. Fonts and every other table are unchanged.

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | Baseline `verify.sh` | `Cannot connect to the Docker daemon` | dockerd not running; my start guard didn't fire | Started dockerd explicitly; baseline 14 passed |
| 2 | A2 (2 tests in `test_quotation_director_position.py`) | `'types.SimpleNamespace' object has no attribute 'end_user_name'` | Their fake Document predates the new column | Added `end_user_name=None` to the fake (§4) |
| 3 | UI test | `Page.wait_for_function: EvalError … violates … Content Security Policy … script-src 'self'` (seen in a debug run) | The app's CSP blocks Playwright's string-eval polling | Used `expect(locator).not_to_have_value("")` |
| 4 | UI test | modal stayed open; banner: `Row 1: needs either a product, or a remark describing what this line item is` | **Test input**: the app's existing rule says a free-text line needs a remark | The test fills `.li-remark` |
| 5 | UI test (new list assertion) | `element(s) not found` for `tr` `has=` locator | **Test bug**: a `has=` locator is relative to the row, so it can't start with `#documents-tbody` | Matched `[data-view-doc-id='<id>']` |

These are separate root causes, not repeats; there were no more than 2 attempts on any one check.

## 6. Final verification output
```
tests/test_auth.py::test_admin_login_ok PASSED                           [  6%]
tests/test_auth.py::test_wrong_password_rejected PASSED                  [ 12%]
tests/test_auth.py::test_protected_route_without_token_rejected PASSED   [ 18%]
tests/test_auth.py::test_protected_route_with_garbage_token_rejected PASSED [ 25%]
tests/test_catalogue_smoke.py::test_category_and_product PASSED          [ 31%]
tests/test_documents_smoke.py::test_quotation_lifecycle PASSED           [ 37%]
tests/test_documents_smoke.py::test_quotation_pdf_supplier_block_shows_company_heading_and_selected_md PASSED [ 43%]
tests/test_documents_smoke.py::test_quotation_with_one_time_manual_customer PASSED [ 50%]
tests/test_health.py::test_service_reachable_via_gateway[auth-service] PASSED [ 56%]
tests/test_health.py::test_service_reachable_via_gateway[document-service] PASSED [ 62%]
tests/test_health.py::test_service_reachable_via_gateway[catalogue-service] PASSED [ 68%]
tests/test_health.py::test_service_reachable_via_gateway[search-service] PASSED [ 75%]
tests/test_health.py::test_web_ui_served PASSED                          [ 81%]
tests/test_search_smoke.py::test_product_becomes_searchable PASSED       [ 87%]
tests/test_search_smoke.py::test_document_becomes_searchable PASSED      [ 93%]
tests/test_ui_manual_customer.py::test_create_document_with_manual_customer PASSED [100%]
============================== 16 passed in 5.25s ==============================
verify.sh: PASSED
```
Unit tests: `pytest services/document-service/tests` → `195 passed in 114.14s`. The stack is down.

**Playwright**: `pip install playwright==1.63.0` into `.venv-tests`, matching the preinstalled Node Playwright 1.63.0. I did **not** run `playwright install --with-deps chromium`: this environment ships Chromium under `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers` and says not to install another one, and the preinstalled build launched fine (Chromium 153). On a machine without Python Playwright, the UI test is skipped and `verify.sh` still passes.

## 7. Found but not fixed (out of scope)
- **The documents list has the same load-order race for project names**: `projectLabel()` reads `cachedProjects`, which may not be loaded yet on first render. I fixed this only for the new Customer column, by re-rendering once customers load.
- **Saved-customer addresses print in JSON key order** (e.g. `Yangon, No. 12, Legacy Road`: Postgres JSONB sorts keys, so `city` comes before `line1`). This predates this iteration; A6 confirms it is unchanged. Fix: join a fixed key order (`line1, line2, city, …`).
- **Manual email/phone are not validated** (free text, like the existing customer fields). Stored only, not printed.
- Still open from earlier iterations:
  - the footer bottom margin assumes a 3-line footer (iteration 3 §7);
  - the Total-row orphan (iteration 2 §7);
  - the MinIO image decision (iteration 1 §8).

## 8. Blockers / decisions needed
None blocking. One thing to confirm:
- **Question**: Keep the new **Customer** column in the documents list? The plan assumed one already existed; I added it to meet A4, with slightly tighter cell padding so the table still fits 1280 px screens.
- **Options**: A) keep it (shows saved and manual names); B) remove it and show the manual name only in the detail view and edit form.
- **My recommendation**: A.

## 9. Suggestions for the next iteration
- The user reviews `loop/evidence/iter-7-*.png`, especially the form layout and the new list column.
- Consider adding `playwright` to a separate `tests/requirements-ui.txt` if the UI test should run in CI.
