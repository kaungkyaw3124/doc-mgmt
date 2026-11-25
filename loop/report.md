# report.md — Iteration 35

## 1. Summary
- **Status**: DONE
- **Goal**: Excel printing (user's screenshots 3, 4, 5): the Customer Service / Email / Phone footer must print on every page like the PDF; Terms & Conditions + Regards + MD seal must stay together at the END (never split, seal never cut by a page break)
- **Ponytail skill loaded**: yes (active from the session start hook, followed throughout)
- **Branch / commit**: `dev` @ see `git log` (commit `loop iter 35: ...`)
- **One-paragraph summary**: The XLSX now has a real Excel page footer (`oddFooter`, left side, Arial 8pt): **Customer Service** (bold) / `Email: …` / `Phone: …`. It prints at the bottom of every page, and the bottom margin is 0.9in so it never overlaps the table. The print area now stops before the on-screen Customer Service block, so the footer does not print twice. The tail is kept together by a different mechanism than the plan described. **Excel ignores manual page breaks while "Fit to 1 page wide" is on** (Microsoft's docs say so), so a row break before Terms would have worked in LibreOffice but not for the user in Excel. The rule both apps do follow is that a row is never split across pages. So Terms, "Regards," and the MD seal now share **one row**: Terms (with its heading) in A:F, and "Regards," in the last column on the line after the last Terms line (same look as before), with the seal anchored below it inside that row. If that row does not fit on a page, the whole row moves to the next page. The seal can never be cut, and Regards can never be separated from Terms. Very long custom Terms (more than ~20 lines, since Excel rows max out at 409pt) put their first lines in ordinary rows above the tail row.

## 2. Baseline (before any change)
Document-service unit suite, run in `doc-test-lo` (LibreOffice + PyMuPDF) before any edit:
```
409 passed in 227.57s (0:03:47)
```
`verify.sh` on the loop-test stack: `100 passed, 25 skipped in 49.37s`. Caveat: it ran right after the unit suite, after my first (later discarded) draft had been edited in. It is a black-box suite that does not inspect XLSX layout; the last clean run (iteration 34) was the same 100 passed / 25 skipped.

Reproduction (before): the old code's LibreOffice render of the 58–70-item cases put "Terms and Conditions" on page 1 and the MD seal on page 2. No page had a footer (see §5, mutation run).

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | Footer on every printed page, not duplicated | PASS | `test_footer_on_every_page_and_tail_together_on_the_last[3,45,50,54,58,62,66,70,120]` (LibreOffice → PDF): on every page, `"Customer Service"` appears **exactly once**, `Email: sales@acme.example` and `Phone: +95 1 234 5678` are present, and the footer sits in the bottom inch. Unit: `test_footer_lines_like_the_pdf` (all lines / email omitted / none → no footer), `test_footer_is_set_escaped_and_has_room` (`r&d@…` → `r&&d@…`, no `'`, 8pt, real line breaks instead of `_x000a_`, ≤255 chars, bottom margin ≥ footer margin + 3 lines), `test_no_support_contact_means_no_footer_and_normal_margin` (bottom 0.5), `test_print_area_ends_before_the_on_screen_customer_service_block`. Screenshots: `loop/evidence/iter-35-print-page1.png`, `iter-35-print-last.png` |
| A2 | Terms + Regards + MD seal together on the last page | PASS | Same LibreOffice test: the tail text appears only on the **last** page, Terms above Regards, and the seal below Regards on that page. The 50–70 sweep walks the end of the table across the page-1 boundary: at 56+ items the whole tail moves to page 2 together (`iter-35-print-last.png`). Unit: `test_terms_regards_and_seal_share_one_row[plain/labour]`, `test_without_a_seal_name_and_position_are_in_the_tail_row`, `test_very_long_terms_spill_into_rows_above_the_tail_row` (60 lines, row ≤ 409pt, no line lost) |
| A3 | MD seal never crosses a page break | PASS | Image check in the LibreOffice test: the seal's bbox lies between Regards and the footer on one page, and its printed shape keeps the 200×220 ratio within 3% (a cut seal prints shorter). Unit: the seal is anchored in the tail row and its bottom is above the row's bottom, row ≤ 409pt |
| A4 | Pagination correct for the listed cases; no regressions; data safety | PASS | The cases the plan lists (short doc, tail doesn't fit, tail near the boundary, long documents) are covered by the 3/45/50…70/120-item LibreOffice sweep instead of a pure break function, because the break function was dropped (see §8). Document unit suite **426 passed** (409 + 17 new). `verify.sh` **100 passed, 25 skipped** (loop-test stack). Data safety: see below |

**Data safety:** unit and LibreOffice tests ran in-process in the `doc-test-lo` container (no DB). `verify.sh` ran on the throwaway `loop-test` stack (port 18080), removed afterwards with `./scripts/loop-test-down.sh`. The user's stack was only backed up and rebuilt with `up -d --build` (no volume removal).

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `services/document-service/app/core/export_quotation.py` | modified | `_footer_lines` + `_Footer` (Excel page footer; openpyxl writes line breaks as `_x000a_`, which LibreOffice prints literally, so the footer string is written with real line breaks); bottom margin 0.9in when there is a footer; print area ends at the tail row; Terms + Regards + seal in one row (`_line_count`, `LINE_PT`, `HEADING_PT`, `MAX_ROW_PT`); `_place` takes a y offset |
| `services/document-service/tests/test_xlsx_print_footer_tail.py` | added | 17 tests (footer unit tests, tail row unit tests, LibreOffice print sweep) |
| `tests/test_md_regards.py`, `tests/test_xlsx_image_sizes.py` | modified | "Regards," is now the end of the tail row's last-column text rather than a row of its own. Same checks: bold, centred, same column, above the seal, centre within 15px. The overlap test now compares pixel boxes (an image's y offset vs a top-aligned cell's text lines) rather than whole rows; `_rect` now counts the image's row offset |
| `tests/test_page_margins.py` | modified | bottom margin 0.5 → 0.9 (room for the footer the plan asked for) |
| `tests/test_xlsx_print_setup.py`, `tests/test_labour.py` | modified | print area ends at the tail row, no longer the last used row (the plan's de-duplication) |
| `loop/evidence/iter-35-print-page1.png`, `iter-35-print-last.png`, `iter-35-before-lo-long-case.png` | added | LibreOffice render: 56 items, letterhead + seals. Page 1 with the footer; page 2 with the whole tail + footer. Before: old code, no footer |

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | LO render of the long case | all 11 items on 1 page, overlapping | LibreOffice doesn't grow rich-text rows (known backlog), so pagination can't be reproduced from item rows | Tried explicit item row heights + a page-break estimator with a manual break per page |
| 2 | Plan's manual-break design | — | Microsoft docs: **Excel ignores manual page breaks under Fit To**. Tried a fixed scale instead: LibreOffice then split the Remark column onto extra pages (its column widths are ~15% wider than Excel's even with Carlito installed) | Dropped breaks/fixed scale/item heights; tail in one unsplittable row; kept Fit To |
| 3 | footer render | `Customer Service_x000a_Email…` | openpyxl escapes `\n` in footer text | `_Footer.__str__` unescapes |
| 4 | new tests | Regards line count / seal-vs-margin threshold / "Customer" in END USER | errors in my own new tests (off-by-one lines; LibreOffice sizes its footer to the content, so the body runs past the 0.9in line) | fixed the tests: seal must end above the footer and keep its shape |
| 5 | 12 existing tests | print area / margin / Regards row asserts | they encoded the old row structure | updated to the new layout, same intent (see §4) |

Mutation check: the new LibreOffice sweep run against the **old** exporter fails on the footer at every n. With the footer asserts removed, it fails `58/62/66/70 items: tail on page 1 of 2` (Terms on page 1, seal on page 2), the user's img 4/5 bug.

## 6. Final verification output
```
document-service (doc-test-lo, in-process): 426 passed in 230.86s (0:03:50)
verify.sh (loop-test stack): ======================= 100 passed, 25 skipped in 49.43s =======================
```

## 7. Found but not fixed (out of scope)
- LibreOffice does not grow rich-text Item rows, so multi-line items overlap in the LO render (visible in `iter-35-print-page1.png`). Existing backlog item; Excel is fine. Explicit row heights would fix LO, but they risk clipping text in Excel.
- LibreOffice repeats the header images (banner/seal) on every page because they are anchored in the print-title rows; Excel prints them on page 1 only. Unchanged by this iteration.
- Rows taller than a page (one item with ~80+ lines) are still the known limitation: Excel never splits a row. The same applies to the tail row, which is capped at 409pt (Excel's maximum). Longer Terms put their first lines in normal rows above it.
- Excel vs LibreOffice: Excel's print scale for this sheet is ~68%, LibreOffice's ~59%. No safety margin is needed, because the one-row tail does not depend on estimating where pages end.

## 8. Blockers / decisions needed
- **Deviation from the plan's mechanism (no decision needed, but the planner should know):** the plan asked for a manual row break before Terms, chosen by a pagination estimate, plus a pure-function test. Excel ignores manual breaks while "Fit to 1 page wide" is on. The fixed-scale workaround breaks "one page wide" in LibreOffice. The tail is therefore one unsplittable row, so there is no break function to unit-test. The plan's cases are covered by the LibreOffice sweep instead.
- **Visible change:** "Regards," and the seal are now part of the Terms row (same position as before: right column, under the last Terms line). The seal's vertical spot comes from Excel's line height (12.75pt). In LibreOffice there is a slightly larger gap above the seal. Please check in real Excel that "Regards," sits just under the last Terms line.

## 9. Suggestions for the next iteration
- Open `iter-35` output in real Excel (Print Preview) with the user's CCTV quotation: check the footer on each page, and that the Terms/Regards/seal row moves to the last page whole.
- Excel-side note: the footer uses Excel's standard `&B` and line-break codes, so the user should see three left-aligned lines at the bottom of every page.

App running at http://localhost:8080 (login: admin / CHANGE_ME, local only). Backup taken first: `backup-2026-11-25-1002.tar.gz`; after `up -d --build` + nginx restart: HTTP 200, all 9 services up, 100 products, 2 documents.
