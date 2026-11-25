# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **31** |
| Goal | **Excel quotation: bigger banner/logo, company seal and director seal** (match the PDF's proportions), and fix four visible Excel glitches from the same screenshots |
| Attempt limit | 5 per failing check |
| Status | READY FOR CLAUDE CODE |

## What the user wants
"In generating excel, all logo and seals and heading are small. Make it bigger in excel. In pdf, the logo size are acceptable." (Screenshot of an exported XLSX: the letterhead banner top-left, the company seal top-right above column F, the director seal under "Regards," — all look tiny next to the table.)

## Part A — bigger images in the XLSX (`services/document-service/app/core/export_quotation.py`)
Today (see the code): banner fitted inside `box_w × 115 px`; header logo and company seal fixed **90×90 px**; director seal fixed **100×100 px**; `row += 6` reserves the header rows.
- **Rule: size the images by the same proportions the PDF uses.** Read the PDF layout (`templates/quotation.html` header/logo/seal CSS, ~lines 70–80 and the signature block) and compute each image's width as a fraction of the content width (e.g. company seal ≈ 120px ≈ 31.75 mm of 184.6 mm → ~17% of the width; banner = full width minus seal). Apply the **same fractions to the sheet's table width** (`sum(col_px)`), so Excel looks like the PDF at any column setup (with or without labour columns). State the fractions in the report.
- **TARGET SIZES drawn by the user** (two screenshots committed as `loop/evidence/iter-31-user-target-header.png` and `iter-31-user-target-md-seal.png` — **look at them**; the red shapes are what the user wants; Excel at ~100% zoom, column F ≈ 111 px wide):
  1. **Banner / logo**: the red rectangle ≈ **560 × 115 px** (it starts at the left of column B and ends around the F column's left part; today the visible banner is ≈ 435 × 85). Aspect ratio preserved: the banner fills that box (fit-inside), never stretched. It may use rows 1–6 and must end above the "QUOTATION" row (the user's rectangle slightly touches row 7 — keep a small gap instead).
  2. **Company seal** (top right): the red circle ≈ **170 px diameter** (today 90 px), centred horizontally near the **F|G column boundary** (x ≈ 830 on the screenshot, i.e. the circle overlaps the left part of G), vertically over rows 1–6. It must not overlap the banner.
  3. **MD / director seal** (signature, second screenshot): the red box ≈ **180 px wide × 200 px tall** area (today 100 × 100), its horizontal centre also near the **F|G boundary** (x ≈ 795 on that screenshot) — i.e. **further left than today** (today it is centred in G). "Regards," stays centred right above the seal at the same centre. The seal image is fitted inside that 180 × 200 box with its own aspect ratio. Reserve enough rows (≈ 10 rows) so nothing overlaps text; the Terms text sits to the left and is not covered.
  Implement these as sizes **relative to the table width** (the PDF-proportion rule above) so they scale when labour columns exist, and make the unit tests assert the absolute px for the default (no-labour) column layout within ±3 px. If the PDF-proportion rule and the user's drawn targets disagree, **the user's targets win** for the default layout; report both numbers.
- **Reserve enough rows** so nothing overlaps text: raise the reserved header rows and set row heights so the taller images end above the title row; the signature block reserves rows for the taller director seal (`seal_row + 7` → compute from the image height ÷ row height). The seal stays centered over the last column(s) and the "Regards," text stays centered above it (existing anchor logic keeps working with the new size).
- **Title "QUOTATION"/"INVOICE" heading**: slightly bigger and bold (≈ 16–18 pt, was smaller) so it matches the larger letterhead; keep it in the same cell and colour.
- Works for both header styles (squared logo and letterhead banner), with and without a company seal, with and without a director seal, with and without labour columns.
- The iteration 22 print setup (fit to one page wide, A4 portrait, print area A1:last) must still produce **one page wide**; bigger images must not push the print area wider or onto a second page horizontally. The taller header may add height — keep "Remarks / terms / signature stay together" behaviour from iteration 14 as the Excel analogue allows; do not regress `test_xlsx_print_setup.py`.

## Part B — four glitches visible in the screenshots (the user's second screenshot confirms images and header are still the old small size, `####` appears when zooming out, the apostrophe and the clipped "Contact N" are still there)
1. **`##########` in the Total cell** (Total: row, column F): the grand total of a big amount (e.g. 11,087,394.00) shows `####` **when the user zooms out** in Excel (at 100% it looks fine). At small zoom Excel renders digits relatively wider than the column, so a column that barely fits at 100% breaks. Fix: size the Price/Amount (and labour) columns with a **safety margin of at least 40%** over the widest formatted value in the document (`#,##0.00` length, bold font for the totals row; min 16, cap ~30) AND turn on **shrink-to-fit** (`Alignment(shrink_to_fit=True)`) on the total/amount cells as a second guard. Test by rendering with LibreOffice at 50% zoom (or by asserting width ≥ 1.4 × formatted length + 2) with totals up to 9,999,999,999.99; never show `####`.
2. **Amount column header shows only "(USD)"**: the header "Total Price (USD)" is clipped because the header row is too short for two wrapped lines. Fix: set the header row height for wrapped two-line text (≈ 32 pt) and make sure both lines show ("Unit Price (USD)" / "Total Price (USD)").
3. **A stray apostrophe on phone numbers**: "Phone: '+959958645521" and "Contact '+95…" show a literal `'`. `_safe_str` (line ~26) prefixes a quote to any value starting with `= + - @` to stop formula injection, but that quote is visible in the cell. Fix: keep injection-safe **without a visible quote** — write such values as real strings (`cell.data_type = "s"` / explicit string type, or rich text) so Excel never treats them as formulas; the injection tests must still prove `=cmd|' /c calc'!A1` is not a formula after loading the file with openpyxl (`data_type != "f"`) and that `+959958645521` and `-5` display exactly as typed.

4. **Header labels clipped in column A**: in the supplier block the label "Contact No" is cut off ("Contact N") because column A is 8 wide and the next cell has text; same risk for "Position" / "Address". Fix: make sure every label in column A is fully visible (shorten to "Contact" or merge A:B for the label with the value in C, or widen A) without changing the PDF or breaking the image anchors.

## Tasks
1. Inspect `export_quotation.py` (image placement, row reservation, column widths, `_safe_str`, header rows) and `templates/quotation.html` (PDF proportions); list the numbers (px, fractions) in the report; render the current XLSX and the PDF of the same document to PNG for a before picture.
2. Part A: implement proportional image sizes + row reservation + title size.
3. Part B: the three fixes.
4. Tests + evidence.

## Tests
- Unit (`services/document-service/tests/`): build the XLSX for a quotation with banner + company seal + director seal; read the drawing anchors/ext from the saved file (openpyxl `ws._images` or the drawing XML): each image's width/height equals the computed fractions of the table width (±2 px), aspect ratio preserved, no image overlaps a cell that has text (images' bottom row < title row; signature seal clear of the text rows). Variants: squared logo header, no seal, no director seal, labour on.
- Print: the iteration 22 LibreOffice check — still 1 page wide with the larger images; `test_xlsx_print_setup.py`, tax and labour XLSX tests unchanged and passing.
- Glitch tests: huge total (e.g. 9,999,999,999.99) → column wide enough (assert width ≥ formatted length + 2) and a LibreOffice render shows no `#`; header row height ≥ 30 and both header lines present in the cell text; phone `+959958645521` and `-5` stored as strings with no leading quote, formula-injection strings stay `data_type == "s"`.
- Evidence: before/after PNGs rendered by LibreOffice: `loop/evidence/iter-31-xlsx-before.png`, `iter-31-xlsx-after.png`, plus `iter-31-xlsx-after-letterhead.png` (banner case) and a PDF page `iter-31-pdf-reference.png` for comparison.
- Existing suites pass; `./scripts/verify.sh` passes; all tests on the throwaway `loop-test` stack / in-process only.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Banner/logo, company seal, director seal sized by the PDF's proportions, never stretched | anchor/size unit tests + screenshots |
| A2 | No overlaps; signature block clear; works with/without seals, labour, both header styles | unit tests + screenshots |
| A3 | Still one page wide when printed; existing XLSX tests pass | LibreOffice print check + suites |
| A4 | No `####` in totals; amount header fully visible | unit + render |
| A5 | No visible apostrophe on phone numbers; formula injection still blocked | unit tests |
| A6 | No regressions; data safety | `./scripts/verify.sh`, report states which stack |

## Out of scope — record under "Found but not fixed"
Changing PDF image sizes (user said acceptable); a per-company "image size" setting; the LibreOffice-only overlapping rich-text Item cells (known BACKLOG item).
