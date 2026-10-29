# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **7** |
| Goal | When creating/editing a document, the user can **either pick a saved customer or type the customer info by hand (one-time, not saved to the Customers list)** |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## What the user wants
Today the document form (`web/index.html` ~line 1233, `#doc-customer` with a
datalist + "+ New customer") only links a **saved** customer (`Document.customer_id`).
The user wants a choice in the form:

- **Saved customer**: exactly as today.
- **Enter manually**: the user types the customer info for **this document only**.
  It is stored on the document and is **never** added to the Customers list
  (the user confirmed: one time, not saved).

Manual fields:
| Field | Required | Prints in the END USER block as |
|---|---|---|
| Customer Name | yes | Customer Name |
| Organization | no | Organization (falls back to Customer Name if empty) |
| Address | no | Address |
| Phone | no | not printed (stored only) |
| Email | no | not printed (stored only) |

Saved-customer documents keep their current output exactly (Organization still
shows the customer name). Do not add an Organization field to saved customers.

## Backend: `services/document-service`
1. **Model + migration**: add nullable columns to `documents`:
   `end_user_name` (String 255), `end_user_organization` (255), `end_user_address` (Text),
   `end_user_phone` (50), `end_user_email` (255). New migration
   `alembic/versions/0005_document_manual_end_user.py`, guarded/idempotent like
   `0004_company_director_position.py` (it runs automatically at startup via
   `alembic upgrade head` in `app/main.py`). Existing rows stay untouched.
2. **Schemas** (`app/schemas.py`): add the 5 fields to create / update / out.
3. **Validation** (create and full edit in `app/routers/documents.py`):
   - A document has **either** `customer_id` **or** `end_user_name`, never both
     → 422 with a clear message if both are sent.
   - Whatever is allowed today when neither is sent stays allowed (check current behaviour; don't make it stricter).
   - Switching a document from manual to saved clears the 5 manual fields, and saved to manual sets `customer_id` to null.
   - Trim whitespace. An all-blank `end_user_name` counts as not sent.
4. **Exports** (`_gather_export_data` ~line 725, `quotation.html` ~line 211, `export_quotation.py` ~line 173):
   when `customer_id` is null and `end_user_name` is set, build the same
   `customer` dict the exports already use from the manual fields, plus
   `organization` and `address`. Make Organization print
   `organization or name` and Address print the manual address. Saved-customer
   output must not change.
5. **Search**: `app/core/search_client.py` indexes documents. Add `end_user_name`
   and `end_user_organization` to the indexed document so a manual-customer
   document is searchable by those names.
6. Anywhere the API returns or lists a document's customer name for the UI
   (document list, detail), the manual name must show up too.

## Frontend: `web/index.html` + `web/js/app.js`
1. In the document modal, above the customer input: a two-option switch,
   **Saved customer** / **Enter manually** (radio buttons or a small segmented
   control in the existing style). Default: Saved customer.
2. Saved customer: today's input, datalist, hint and "+ New customer" button unchanged.
3. Enter manually: hide those and show 5 inputs (Customer Name*, Organization,
   Address (textarea), Phone, Email). Send them as the `end_user_*` fields and
   send `customer_id: null`.
4. Editing an existing document opens in the right mode with the values filled in.
5. The document list / detail shows the manual customer name where it now shows
   the saved customer's name (`app.js` ~lines 549 and 685 look it up in
   `cachedCustomers`; fall back to `doc.end_user_name`).
6. Keep the style of the existing form. No new libraries.

## Tests
- `services/document-service/tests/test_manual_end_user.py` (unit, like the existing tests):
  1. Create with manual fields → stored; no Customer row created (count unchanged).
  2. Both `customer_id` and `end_user_name` → 422.
  3. Edit manual → saved clears the manual fields; saved → manual nulls `customer_id`.
  4. PDF + XLSX with manual end user: Customer Name, Organization, Address show the manual values; empty Organization → name.
  5. Saved-customer PDF + XLSX END USER output is unchanged.
  6. Migration: running `alembic upgrade head` twice is safe (follow `test_migrations_idempotent.py`).
- Smoke (`tests/test_documents_smoke.py`): through the gateway, create a quotation
  with a manual customer, export the PDF, check the name/org/address text, check that
  `GET /api/customers` doesn't contain it, check search finds the document by the
  manual name (poll ≤ 10 s), then clean up.
- UI proof: use Playwright (Python; `pip install playwright && playwright install --with-deps chromium`)
  against the running stack. Log in, open the New document form, switch to
  "Enter manually", fill it in, save, and confirm the document appears in the
  list with the manual name. Add it as `tests/test_ui_manual_customer.py`, skipped
  automatically if Playwright isn't installed. If Playwright can't be installed,
  record the reason and still do every other check.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | New unit tests pass | `pytest services/document-service/tests/test_manual_end_user.py -v` |
| A2 | No regressions | `pytest services/document-service/tests`: all pass |
| A3 | Smoke passes | `./scripts/verify.sh` exits 0, including the new manual-customer smoke test |
| A4 | UI proof | Screenshots: `loop/evidence/iter-7-form-saved.png`, `iter-7-form-manual.png` (form in each mode) and `iter-7-list.png` (list showing the manual name) |
| A5 | PDF proof | `loop/evidence/iter-7-pdf-manual.png`: crop of the END USER block of a manual-customer quotation |
| A6 | Existing data safe | Start the stack on the old code with a saved-customer document, upgrade to the new code, and confirm that document still opens and exports the same END USER text |
