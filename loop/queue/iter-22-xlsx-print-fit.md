# PLAN.md — Iteration 22 (QUEUED — promote after iteration 21, or earlier if the user says so)

| Field | Value |
|---|---|
| Iteration | **22** |
| Goal | The quotation **XLSX prints with all 7 columns on every page** (No, Item, Qty, Unit, Price, Amount, Remark). Today Excel splits the sheet: columns A–E on one page, Price/Amount/Remark on a second page |
| Attempt limit | 5 per failing check |
| Status | QUEUED |

## What the user reported (with screenshots)
1. Excel sheet: the Remark column is on another page when printing.
2. Print preview shows only the first 5 columns on page 1.
3. The remaining columns (Amount, Remark) and the End-user details print on a separate page 2.
The user does not want this to happen.

## Root cause (already found and reproduced by the planner)
`services/document-service/app/core/export_quotation.py` sets column widths `[8, 55, 8, 8, 16, 16, 30]` (= 141 characters ≈ 10.6 in) but sets **no page setup**:
no fit-to-page, no print area, no paper size, no orientation (only margins and `print_title_rows`). A4 portrait has ≈ 7.3 in of printable width, so Excel breaks the sheet between columns E and F.
Reproduced with LibreOffice: same workbook widths without fit-to-width → 2 pages (page 1 has `No`, page 2 has `Price/Amount/Remark`); with `fitToPage=True, fitToWidth=1, fitToHeight=0, paperSize=A4, print_area=A1:G{last}` → 1 page with all four.

## Decisions already made
- **Fit to 1 page wide, as many pages tall as needed**: `ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)`, `ws.page_setup.fitToWidth = 1`, `fitToHeight = 0`.
- **A4, portrait** (same as the PDF): `paperSize = ws.PAPERSIZE_A4`, `orientation = "portrait"`.
- **Print area** `A1:G{last used row}` so nothing outside A:G (images, stray cells) widens the printed page. Make sure the logo/banner, seal and the "Regards" signature image anchors are inside the area (the banner is fitted inside A:E; the seal is at F1; the signature is placed lower — check each and extend the area if an image would be cut).
- Keep the existing margins and `print_title_rows` (header repeats on every page).
- Center horizontally (`ws.print_options.horizontalCentered = True`).
- If the automatic scale comes out below ≈ 68 % (text too small), narrow the Remark column from 30 to 24 (and only that column) and report the scale before/after. Do not change any other column width, fonts, or layout.
- No change to the PDF export, the data, or the API.

## Tasks
1. Add the page setup to `export_quotation.py` (one small block next to the existing margins code).
2. Make sure every anchored image is inside the print area; adjust the area if needed.
3. Tests + evidence (below). Add a comment explaining why the sheet must be fit-to-width.

## Tests
- Unit test on the generated workbook (openpyxl): `fitToPage`, `fitToWidth == 1`, `fitToHeight == 0`, paper size A4, portrait, `print_area` covers A1:G{last row}, `print_title_rows` unchanged.
- **Real print check with LibreOffice** (`soffice --headless --convert-to pdf`; skip the test with a clear message if `soffice` is not installed in the test image, but run it in this iteration — install it in the test container if needed): for quotations with 3 items, 45 items (multi-page), with tax, with sub-items, with a letterhead banner and seal, with long Remark text:
  - every PDF page contains the column titles including **Remark** (print titles repeat) and **no page contains only some of the columns**;
  - the page count equals the expected number (1 page for 3 items; no blank pages; no page that holds only the right-hand columns);
  - on page 1 the banner/logo image, the seal and the Total row are present; the signature block ("Regards") is not cut off;
  - horizontal check with PyMuPDF word boxes: the right edge of the "Remark" header is inside the page minus the right margin.
- Existing XLSX tests (tax, letterhead anchors, tail layout, etc.) still pass; all document-service tests pass; `./scripts/verify.sh` passes.
- Evidence: `loop/evidence/iter-22-xlsx-print-before-p1.png` / `-p2.png` (the bug: columns split) and `iter-22-xlsx-print-after-p1.png` (fixed), rendered from the LibreOffice PDFs; say that Excel itself could not be run and LibreOffice is the proxy.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Page setup flags set | unit test |
| A2 | All 7 columns on every printed page; no split | LibreOffice print test (3 and 45 items, banner/seal/tax/sub-items) |
| A3 | Images and signature inside the print area | test + `iter-22-xlsx-print-after-p1.png` |
| A4 | Before/after proof | evidence PNGs |
| A5 | No regressions | document-service suite, `./scripts/verify.sh` |
| A6 | Data safety | tests on throwaway stack/in-process only; report says which |

## Out of scope — record under "Found but not fixed"
- Landscape option, other paper sizes, Excel-specific rendering differences.
