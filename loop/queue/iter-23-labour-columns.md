# PLAN.md — Iteration 23 (QUEUED — promote after iteration 22, or earlier if the user says so)

| Field | Value |
|---|---|
| Iteration | **23** |
| Goal | **Labour cost per line item.** When creating/editing a quotation the user can add a **Labour unit price** to each line; the quotation (form, PDF, XLSX) shows **Material** and **Labour** column groups, a **SUB Total** row (material total and labour total) and a **Grand Total** row |
| Attempt limit | 5 per failing check |
| Status | QUEUED |

## What the user wants (two screenshots)
1. A quotation table with grouped headers: `No | ITEM | Qty | Unit | Material: Unit Price, Total Price | Labour: Unit Price, Total Price`.
   Material Total = Qty × material unit price. Labour Total = Qty × labour unit price. A line with no labour shows an **empty** labour unit price and a **"-"** as labour total.
   Example: CCTV, qty 27, material 220,000 → 5,940,000; labour 20,000 → 540,000. "9U rack": qty 1, labour 60,000 → 60,000.
2. Below the items: a bordered, bold **SUB Total** row (centered label; material total and labour total in their columns) and a bordered **Grand Total** row (centered label; one amount = material + labour).
   The user wants these SUB Total / Grand Total rows in the **printed Excel and the PDF**.

## Decisions already made
- **Optional per document**: a checkbox on the quotation form **"Include labour"** (default **off**, so every existing quotation, PDF and XLSX stays exactly as it is). When on, each line row shows a **Labour unit price** input and a computed labour total; the Material columns are today's Unit Price / Amount.
- **Data**: `line_items.labour_unit_price` (Numeric(12,2), nullable) and `documents.include_labour` (Boolean, default false, not null; existing rows false). Alembic migration, single head, SQLite-compatible for tests. Labour total is computed (`quantity × labour_unit_price`), not stored separately unless the existing code stores `line_total` the same way — follow the existing pattern for `line_total`.
- **Sub-items** (rows under a product) stay informational: no labour, not in any total (as today).
- **Totals when labour is on**: `SUB Total` row = (material total, labour total); `Grand Total` row = material + labour (+ tax, see below). Zero labour totals display as "-". Number format `#,##0` (as the existing money formatting; keep the document currency label in the header).
- **Tax is for products only, NOT for labour** (confirmed by the user: "tax is only for products, not for labour"). The line's existing `tax_rate` applies to the **material** amount of that line only; labour is never taxed. When any tax exists the totals block is: SUB Total (material | labour) → Tax (one amount, on material only) → Grand Total (material + labour + tax). With no tax: SUB Total → Grand Total (material + labour). Label the tax row so it is clear it is on materials (e.g. "Tax (on materials)"), and put one sentence in the report confirming the rule.
- **Labour is optional when creating a document** (confirmed): the "Include labour" checkbox is off by default; a document without labour is unchanged everywhere.
- **Layout**:
  - Header is two-level: "Material" spans its two columns, "Labour" spans its two columns; No, Item, Qty, Unit (and Remark if the document has remarks today) span both header rows. The Remark column stays where it is today (after the amounts).
  - **PDF**: adjust column widths so all columns fit the A4 content width (11 mm side margins); the item text column shrinks, not the numbers; the tail-layout rule from iteration 14 must keep working (last item + totals + terms + MD together; SUB Total and Grand Total rows go in the same `tail` tbody).
  - **XLSX**: add the labour columns to the right of Amount (before Remark), merged group headers, SUB Total / Grand Total rows with borders and bold, **live formulas** (SUM of the item rows; labour total per row = Qty × labour price; grand total = sum cells) like the current Subtotal/Total formulas. Update the logo/banner/seal anchors if the sheet gets wider (seal stays at the top-right of the table width; banner box stays left of it).
  - **Print setup** (from iteration 22 if it is already merged, otherwise add it now): fit to one page wide, A4 portrait, print area `A1:{last column}{last row}` computed from the real last column, so the wider sheet still prints all columns on every page.
- **Document form** (`web/index.html` / `web/js/app.js`): the checkbox; labour unit price input per line (numeric, ≥ 0, optional); live Material total / Labour total per line and live SUB Total / Grand Total under the table; works for add/remove/reorder rows, for manual (non-catalogue) items, and when editing an existing quotation (checkbox state restored). Toggling the checkbox off hides the labour inputs but keeps the typed values until save; saving with it off ignores labour in all outputs.
- **Documents list/detail views** that show a total use the grand total (material + labour [+ tax]) so the number matches the PDF.
- **Group isolation (iteration 19), version checks (iteration 21)**: keep working; no new endpoints needed beyond the existing create/update payloads.
- Out of scope: a default labour price on products (record as an idea), different tax per column, labour-only quotations.

## Tasks
1. Inspect the document form, schemas, create/update routers, totals calculation (where `line_total`/`tax_total` are computed), PDF template, XLSX exporter; list them in the report.
2. Migration + model + schema changes (`include_labour`, `labour_unit_price`); server-side recalculation of totals (never trust client totals).
3. Form UI (checkbox, labour inputs, live totals, edit/restore).
4. PDF template + export, XLSX exporter (columns, merged headers, formulas, borders, SUB Total / Grand Total rows).
5. Tests + evidence (below).

## Tests
- Calculation unit tests: the user's screenshot as fixture — 29 lines (use a trimmed set that includes: CCTV qty 27 / 220,000 / labour 20,000; "9U rack" qty 1 / 951,000 / labour 60,000; "GI pipe" qty 1 / 150,000 / labour 50,000; lines with no labour) → SUB Total material 31,756,300 and labour 3,813,000 for the full 29-line data **(use the exact numbers from the screenshot for a full 29-line fixture if you can reconstruct it; otherwise a smaller fixture with hand-computed totals)**, Grand Total 35,569,300; zero-labour lines show "-"; decimals, large numbers, qty with decimals; tax cases (tax only on material, labour untaxed: e.g. material 1,000,000 at 5% + labour 200,000 → tax 50,000, Grand Total 1,250,000); `include_labour=false` → output byte-for-byte unchanged versus before this iteration for the existing fixtures (layout tests/XLSX tests unchanged).
- API: create/update with labour, server recalculates and ignores client totals; negative labour price → 422; labour price given while `include_labour=false` is stored but not used in outputs.
- PDF (WeasyPrint box-tree like the existing layout tests): two-level header spans correct columns; all columns inside the page content width; SUB Total / Grand Total rows present and inside the tail block with the last item, terms and MD signature; multi-page (45 lines) keeps the header repeating and the totals on the last page.
- XLSX: header merges (`Material` over its 2 columns, `Labour` over its 2), formulas compute correctly when recalculated by LibreOffice (convert to PDF/xlsx and read values), SUB Total / Grand Total borders/bold, anchors still correct, and the LibreOffice print check from iteration 22 (all columns on every page) passes with labour columns.
- Playwright `tests/test_ui_labour.py` (throwaway `loop-test` stack ONLY): create a quotation, tick "Include labour", enter materials + labour for 3 lines → live totals match → save → download PDF and XLSX → totals match; edit it again → checkbox and values restored; untick → PDF/XLSX identical to a no-labour quotation.
- Generate the screenshot-like data from code; use synthetic company/customer names. All existing tests pass; `./scripts/verify.sh` passes.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Totals math (material, labour, SUB, Grand, tax) | unit/API tests incl. the screenshot numbers |
| A2 | `include_labour=false` outputs unchanged | existing layout/XLSX tests pass untouched |
| A3 | PDF layout (headers, widths, totals in the tail block, multi-page) | PDF tests + `loop/evidence/iter-23-labour-pdf-p1.png`, `-p2.png` |
| A4 | XLSX (merges, live formulas, totals, print fits) | tests + `iter-23-labour-xlsx.png` (LibreOffice render) |
| A5 | Form flow (checkbox, live totals, edit/restore) | Playwright + `iter-23-labour-form.png` |
| A6 | Migration single head; no regressions; data safety | `alembic heads`, `./scripts/verify.sh`, suites; report states which stack tests ran on |

## Out of scope — record under "Found but not fixed"
- Product default labour price; per-column tax; labour on sub-items; Remark column redesign.
