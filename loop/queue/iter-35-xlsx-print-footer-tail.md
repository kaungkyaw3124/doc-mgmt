# PLAN.md — Iteration 35 (QUEUED — promoted by run-all when 34 is accepted)

| Field | Value |
|---|---|
| Iteration | **35** |
| Goal | **Excel printing (user's screenshots 3, 4, 5):** the Customer Service / Email / Phone footer must print on every page like the PDF; Terms & Conditions + Regards + MD seal must stay together at the END (never split, seal never cut by a page break) |
| Attempt limit | 5 per failing check |
| Status | QUEUED |

## What the user reported (look at `loop/evidence/user-iter-34-35-img3.png`, `-img4.png`, `-img5.png`)
- **img 3** (print preview, page with a long table): big blank area under the table (red arrow) — **no footer**. The PDF prints "Customer Service / Email / Phone" at the bottom of **every page** (`.pdf-footer`, running margin box in `templates/quotation.html`); the Excel print has no footer on the page. (In the sheet the block exists once, in rows after the signature: `export_quotation.py` ~line 541 `ws[f"A{row}"] = "Customer Service"`.)
- **img 4**: a printed page shows the end of the table, **Total, Terms and Conditions, "Regards,", and the MD seal cut off at the bottom of the page** ("Winterfell Co., Ltd" half missing). The user: "wrong, it should be showing in last" — the whole tail (Terms + Regards + seal) must be together, on the last page.
- **img 5**: the next page begins with the repeated header block + table header, then only "Customer Service / Email / Phone", and the **bottom of the MD seal image spills onto this page** under the repeated header ("Winterfell Co., Ltd." faintly at the right). Images do not split cleanly at a page break, so the seal must never sit across one.
(The repeated header block on continuation pages — `ws.print_title_rows = f"1:{row}"` — is a deliberate earlier decision (iteration 14); do not change what repeats.)

## What to build
1. **Print footer on every page** (Excel page footer via `ws.oddFooter` / `evenFooter` / `firstFooter` or the same text in all): left-aligned, small font (≈ 8 pt like the PDF's 7 pt lines), three or four short lines exactly like the PDF footer: `Customer Service`, `Email: <support_email>`, `Phone: <phone>` — same data and same rules as the in-sheet block (company support email/phone; omit empty lines; no stray apostrophes; escape `&` as `&&` in footer text because `&` is a control char; keep each section ≤ 255 chars). Reserve enough bottom margin/footer margin so the footer never overlaps the table (page_margins bottom ≈ 0.9 in for 3 lines; keep top/left/right as in iteration 22).
   - Remove the **duplicate**: set the print area to end **before** the in-sheet "Customer Service" block (the block can stay on screen for people viewing the sheet, but must not print twice). Keep it consistent for the document with no support email/phone (no footer lines → no footer, normal margins).
2. **Keep the tail together and last**: the **tail block** = the "Terms and Conditions" heading, its lines, the "Regards," row and the MD seal rows (and the director text when there is no seal). It must **never split across pages** and the seal must never cross a page break:
   - Estimate pagination from the row heights the code already sets and the print scale (fit-to-width scale = printable width ÷ sheet width; A4 portrait; subtract the repeated title rows and the footer margin on pages after the first), accumulate heights, and when the tail does not fit in the space left on the current page, insert a **manual row break** (`ws.row_breaks.append(Break(id=<row before the tail>))`) right before "Terms and Conditions" so the whole tail moves to the next page together. Also make sure no **item row** is left alone on a page with only the tail if avoidable (Excel never splits a row; rows taller than a page are the existing limitation — note it).
   - Terms may be on the same page as the last item rows/Total when everything fits; the requirement is: tail contiguous, last, seal not cut.
   - Because the estimate can be off by a little, leave a safety margin (≈ 5 % of a page) when deciding to break.
3. Do not change what iterations 14, 22 and 31–34 established (one page wide, print titles, images, units, labour…).

## Tasks
1. Inspect `export_quotation.py` (page setup ~lines 360–375, signature/tail ~440–560, print area ~564), the PDF footer content in `templates/quotation.html`/`export_pdf.py` (what lines/data it prints), and render the current XLSX of a long quotation (the user's CCTV-style items: 8–11 items, several with 15–40 text lines) with LibreOffice to PDF to reproduce screenshots 3–5 (before pictures).
2. Footer, print area, tail break logic.
3. Tests + evidence.

## Tests
- Unit: footer text (all three lines present for a company with support email/phone; omitted lines; `&` escaping; no leading apostrophe); `ws.oddFooter` set; print area excludes the in-sheet Customer Service block; bottom margin ≥ footer height.
- Pagination: a pure-function test of the break logic (feed row heights + scale → expected break row) for: short document (no break), tail does not fit (break before Terms), tail fits exactly with the safety margin, very long item rows.
- **LibreOffice end-to-end** (the real check): convert the XLSX of (a) a short quotation, (b) the user's long case (reproducing img 4/5), (c) a 45-item quotation to PDF and read each page's text/images (pypdf + page images or `pdftotext -layout`, whichever is available — the iteration 22 tests already do LibreOffice printing): every page ends with the Customer Service / Email / Phone footer; "Terms and Conditions", "Regards," and the MD seal image are on the **same, last page**; the seal is not cut (its bbox is fully inside one page — check with PyMuPDF/pdfimages image rects if available; otherwise check the seal's text "Managing Director" is on the same page as "Regards,"); the footer is not duplicated on the last page.
- Existing XLSX suites (print setup, image sizes, labour, units, tax) pass; `./scripts/verify.sh` passes (loop-test stack / in-process only).
- Evidence: page PNGs of the LibreOffice PDF for the long case: `loop/evidence/iter-35-print-page1.png`, `iter-35-print-last.png` (footer + tail together), compared with the user's screenshots.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Footer (Customer Service / Email / Phone) on every printed page, not duplicated | LibreOffice PDF test + screenshots |
| A2 | Terms + Regards + MD seal together on the last page, never split | LibreOffice PDF test (long case) + screenshots |
| A3 | MD seal never crosses a page break | image-rect check |
| A4 | Pagination function correct for the listed cases; no regressions; data safety | unit tests, existing suites, `./scripts/verify.sh` |

## Out of scope — record under "Found but not fixed"
PDF (already correct), changing which rows repeat, rows taller than a page, Excel's own estimate differing slightly from LibreOffice (note it and say how the safety margin was chosen).
