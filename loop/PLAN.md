# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **14** |
| Goal | Quotation PDF/XLSX: (1) remove the "Items" group row at the top of the items table; (2) the last item, the Subtotal/Tax/Total rows, the Terms and the MD signature always end up together on the LAST page, never stranded apart |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## What the user wants (two screenshots)
1. **Remove the "Items" text row.** The first row of the items table is a full-width
   italic row reading "Items" (the user circled it). It is `group_label` in
   `services/document-service/app/templates/quotation.html` (~line 229,
   `<tr class="group-header">`) and in `app/core/export_quotation.py` (~line 218,
   merged row A:G). Remove that whole row from **both** the PDF and the XLSX.
   Do not just blank the text: no empty grey row may remain. If every item has the
   same category, the row used to show the category name instead of "Items" —
   remove it in that case too (the user wants no group row at all).
2. **Don't strand the signature.** On a multi-page quotation the last page of items ends
   with the Subtotal/Tax/Total rows, then Terms + "Regards," + the MD seal do not fit
   under them, so Terms and the signature move to a new page by themselves (the seal
   sometimes lands on a third page). The user wants the **last item row and the
   Subtotal / Tax / Total rows moved down so they appear on that last page**, together
   with Terms and Conditions and the MD signature. Page 1 (and any middle page) just
   ends one row earlier.

   Desired result on the last page, top to bottom: (repeated header) → the last item
   (with its sub-item rows, if it has any) → Subtotal / Tax / Total → Terms and
   Conditions → Regards + MD seal.

## Why this is risky (read before touching CSS)
The pagination rules in `quotation.html` and `tests/test_quotation_pdf_layout.py`
(read the comment block at the top of that test file) are the result of several
failed attempts: `.terms` has `break-inside/after: avoid` paired with `.md-block`
`break-before: avoid`. **Do not remove or weaken those.** Also `tests/test_header_repeats.py`,
`tests/test_page_margins.py` and `tests/test_md_regards.py` encode other layout decisions.
Tackle task 2 only by *adding* keep-together rules and re-run the full suite after each try.

## Tasks
1. **Remove the group row** (PDF template + XLSX exporter). In the XLSX also shift the
   following rows correctly (item rows start right under the header row, merged
   cells / borders / freeze panes / print area still correct, tax and total rows
   unchanged). Check `tests/test_quotation_xlsx_tax.py` — update only the row numbers
   that moved because of this task, never the assertions about values.
2. **Keep the tail together.** Make the last item *group* (the last item row plus its
   `sub-item` rows), the Subtotal/Tax/Total rows, `.terms` and `.md-block` stay on one
   page: if they do not all fit on the current page, the whole tail starts on the next
   page. Try in this order and stop at the first that passes all tests:
   - **A. CSS only**: `break-after: avoid` / `page-break-after: avoid` on the last
     item row, on every sub-item row of the last item, and on the Subtotal/Tax rows;
     `break-before: avoid` on the Total row; keep `.terms`/`.md-block` rules as they are.
     (WeasyPrint's support for break rules between table rows is partial, so verify with
     real renders, not by reading the CSS.)
   - **B. Structure change** if A cannot work: split the table so the tail (last item
     group + totals) is a separate block with `break-inside: avoid`, wrapped together with
     `.terms` and `.md-block` in one container with `break-inside: avoid`. The tail must
     keep the same column widths as the main table (use `table-layout: fixed` and shared
     `<col>` widths) so the columns line up perfectly across the page break. If the tail
     is ever taller than one page, it must fall back to normal flow (no infinite loop,
     no clipped content).
   Whatever approach: the header (logo/letterhead, QUOTATION, SUPPLIER/END USER) still
   repeats on every page, and the footer still appears on every page.
3. **No other layout changes.** Margins, fonts, colours, logo size, footer all stay as they are.

## Tests (add to `services/document-service/tests/test_quotation_tail_layout.py`, no Docker needed — use the same approach as `test_quotation_visual_style.py`: render with the real `generate_quotation_pdf`, read the WeasyPrint box tree or pypdf page text)
1. **Sweep**: for every item count N from 1 to 45 (also with `with_tax`, and with a
   director that has a seal, and with a director that has no seal, and with a last item
   that has 3 sub-items) assert:
   - the last item's text, "Total:" and "Terms and Conditions" and the "Regards," line are all on the **same, final page**;
   - "Total:" never appears on a page that does not also contain the last item row;
   - no page's body consists only of Terms/Regards (a final page must also contain at least the last item row);
   - the page count is never more than one higher than the same render before this change (no explosion of pages).
2. The text "Items" is not rendered as a table row in the PDF (render with category-less
   products and with all-one-category products), and no empty row sits between the table
   header and the first item. XLSX: the first cell under the header row is item 1.
3. Tall-tail fallback: a last item with ~25 sub-items still renders, nothing clipped, no exception.
4. All existing tests pass unchanged except the XLSX row-number shifts allowed in Task 1.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | New tests pass | `cd services/document-service && pytest tests/test_quotation_tail_layout.py -v` |
| A2 | No regressions | `./scripts/verify.sh` passes (incl. `test_quotation_pdf_layout.py`, `test_header_repeats.py`, `test_page_margins.py`, `test_md_regards.py`) |
| A3 | Proof: before | `loop/evidence/iter-14-before-p1.png`, `iter-14-before-p2.png` — a 14-item quotation rendered on the unchanged code (page 1 with totals, page 2 with only Terms/Regards) |
| A4 | Proof: after | `loop/evidence/iter-14-after-p1.png`, `iter-14-after-p2.png` (same data: last item + totals + terms + seal together on the last page, no "Items" row) and one more pair with sub-items |
| A5 | XLSX | open the generated .xlsx with openpyxl in the report: print rows 1–25 of the quotation sheet showing no "Items" row and correct totals |

Render PDFs to PNG with `pdftoppm -r 70 -png`. Use a director **with** a seal image
(a generated 300x300 PNG is fine) for the after-screenshots, since the seal is what
used to overflow.

## Out of scope — record under "Found but not fixed" if you notice them
- The Excel export has no page breaks of its own; do not try to replicate the PDF page logic there.
- Anything about letterhead/logo header style (separate branch `letterhead-header`).
