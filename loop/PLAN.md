# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **1** |
| Goal | Quotation SUPPLIER block shows the company on the heading line and the selected MD's details below it |
| Attempt limit | 5 per failing check |
| Base branch | `dev` |
| Branch | `loop/iter-1-supplier-md-block` |
| Status | READY FOR CLAUDE CODE |

## Step 0 — harness check (from iteration 0b, never run)
1. `docker pull nginx:alpine` and `pip download pytest==8.3.3 -d /tmp/pipcheck`.
   If either is 403/Forbidden → stop, status `BLOCKED`, list refused domains.
2. Run `./scripts/verify.sh` once as the baseline. Smoke tests that fail
   because of real app bugs → `xfail(strict=True, reason="BUG: ...")`, as
   iteration 0 planned. Fix harness problems (verify.sh, conftest, env).
3. Run the document-service unit tests once and record the baseline:
   `pip install -r services/document-service/requirements.txt -r services/document-service/requirements-dev.txt`
   then `pytest services/document-service/tests -v`.

## What the user wants
Current output (wrong):
```
SUPPLIER
Name        Swift Solutions Co., Ltd.      <- company name
Position    Managing Director
Address     <company address>
Contact No  <company contact>
Email       <company email>
```
Wanted:
```
SUPPLIER: Swift Solutions Co., Ltd.        <- company name moves to the heading
Name        U Wathone Htoo                 <- selected MD's name
Position    Managing Director              <- MD's position
Address     BUILDING NO.4, ...             <- address
Contact No  09-988733966                   <- contact
```

## Rules for each field
| Row | Value | Fallback if empty |
|---|---|---|
| Heading | `SUPPLIER: <company.name>` | `SUPPLIER` alone if no company |
| Name | `director.name` | `—` |
| Position | `director.position` | `company.position`; omit row if both empty (current behaviour) |
| Address | `director.address` | `company.address` |
| Contact No | `director.contact_no` | `company.contact_no` |
| Email | **remove this row** from the Supplier block | — (footer "Customer Service" email stays unchanged) |

`director` is the MD selected on the document (`Document.director_id`,
model `CompanyDirector`). Do not change the data model, the END USER block,
the MD seal/signature block, or the footer.

## Tasks
1. PDF: `services/document-service/app/templates/quotation.html` — Supplier
   block (around lines 145–156). Keep the existing styling and the 48%
   column width rules.
2. XLSX: `services/document-service/app/core/export_quotation.py` — the
   Supplier section (around lines 136–170). Same rules. Update the
   docstring that says company info and director are kept separate.
3. Make sure the export routers in
   `services/document-service/app/routers/documents.py` pass `director`
   (with address/contact_no/position) to both exports. Check, don't assume.
4. Ignore the stray `logo-square-fix/` folder at repo root; it is not used.
5. Tests:
   - Update existing tests whose assertions encode the **old** layout
     (likely `test_quotation_director_position.py`,
     `test_supplier_position_e2e.py`, `test_quotation_pdf_layout.py`).
     Changing an assertion is allowed only where it asserted the old
     Supplier layout; say which ones and why in the report.
   - Add `services/document-service/tests/test_supplier_md_block.py` that
     covers, for both PDF (text via `pypdf`) and XLSX (cells via `openpyxl`):
     a) director with all fields → heading has company name; rows show MD
        name/position/address/contact; no Email row in the Supplier block.
     b) director with empty address/contact → falls back to company's.
     c) no director → Name is `—`, address/contact are company's.
   - Add one smoke assertion in `tests/test_documents_smoke.py`: create a
     company + director, a quotation with that `director_id`, export PDF,
     check the MD's name and `SUPPLIER: <company name>` appear in the text.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | New unit tests pass | `pytest services/document-service/tests/test_supplier_md_block.py -v` all pass |
| A2 | No regressions | Whole `pytest services/document-service/tests` — same or better than baseline from Step 0 |
| A3 | Smoke passes | `./scripts/verify.sh` exits 0 (xfails with `BUG:` reasons allowed) |
| A4 | Visual proof | Render one PDF with a director and convert page 1 to PNG (`pdftoppm -png -r 80 -f 1 -l 1`). Commit it as `loop/evidence/iter-1-supplier.png` and describe what it shows in the report |
| A5 | XLSX proof | Paste the Supplier cells (A/B columns) read back from a generated XLSX into the report |

## Next
User will send the next layout change after reviewing the PNG.
