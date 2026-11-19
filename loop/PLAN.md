# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **24** |
| Goal | **Select all on every list.** Products, Documents, Customers, Projects each get a checkbox column for every user and a "Select all" header checkbox that selects exactly the rows currently shown (after search, category, date and group filters), plus a selection bar with the count |
| Attempt limit | 5 per failing check |
| Status | READY FOR CLAUDE CODE |

## What the user wants
Screenshot of the Products list: a checkbox column exists (only for superusers, inside the Group cell) and "Move selected to group…" works. The user wants a **Select all**: if there are 100 products, a date filter leaves 10, and they click Select all, **exactly those 10** are selected. Same on Documents, Customers, Projects. Iteration 25 builds "Add selected to cart" on top of this.

## Decisions already made
- **Own checkbox column** (first column, narrow) on all four lists, visible to **every** user (not only superusers). Today's checkbox lives inside `groupCell()` (`.move-select`, superuser only) — move it into the new column and keep one shared selection mechanism; the superuser's "Move selected to group…" keeps working with it. The Group column keeps its tag and "Move…" link for superusers.
- **Header checkbox** in the new column:
  - click → selects every row currently rendered (the filtered list, **all pages** if the list is paginated; if the list is not paginated that is simply every visible row);
  - click again → clears them;
  - **indeterminate** state when only some visible rows are ticked; checked when all visible rows are ticked.
- **Selection is by id, held in a JS `Set` per list** (not by reading the DOM), so it survives re-rendering and pagination.
- **Changing any filter, search text, sort-reset or the Reset button clears the selection** (safety: never act on hidden rows). Sorting alone keeps it. Reloading a list after a save/move/delete clears it.
- **Selection bar** above each table, shown only when ≥ 1 selected: `N selected` + **Clear** + the bulk buttons that exist for that list (Move selected to group… for superusers; iteration 25 adds Add to cart on Products). Sticky below the filter row if the page scrolls. Count must always equal the Set size.
- **Group isolation** untouched: the lists already contain only rows the user may see; select all can only select those. The Move endpoint still validates server-side.
- **No bulk delete** from selection. Out of scope.
- Keyboard/a11y: header checkbox has `aria-label="Select all shown"`, row checkboxes `aria-label="Select row"`; Space toggles; selection bar `aria-live="polite"`.
- Performance: one delegated change handler per table; no per-row listeners; 500 rows must toggle in well under 100 ms.

## Tasks
1. Inspect the four list renderers (`loadProducts`, `loadDocuments`, `loadCustomers`, `loadProjects`, `applyFilterAndSort`, `tableConfig`, `groupCell`, the move code ~line 3720 of `web/js/app.js`, and `web/index.html` table headers); list findings in the report.
2. One shared selection helper (per-table `Set`, header checkbox sync, selection bar, clear-on-filter-change) used by all four lists; update `index.html` headers/colspans (empty-row `colspan`s must match the new column count).
3. Keep "Move selected to group…" working through the shared selection.
4. Tests + evidence.

## Tests
- Playwright `tests/test_ui_select_all.py` on the throwaway `loop-test` stack ONLY: create 100 products via API in different dates (set `created_at` through the test DB/API helper or create 10 with a distinct date) → date filter shows 10 → Select all → bar says "10 selected" → exactly those 10 ids selected → change filter → selection cleared and bar hidden → select all with no filter → "100 selected" → click header again → 0. Same smoke on Documents, Customers, Projects (a few rows each).
- Indeterminate: tick one row → header indeterminate; tick all → header checked.
- Non-superuser (group user) sees the checkbox column and Select all; superuser still sees Move selected and it moves exactly the selected ids; a group user never sees rows of other groups.
- Selection survives sorting; cleared after reload/save/move.
- Empty list: Select all disabled, no errors in the console.
- All existing tests (including `test_ui_group_move.py`) pass; `./scripts/verify.sh` passes.
- Evidence: `loop/evidence/iter-24-select-all-products.png` (10 of 100 selected), `iter-24-select-all-indeterminate.png`.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Select all selects exactly the filtered rows (10 of 100) on Products | Playwright + screenshot |
| A2 | Same on Documents, Customers, Projects | Playwright |
| A3 | Filter/search/reset change clears selection; sort keeps it | Playwright |
| A4 | Header checkbox states (unchecked / indeterminate / checked); a11y labels | Playwright |
| A5 | Every user sees the column; superuser Move selected still works; group isolation intact | Playwright + existing suites |
| A6 | No regressions; data safety (tests only on `loop-test` stack) | `./scripts/verify.sh`, report states which stack |

## Out of scope — record under "Found but not fixed"
Bulk delete, select across different filters, saved selections, keyboard shift-click range select (note as an idea).
