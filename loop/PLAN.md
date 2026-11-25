# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **34** |
| Goal | **Excel layout (user's screenshots 1 and 2):** much bigger header banner, company seal centred in the last column, the END USER block moved into the red rectangle on the right, Qty and Unit centred |
| Attempt limit | 5 per failing check |
| Status | READY FOR CLAUDE CODE |

## What the user wants
Screenshots (look at them): `loop/evidence/user-iter-34-35-img1.png` (Excel top of an INVOICE with red shapes) and `user-iter-34-35-img2.png` (Qty/Unit columns with red arrows). Iteration 35 (queued) handles screenshots 3–5 (print footer and page breaks).

### 1. Header banner: bigger (img 1, red rectangle)
The red rectangle spans **x 28 → 705, y 28 → 225** on a sheet whose table is ≈ 980 px wide, i.e. **≈ 677 × 197 px** from the left edge of column A down to just above the INVOICE/QUOTATION title row (rows 1–9). Iteration 33 made the banner box 625 × 140; make it **677 × 197** (default 987-px sheet; keep the scaling by table width), fit-inside with the image's own aspect ratio (never stretched/cropped; a ≈ 4.8:1 banner ends up ≈ 677 × 141, centred vertically in the box or top-aligned — top-aligned is fine, but then the row reservation must still be computed from the **box height** so the title is not pulled up). Keep at least one empty row between the box and the title row (the user's rectangle touches the title — keep a gap instead). Stays anchored at column A's left edge.
### 2. Company seal: centred in the last column (img 1, red circle)
The red circle: **≈ 190 px diameter, centred horizontally in the last column** (G on the default sheet, x ≈ 906 of 800–1008), vertically over rows 2–9 (centre ≈ the box's vertical middle). Today it is 170 px centred at ≈ 777 (over F|G). Make it **190 px, centred in the last column** (fit-inside, aspect kept). It must not touch the banner (gap ≥ 25 px; banner right edge ≈ 681, seal left edge ≈ 811 → fine) or the END USER block below. With labour columns "last column" is still the Remark column.
### 3. END USER block → the red rectangle (img 1)
The second red rectangle is at **x 775 → 1005, y 265 → 407** (rows 13–19, right side): the **END USER block** (heading "END USER", then Customer Name, Quotation/Invoice ID, Organization, Address) must sit **in the last column (G)**, inside that rectangle, on the same rows as the SUPPLIER block (heading on the SUPPLIER heading's row). Today it starts in column E/F. Layout: heading "END USER" bold; then one line per field in the last column as `Label: value` (label bold or regular like today's labels; value in normal font), left-aligned; **wrap text** and set the row height so a long Address/Organization wraps onto a second line instead of being cut off or overflowing into the table. The supplier block (left) keeps its layout. The label for the ID follows the document type ("Quotation ID" / "Invoice ID") as today. Customer ID row if present today stays. The PDF is unchanged.
### 4. Qty and Unit centred (img 2)
In the item table, the **Qty and Unit cells are horizontally centred** (header already is): every item row, sub-item rows, and with labour columns. Numbers keep their number format; vertical alignment as today (top). Price/Amount stay right-aligned.

## Rules
- Keep everything from iterations 22, 31–33 working: one page wide, print area, MD seal in the last column (iteration 32), "No/Nos" units, labour columns, no `####`, no visible apostrophes, row reservation computed from image heights (never overlapping text).
- Works with and without company seal, with letterhead banner or squared logo, with/without labour, INVOICE and QUOTATION (document-type heading), with and without an end-user organization/address.
- Squared-logo header style: only the seal/END USER/Qty-Unit parts change.

## Tests
- Update `services/document-service/tests/test_xlsx_image_sizes.py` (default layout): banner box 677 × 197 from x ≈ 3 (a 1600×300 image → 677 × 127; a 1000×400 → 492 × 197), seal 190 px, its centre = the last column's centre (±3 px), banner/seal gap ≥ 25 px, nothing covers text (existing overlap test for all variants).
- New/updated unit tests: the END USER cells are in the last column (assert the column letter) and on rows aligned with SUPPLIER; long address → wrap on and row height ≥ 2 lines; Qty and Unit cells `alignment.horizontal == "center"` for item and sub-item rows, also with labour.
- `test_xlsx_print_setup.py`, labour/tax/units/label tests and the rest of the XLSX suite pass; one page wide still verified with LibreOffice.
- Evidence: LibreOffice render of an INVOICE with the Winterfell-style banner, company seal, end user org/address: `loop/evidence/iter-34-xlsx-top.png` (compare with the user's red shapes), and `iter-34-xlsx-qty-unit.png`.
- Existing suites pass; `./scripts/verify.sh` passes (loop-test stack / in-process only).

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Banner box 677 × 197 from column A, aspect kept, gap before the title | unit test + screenshot vs red rectangle |
| A2 | Company seal 190 px centred in the last column, clear of banner | unit test + screenshot vs red circle |
| A3 | END USER block in the last column, aligned with SUPPLIER, long values wrap | unit tests + screenshot vs red rectangle |
| A4 | Qty and Unit centred (item rows, sub-items, labour layout) | unit tests |
| A5 | No regressions; one page wide; data safety | existing XLSX suites, `./scripts/verify.sh` |

## Out of scope — record under "Found but not fixed"
Print footer and page breaks (iteration 35), PDF.
