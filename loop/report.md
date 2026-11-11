# report.md — Iteration 14

## 1. Summary
- **Status**: DONE
- **Goal**: Quotation PDF/XLSX: (1) remove the "Items" group row at the top of the items table; (2) the last item, the Subtotal/Tax/Total rows, the Terms and the MD signature always end up together on the LAST page, never stranded apart
- **Ponytail skill loaded**: yes (loaded with the Skill tool at the start)
- **Branch / commit**: `dev` @ (this commit, see `git log`)
- **One-paragraph summary**: The group row ("Items", or the single category name) is gone from both the PDF template and the XLSX exporter. The XLSX rows were already computed dynamically, so item 1 now sits right under the column titles and every formula, merge and print-title row follows on its own. For the tail, approach A (row-level `break-after/-before: avoid`) was tried first and only partly worked: WeasyPrint ignored it when the Total row was the row that overflowed (e.g. 29 items). The fix that works is half-way between A and B: the last item and its sub-items plus Subtotal/Tax/Total go in a **second `<tbody class="tail">` of the same table** with `break-inside: avoid`, and `table.items` gets `break-after: avoid`, which chains it to the existing `.terms` → `.md-block` pairing. Because it is the same table, the columns line up by construction (no `<col>` duplication needed). A tail taller than a page splits normally. No existing `.terms`/`.md-block` rule was touched, and all existing tests pass unchanged.

## 2. Baseline (before any change)
Run on a clean worktree of HEAD `6d754c8` (an earlier baseline run was stopped and restarted there, because its Docker build could have picked up my first edit).
```
$ ./scripts/verify.sh
======================== 24 passed, 8 skipped in 5.16s =========================
verify.sh: PASSED

$ pytest services/document-service/tests      (inside the document-service image + requirements-dev + poppler-utils)
212 passed, 16073 warnings in 69.35s (0:01:09)
```

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | New tests pass | PASS | `pytest tests/test_quotation_tail_layout.py` → `9 passed in 81.61s`. On the code with only the group row removed (no tail fix): `4 failed` sweep cases (`plain, N=17: 'LASTROW 017' not on the final page 2`, `tax, N=15`, `seal, N=13`, `seal_tax_subs, N=8`). With approach A only: still `4 failed` (`plain, N=29`, `tax, N=27`, `seal, N=29`, `seal_tax_subs, N=24`) |
| A2 | No regressions | PASS | `pytest services/document-service/tests` → `221 passed in 144.10s` (212 + 9 new, no existing test changed). `./scripts/verify.sh` → see §6 |
| A3 | Proof: before | PASS | `loop/evidence/iter-14-before-p1.png` (14 items + "Items" row + Total on page 1), `iter-14-before-p2.png` (only Terms + Regards + seal). Also `iter-14-before-subs-p1/p2.png` (8 items, last one with 3 sub-items, tax) |
| A4 | Proof: after | PASS | `loop/evidence/iter-14-after-p1.png` (items 1–13, no "Items" row), `iter-14-after-p2.png` (item 14 → Total → Terms → Regards + seal). Sub-items pair: `iter-14-after-subs-p1/p2.png` (item 8 + sub-items 0.1–0.3 → Subtotal/Tax/Total → Terms → seal on page 2). Director has a 300x300 PNG seal in all of them |
| A5 | XLSX | PASS | dump below: header on row 11, item 1 on row 12, no "Items" row, Subtotal = sum of main item rows only, Tax/Total formulas correct |

A5 output (5 items with tax 10%, last item has 3 sub-items; rows 1–25):
```
 1 | QUOTATION |  |  |  |  |  |
 3 | Date: | 2026-01-01 |  |  |  |  |
 5 | SUPPLIER: Acme Supplier Co., |  |  |  | END USER |  |
 6 | Name | Zaw Zaw |  |  | Customer Name | Beta Customer Co., Ltd. |
 7 | Position | Managing Director |  |  | Quotation ID | QT-0001 |
 8 | Address | N/A |  |  | Organization | Beta Customer Co., Ltd. |
 9 | Contact No | N/A |  |  | Address | No. 99, Customer Street, Man |
11 | No | Item | Qty | Unit | Price (USD) | Amount (USD) | Remark
12 | 1 | Widget 001\nWidget 001 | 1 | Nos | 100 | 100 |
13 | 2 | Widget 002\nWidget 002 | 1 | Nos | 100 | 100 |
14 | 3 | Widget 003\nWidget 003 | 1 | Nos | 100 | 100 |
15 | 4 | Widget 004\nWidget 004 | 1 | Nos | 100 | 100 |
16 | 5 | LASTROW 005\nLASTROW 005 | 1 | Nos | 100 | 100 |
17 | 0.1 | SKU-1 — Sub part 1 | 1 | Nos | 5 | 5 |
18 | 0.2 | SKU-2 — Sub part 2 | 1 | Nos | 5 | 5 |
19 | 0.3 | SKU-3 — Sub part 3 | 1 | Nos | 5 | 5 |
20 | Subtotal: |  |  |  |  | =F12+F13+F14+F15+F16 |
21 | Tax (10%): |  |  |  |  | =F20*10.0/100 |
22 | Total: |  |  |  |  | =F20+F21 |
24 | Terms and Conditions |  |  |  |  |  |
25 | Price Validity: 30 Days |  |  |  |  |  |
```
(Empty rows 2, 4, 10, 23 left out. "0.x" sub-labels are because the test items have no `product_id`. The doubled name is a separate XLSX quirk, see §7.)

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `services/document-service/app/templates/quotation.html` | modified | removed the `group-header` row and its CSS; the last item group + totals now go in `<tbody class="tail">`; added `tbody.tail { break-inside: avoid }` and `table.items { break-after: avoid }` with a comment explaining why row-level rules were not enough |
| `services/document-service/app/core/export_pdf.py` | modified | dropped the `group_label` calculation and template variable |
| `services/document-service/app/core/export_quotation.py` | modified | dropped the merged A:G group row; item rows now start right under the header |
| `services/document-service/tests/test_quotation_tail_layout.py` | added | sweep N=1..45 × {plain, tax, seal, seal+tax+3 sub-items}; no group row in PDF/XLSX (no category and one category); a tall tail with 25 sub-items |
| `loop/evidence/iter-14-*.png` | added | A3/A4 |
| `loop/history/report-iter-13.md` | moved | previous report (rename staged by the user) |

`tests/test_quotation_xlsx_tax.py` needed no change: it finds rows by label, not by fixed row numbers.

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | A1 `test_no_items_group_row_in_pdf` | `got '1Widget 001 1.0 ...'` | my test: pypdf joins the "No" cell to the item name | match `1\s*Widget 001` with a regex |
| 1 | A1 sweep (all 4 variants) | `plain, N=17: 'LASTROW 017' not on the final page 2` | no tail rule yet (expected) | approach A: `break-after: avoid` on the last item/sub-item/subtotal rows, `break-before: avoid` on Total, `break-after: avoid` on the table |
| 2 | A1 sweep (all 4 variants) | `plain, N=29: 'LASTROW 029' not on the final page 2` | WeasyPrint ignores row-to-row avoid when the overflowing row is the Total itself: item 29 stayed at the bottom of page 1 and Total went to page 2 | replaced the row rules with a separate `<tbody class="tail">` + `break-inside: avoid` → all 9 pass |

## 6. Final verification output
```
$ ./scripts/verify.sh
==> [verify] wait for health (timeout 180s)
all services healthy
...
======================== 24 passed, 8 skipped in 4.85s =========================
==> [verify] docker compose down
verify.sh: PASSED
```
Unit tests: `pytest services/document-service/tests` → `221 passed in 144.10s`.

## 7. Found but not fixed (out of scope)
- XLSX prints a manual item's name twice ("Widget 001\nWidget 001") when the product has no description: `export_quotation.py` falls back to `item.description` without the "only if it differs from the name" check that `export_pdf.py` got (see the comment around `item_description_override` in `export_pdf.py`). Found in the A5 dump. Fix: use the same check in the XLSX.
- The Excel export has no page breaks of its own (plan: out of scope).
- The document-service unit tests aren't part of `verify.sh` (it only runs the root `tests/` smoke suite). The host has Python 3.14 without WeasyPrint, so I ran them inside the document-service image (+ `requirements-dev.txt` + `poppler-utils`).

## 8. Blockers / decisions needed
None.

## 9. Suggestions for the next iteration
- Check a real quotation with long item descriptions / product images: the sweep uses one-line items.
- Fix the duplicated XLSX name from §7 (a few lines, reuse the PDF's check).
- Consider adding the document-service unit tests to `verify.sh`, or a documented `docker run` target, so they can't be skipped by accident.
