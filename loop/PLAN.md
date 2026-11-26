# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **39** |
| Goal | **Excel: centre the "No" (item number) column horizontally** |
| Attempt limit | 5 per failing check |
| Status | READY FOR CLAUDE CODE |

## What the user wants
Screenshot `loop/evidence/user-iter-39-no-column.png`: the item numbers (1, 2, 3 …) in the first column "No" are right-aligned; the user wants them **horizontally centred** like Qty and Unit already are (iteration 34). The header "No" is already centred.

## What to change (`services/document-service/app/core/export_quotation.py`)
- In the item rows (`values = [idx, …]` loop, ~line 441) set `horizontal="center"` for **column 1** as well: today `horizontal="center" if col in (3, 4)`; make it `col in (1, 3, 4)`. Same in the sub-item rows (~line 474) for whatever cell carries the sub-item number (e.g. "1.1", "1.2") if that column is the No column; keep its text/format as is.
- Vertical alignment stays top; number format and borders unchanged; works with and without labour columns; the Qty/Unit centring from iteration 34 stays.
- Nothing else changes (PDF already centres its No column — verify in `templates/quotation.html` and report; do not change it unless it is not centred).

## Tests
- Update the iteration 34 alignment test (in `services/document-service/tests/test_xlsx_image_sizes.py` or wherever Qty/Unit centring is asserted): `alignment.horizontal == "center"` for column A of every item row and sub-item number cells, with labour on and off; Price/Amount stay right-aligned; the Item column stays left/wrap.
- XML hygiene test from iteration 38 and all XLSX suites pass; `./scripts/verify.sh` passes (loop-test stack / in-process only).
- Evidence: LibreOffice render `loop/evidence/iter-39-xlsx-no-column.png`.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | "No" cells centred in all item and sub-item rows (labour on/off) | unit test + screenshot |
| A2 | No regressions; data safety | existing XLSX suites, `./scripts/verify.sh` |
