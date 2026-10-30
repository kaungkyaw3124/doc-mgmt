# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **12** |
| Goal | Clean up the Company form: drop the duplicate **Signer position**, make **MD position required** (default "Managing Director"), relabel the footer fields, and limit **Short name** to 2–6 letters |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening` / `main`, all identical now) |
| Status | READY FOR CLAUDE CODE |

## Why
In the Company form (`web/index.html` ~lines 1170–1200) the user typed company
data into the wrong fields (the screenshot shows Short name = "Winterfell Co.,Ltd"
and Signer position = "Winterfell Co.,Ltd"). The field roles are unclear:
- **Signer position** (`Company.position`) only acts as a fallback when the MD
  has no position (`quotation.html` ~line 202, `export_quotation.py` ~line 164).
  Each MD already has its own `position`, so this field duplicates it.
- **Contact No** / **Support email** print in the **Customer Service footer**
  on every page. They are the company's office line.
- **Address** is the fallback when the MD has no address.
- **Short name** is the document number prefix (`documents.py` ~line 154), e.g. `SS-20261030/001`.

## Tasks
1. **Remove Signer position from use**:
   - UI: remove the `company-position` field from the Company form and any JS that reads or writes it.
   - API: stop accepting `position` on company create/update (ignore it if sent, so
     old clients don't break), and drop it from `CompanyOut`.
   - Exports: Supplier **Position** = `director.position` only (no company fallback).
   - **Keep the DB column** (no drop, no data loss). Add a comment saying it's unused since iteration 12.
2. **MD position required**:
   - UI: in the MD rows of the Company form, Position is required and prefilled with
     "Managing Director" for a new MD.
   - API: MD create/update rejects an empty or blank position (422). If `position` is
     missing on create, default it to "Managing Director".
   - **Data migration** (document-service Alembic `0007_md_position_default.py`,
     guarded/idempotent): for MDs whose `position` is NULL or blank, set it to
     the company's `position` if that's non-blank **and not equal to the company
     name**, otherwise "Managing Director". Log how many rows changed. This keeps
     what existing quotations print, but doesn't copy the obvious junk from the
     screenshot.
3. **Relabel** (UI labels only, no API rename):
   - Address → **Office address** (hint: "Used when the MD has no address")
   - Contact No → **Customer service phone** (hint: "Printed in the footer of every quotation")
   - Support email → **Customer service email** (same hint)
4. **Short name**:
   - UI: label **Short name (2–6 letters/digits, used in document numbers, e.g. "WF")**,
     `maxlength=6`, auto-uppercase.
   - API: on create/update, validate `^[A-Za-z0-9]{2,6}$` after trimming; store uppercase; 422 with a clear message otherwise.
   - **Existing invalid values are not changed automatically.** Editing such a
     company forces a valid one; until then document numbering keeps working as
     today. In the Company list, mark a company with an invalid short name with a
     small warning ("Short name needs fixing").
   - Document numbering is unchanged for valid values.

## Tests (`services/document-service/tests/test_company_cleanup.py`)
1. Create/update company without `position` → OK; with `position` → ignored (not
   returned, not used in exports).
2. Supplier Position in PDF and XLSX = MD position; MD without position can't be created via API (422) — default on missing.
3. Migration: MD with NULL position + company position "Director" → "Director";
   company position equal to company name → "Managing Director"; blank → "Managing Director";
   running twice is safe.
4. Short name: "wf" → stored "WF"; "W", "TOOLONG7", "Winterfell Co.,Ltd", "W F" → 422;
   an existing company with an invalid short name still numbers documents as before.
5. Update existing tests that relied on the company-position fallback (list them in the report).
- UI (Playwright): the Company form has no Signer position; the labels are the new
  ones; a new MD row is prefilled with "Managing Director"; short name "wf" saves as "WF";
  "Winterfell Co.,Ltd" is rejected with the message.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | New tests pass | `pytest services/document-service/tests/test_company_cleanup.py -v` |
| A2 | No regressions | all service unit suites + `./scripts/verify.sh` pass |
| A3 | Existing data | Before/after on a DB with: a company with position "Director" and an MD with no position; a company whose short name is "Winterfell Co.,Ltd". Paste the PDF Supplier block text and a generated document number for each |
| A4 | UI proof | `loop/evidence/iter-12-company-form.png`, `iter-12-shortname-error.png` |
