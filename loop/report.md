# report.md — Iteration 23

## 1. Summary
- **Status**: DONE
- **Goal**: Labour cost per line item. When creating/editing a quotation the user can add a **Labour unit price** to each line; the quotation (form, PDF, XLSX) shows **Material** and **Labour** column groups, a **SUB Total** row (material total and labour total) and a **Grand Total** row.
- **Ponytail skill loaded**: yes
- **Branch / commit**: `dev` @ (see the `loop iter 23` commit)
- **One-paragraph summary**: There is a new **"Include labour"** checkbox on the document form. It is off by default. When it is on, each line gets a **Labour unit price** input and a live "Material / Labour total". Under the table the form shows a live SUB Total (material), Labour and Grand Total. The server stores `documents.include_labour` and `line_items.labour_unit_price` (migration `0011_labour`, the single head) and recalculates every total itself. `total` = materials + tax + labour, and labour counts only when the box is on. **Tax rule (confirmed by the user): tax is charged on materials only, never on labour.** The PDF and the XLSX get a two-row header (Material and Labour each span Unit Price / Total Price), plus bordered, bold SUB Total → "Tax on materials (x%)" (only when there is tax) → Grand Total rows. A line with no labour shows an empty labour price and "-" as its labour total. The XLSX uses live formulas: labour total = `=C*G`, SUB = sums, Grand = sum cells. The print area is `A1:I{last}`, fit to 1 A4 page wide, and the seal moves to H. With labour off, the PDF markup is identical to before (only new CSS selectors were added), and the XLSX matches the old generator cell for cell on all 5 iteration-22 fixtures.

## 2. Baseline (before any change)
The suites were run on unchanged HEAD `26ef6e9`, from a separate git worktree, while my edits were in progress:
```
document-service (doc-test-lo image, in-process SQLite): 349 passed, 59340 warnings in 229.14s
```
The verify.sh baseline is from iteration 22's final run on the same commit: `87 passed, 17 skipped`, PASSED. I did not re-run it before starting. I also loaded the skill and moved the old report to history **after** I had begun the edits. This is a small departure from LOOP §1 step 4.

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | Totals math (material, labour, SUB, Grand, tax) | PASS | `tests/test_labour.py`, API on SQLite: a 29-line fixture built to give the screenshot's totals (CCTV 27×220,000 / 20,000, 9U rack 951,000 / 60,000, GI pipe 150,000 / 50,000, 5 lines with no labour) → subtotal **31,756,300**, labour **3,813,000**, total **35,569,300**. Totals sent by the client are ignored (POST and PATCH). Tax: 1,000,000 at 5 % + labour 200,000 → tax **50,000**, total **1,250,000**. Decimal qty/prices and 99,999,999.99 work. Negative labour → **422**. Labour stored while `include_labour=false` → stored, not counted in the total, not shown in the PDF/XLSX. PATCH without `include_labour` keeps the flag. |
| A2 | `include_labour=false` outputs unchanged | PASS | Every existing test is untouched and passes. The one exception is `test_single_alembic_head`, whose expected head name moved from `0010_concurrency` to `0011_labour`. One-off checks (not committed): the old and new template give identical HTML markup (the only difference is the added CSS rules, which only match `.labour` and `.labour-total`). The old and new `generate_quotation_xlsx` give identical cells, formats, fonts, borders, merges, print area, print titles, image anchors and column widths on all 5 iteration-22 print fixtures (`IDENTICAL` ×5). |
| A3 | PDF layout (headers, widths, totals in the tail block, multi-page) | PASS | WeasyPrint box tree: "Material (MMK)" spans exactly its Unit Price and Total Price cells (same for Labour). Remark spans both header rows. Every table cell on every page is inside 12.7 mm → 197.3 mm. The template's margins are 12.7 mm, not the plan's 11 mm; the 12.7 mm margins were left as they are. With 3 and 45 lines, the last item, SUB Total, Grand Total, Terms and Regards are all on the last page, the header repeats on every page, and Grand Total appears on no earlier page. `loop/evidence/iter-23-labour-pdf-p1.png`, `-p2.png` (29 lines + seal + MD seal: 28 lines on p1, then the last line + totals + terms + signature on p2) |
| A4 | XLSX (merges, live formulas, totals, print fits) | PASS | Unit tests check the merges `E:F`, `G:H`, `A..D,I` over the 2 header rows, `print_title_rows` covering both header rows, `=C*G` per line, the SUB / Tax / Grand formulas, thin borders on all 9 cells of each totals row, bold text, the print area up to `$I$`, and the seal anchored at H. LibreOffice recalculation (xlsx → PDF, words read with PyMuPDF): the screenshot case shows 31,756,300.00 / 3,813,000.00 / 35,569,300.00 / "-"; the tax case shows 50,000.00 / 1,250,000.00; 45 lines show 45,000.00 / 4,500.00 / 49,500.00. Every page has all column titles, and Remark is inside the right margin. `loop/evidence/iter-23-labour-xlsx.png` |
| A5 | Form flow (checkbox, live totals, edit/restore) | PASS | `tests/test_ui_labour.py` (Playwright, loop-test stack): off by default and labour inputs hidden → tick → 3 lines → per-line 5940000.00 / 540000.00 / "-", SUB 7041000.00, Labour 600000.00, Grand 7641000.00 → save → the PDF has SUB Total / 7,041,000.00 / 600,000.00 / 7,641,000.00, and the XLSX has 9 columns with SUB/Grand Total → Edit: checkbox ticked and 60000 restored → untick → saved total 7,041,000, the PDF has no "Labour" or "SUB Total", and the XLSX is back to 7 columns with "Total:". `loop/evidence/iter-23-labour-form.png` |
| A6 | Migration single head; no regressions; data safety | PASS | `alembic heads` → `0011_labour (head)`. document-service: **365 passed** (349 old + 16 new). verify.sh on loop-test: **87 passed, 18 skipped**, PASSED. UI suite: **33 passed, 10 skipped**. |

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `services/document-service/alembic/versions/0011_labour.py` | added | `documents.include_labour` (bool, not null, default false), `line_items.labour_unit_price` (Numeric(12,2), nullable). Guarded and idempotent like 0008 and 0010. |
| `app/models.py`, `app/schemas.py` | modified | new columns. `labour_unit_price` validated `ge=0` (→ 422). `include_labour` on Create (default false), Update (omit = unchanged) and Out. |
| `app/routers/documents.py` | modified | `_process_items` also returns `labour_total`. On create/update, `total = subtotal + tax + labour` only when include_labour is on. Tax still comes from the material `line_total` only. |
| `app/core/export_pdf.py`, `app/templates/quotation.html` | modified | labour columns, a fixed-layout colgroup (Item takes what is left, 8 pt text), a two-row header, and SUB / Tax on materials / Grand Total rows in the same `tail` tbody. |
| `app/core/export_quotation.py` | modified | 9-column layout when labour is on, merged two-row header, `=C*G` labour formulas, `"-"` number format for zero, `_labour_totals()` for the totals block. The last column, seal column, terms merge, Regards, MD seal and print area now follow the column count. |
| `web/index.html`, `web/js/app.js` | modified | the checkbox, a labour input per line, live per-line and overall totals, the payload, restore on edit, and a "Labour (not taxed)" line in the document detail view. |
| `services/document-service/tests/test_labour.py` | added | 16 tests (API, PDF box tree, XLSX, LibreOffice). |
| `services/document-service/tests/test_concurrency.py` | modified | expected Alembic head is now `0011_labour`. |
| `tests/test_ui_labour.py` | added | Playwright form flow. |
| `loop/evidence/iter-23-*.png` | added | evidence |

Files inspected (Task 1): the form is `web/index.html` (`#line-items`, `#doc-totals-summary`) and `web/js/app.js` (`addLineItem`, `updateDocumentTotals`, the doc-form submit handler, `startEditDocument`, `renderDocumentDetailContent`). Schemas are `DocumentItemIn/Out`, `DocumentCreate/Update/Out`. Totals are computed in `routers/documents.py::_process_items` and in create/update. The PDF is `core/export_pdf.py` + `templates/quotation.html`, and the XLSX is `core/export_quotation.py`.

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | `test_single_alembic_head` | `['0011_labour'] != ['0010_concurrency']` | the test pins the head name | expected value updated to the new head |
| 1 | `test_pdf_zero_labour_shows_dash_and_empty_price` | `assert 1 == 9` cells | the test picked the outer page-layout row, not the item row | test picks the smallest matching row |
| 1 | `test_xlsx_headers_formulas_and_totals_rows` | merged-over cells had no border or font | openpyxl resets the style of merged-over cells when `merge_cells` runs | merge first, then style (`_labour_totals.line`) |
| 2 | same | `MergedCell.font.b` is False after reload | merged-over cells hold no text, and openpyxl keeps only their borders | bold is asserted on visible cells only; borders are still asserted on all 9 cells |
| 1 | UI screenshot | the form defaulted to Invoice | the test didn't choose a type | test selects "quotation" |

## 6. Final verification output
```
document-service (doc-test-lo image): alembic heads -> 0011_labour (head)
                                      365 passed in 218.23s
loop-test: COMPOSE_PROJECT_NAME=loop-test COMPOSE_OVERRIDE=infra/docker-compose.loop-test.yml GATEWAY=http://localhost:18080 ALLOW_DESTRUCTIVE_TESTS=1 ./scripts/verify.sh --keep
======================= 87 passed, 18 skipped in 48.78s ========================
verify.sh: PASSED (stack left running: --keep)
tests/test_ui_*.py (playwright v1.65.1, --network host, loop-test): 33 passed, 10 skipped in 71.63s
```
The 18th skip in verify.sh is `test_ui_labour.py`: the gateway venv has no Playwright, so the test skips there. It ran and passed in the Playwright container.

## 7. Found but not fixed (out of scope)
- **Number format**: the plan says `#,##0`, "as the existing money formatting", but the existing PDF/XLSX use 2 decimals (`#,##0.00`). I kept 2 decimals so labour and no-labour quotations look the same. If the user wants whole numbers (like their screenshot), that is a one-line change per format.
- **Product default labour price** (idea): a `labour_unit_price` on catalogue products that is pre-filled into the line.
- Per-column tax, labour on sub-items, Remark column redesign: not done (out of scope).
- LibreOffice still overlaps rich-text Item cells in rows that have no set height (known since iteration 22; visible in `iter-23-labour-xlsx.png`). Excel sizes those rows itself.
- The UI saves labour prices only while the box is ticked. If it is unticked at save time, the typed labour prices are dropped (the API itself would store them).

## 8. Blockers / decisions needed
None.

## 9. Suggestions for the next iteration
- Decide on whole-number vs 2-decimal money format (see §7).
- Commit a test image (Dockerfile.test with LibreOffice + PyMuPDF + Playwright), so the LibreOffice and UI tests run the same way every time.

Data safety: unit/e2e tests ran in-process (SQLite TestClient) in the `doc-test-lo` container. verify.sh and the Playwright tests ran on the throwaway `loop-test` stack (ports 18080/18443), which was then removed with `down -v`. No tests ran against the user's stack.

**App running at http://localhost:8080 (login: admin / CHANGE_ME, local only).** Backup taken before the rebuild: `backups/backup-2026-11-18-1428.tar.gz`. After `up -d --build` + `restart nginx`: HTTP 200, all 9 services up. On the user's stack, docmgmt is at `0011_labour`. Products: 100. Documents: 0, the same as before this iteration (iterations 20–21 also recorded 0), so no data was lost. Every existing document will have `include_labour = false`.
