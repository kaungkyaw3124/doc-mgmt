# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **2** |
| Goal | Quotation header (logo + seal → QUOTATION → Date → SUPPLIER / END USER blocks) repeats at the top of **every** PDF page |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## What the user wants
Today the header prints only on page 1. When there are many items and the
table continues onto page 2, 3…, only the table's column titles repeat.
The user wants the **whole header block** — company logo, company seal,
QUOTATION title, Date, SUPPLIER block, END USER block — at the top of
every page, followed by the table column titles (which already repeat).
"Every page" includes a last page that holds only Terms / MD signature.

The footer ("Customer Service") already repeats — leave it alone.

## This reverses an earlier decision — read before coding
`services/document-service/app/templates/quotation.html` lines 6–34 explain
why the header was made non-repeating: it used to be a
`position: running()` element in the `@page` top margin, which needs a
**fixed, guessed** top margin (6.5cm, 10cm, 12.5cm all overflowed when a
long address wrapped). **Do not go back to that approach.**

Suggested approach (verify it works in WeasyPrint 62.3; pick another only
if it fails and say why in the report): wrap the page body in one outer
`<table class="page-layout">` whose `<thead>` holds the header block and
whose `<tbody>` holds the items table, Terms and MD block. WeasyPrint repeats
a table's `<thead>` on every page it spans, and the height comes from the
real content, so there is nothing to guess. Make sure the inner items table's
own `<thead>` (column titles) still repeats under it, and that the existing
break rules (`.terms`, `.md-block`, row `break-inside: avoid`) still work
inside the wrapper.

Remove or rewrite the old comments that say the header does not repeat.

## Excel (XLSX) — small part
In `services/document-service/app/core/export_quotation.py`, set the
worksheet's print titles (`ws.print_title_rows`) to repeat the header rows
through the items table header row when the sheet is printed. Excel cannot
repeat pictures through print titles, so the logo/seal will only print on
page 1 in Excel. That is accepted; state it in the report.

## Tests
These existing tests encode the **old** rule. Invert them (allowed; explain
in the report):
- `tests/test_quotation_pdf_layout.py::test_header_and_parties_are_never_duplicated_onto_a_later_page`
  → becomes: with 2 / 10 / 80 items, **every** page has `QUOTATION`,
  `SUPPLIER: <company>`, `END USER`, the MD name in the Name row, and the
  quotation number.
- `tests/test_quotation_visual_style.py::test_logo_only_on_first_page_and_md_seal_size_unchanged`
  → becomes: logo + seal images appear on **every** page; MD seal still 130px.

Keep the intent of the "MD block is never alone on a page" tests
(`test_no_md_only_page_across_a_range_of_lengths`, `_assert_md_page_not_orphaned`).
The header now adds text to every page, which would make a text-length
orphan check pass falsely. Adjust the helper to ignore header/footer text,
so it still catches a page with only the MD block plus header plus footer.

Add `services/document-service/tests/test_header_repeats.py`:
1. 80 items → ≥ 3 pages; each page's text has the header fields, and
   on pages with item rows the column titles (`No`, `Item`/`Description`,
   `Qty`) come **after** the header text.
2. Long wrapping supplier address (5+ lines) with 80 items → on every page
   the header ends above the first item row (no overlap), using the same
   layout/box inspection style as `test_quotation_visual_style.py`.
3. 1–2 items → still **1 page** (the header must not push a short quote
   onto a second page).
4. No logo/seal uploaded → header repeats without empty gaps or errors.
5. XLSX: `ws.print_title_rows` covers row 1 through the items header row.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | New tests pass | `pytest services/document-service/tests/test_header_repeats.py -v` |
| A2 | No regressions | `pytest services/document-service/tests` — all pass (only the 2 inverted tests + orphan helper changed) |
| A3 | Smoke passes | `./scripts/verify.sh` exits 0 |
| A4 | Visual proof | Render an 80-item quotation with logo, seal and MD. Save pages 1, 2 and the last page as PNG (`pdftoppm -png -r 70`) to `loop/evidence/iter-2-page1.png`, `-page2.png` and `-last.png`. Describe each in the report |
| A5 | Page count sanity | Report the page count for 2, 10, 40 and 80 items, before vs after (the header adds height, so some quotes get longer; just report it) |

## Out of scope
The MinIO image decision from iteration 1 — not yet decided by the user.
