# report.md — Iteration 2

## 1. Summary
- **Status**: DONE
- **Goal**: Quotation header (logo + seal → QUOTATION → Date → SUPPLIER / END USER blocks) repeats at the top of **every** PDF page
- **Branch / commit**: `loop/iter-2-header-repeats` @ the commit that adds this report (`loop iter 2: quotation header repeats on every PDF page [DONE]`), merged into `dev`
- **One-paragraph summary**: The PDF header now repeats on every page, including a last page that holds only Terms and the MD signature. I used the suggested approach, and it works in WeasyPrint 62.3. The body is one invisible outer `<table class="page-layout">`. Its `<thead>` holds `.pdf-header`, and its single `<tbody>` cell holds the items table, Terms and the MD block. The header's height comes from its real content, so nothing is guessed, and a long wrapped address just makes the header taller on every page. The items table's own column titles still repeat under the header. The `.terms` / `.md-block` / row break rules work unchanged inside the wrapper. The Excel export now sets `ws.print_title_rows` to row 1 through the column-titles row. **Excel cannot repeat pictures through print titles, so in a printed XLSX the logo and seal appear on page 1 only. This is the accepted limitation.** All acceptance checks pass: unit tests 152 → 159 passed, smoke 14 passed.

## 2. Baseline (before any change)
Docker daemon restarted. The session-only CA shims from iteration 1 (local `python:3.12-slim-bookworm` / `nginx:alpine` tags) were still present.
```
$ ./scripts/verify.sh
============================== 14 passed in 3.14s ==============================
verify.sh: PASSED                                   (exit 0)

$ pytest services/document-service/tests            (Python 3.12 venv)
152 passed in 90.44s (0:01:30)
```

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | New tests pass | PASS | `pytest services/document-service/tests/test_header_repeats.py -v` → `6 passed in 8.41s`. Against the old app code, 4 of the 6 fail. The two "short quote = 1 page" cases pass on both, as they should |
| A2 | No regressions | PASS | `pytest services/document-service/tests` → `159 passed in 63.68s` (152 baseline + 6 new + 1 helper self-check). Changed tests are listed in §4 |
| A3 | Smoke passes | PASS | `./scripts/verify.sh` → `14 passed in 2.48s` … `verify.sh: PASSED`, exit 0 |
| A4 | Visual proof | PASS | `loop/evidence/iter-2-page1.png`, `iter-2-page2.png`, `iter-2-last.png` (80 items, logo, seal, MD with seal, `pdftoppm -png -r 70`); described below |
| A5 | Page count sanity | PASS (reported) | table below |

**A4: what each PNG shows.**
- **Page 1** (`iter-2-page1.png`): logo at top left and company seal at top right (120px each). Below them are QUOTATION (red), the Date, then `SUPPLIER: Swift Solutions Co., Ltd.` with Name `U Wathone Htoo`, Position, a two-line Address and Contact No, beside the END USER block. Next come the items table column titles, the "Items" group row and items 1–21. The grey Customer Service footer is at the bottom. This is identical to the old page 1.
- **Page 2** (`iter-2-page2.png`): the same full header (logo, seal, QUOTATION, Date, both party blocks), then the repeated column titles, then items 22–43, then the footer. Nothing overlaps.
- **Last page, page 5** (`iter-2-last.png`): the same full header, then *Terms and Conditions* and, at the right, the MD's 130px signature seal. There's no items table on this page, so no column titles either. The footer is at the bottom. This is the "page holding only Terms / MD signature" case. Page 4 (not saved) holds items 66–80 and the Total row under the repeated header and column titles. Terms + MD didn't fit in the space left there, so they moved together to page 5 (`.terms` break-inside: avoid, paired with `.md-block` break-before: avoid, as before).

**A5: page counts** (same inputs as A4: logo, seal, MD with seal, default terms, item names like "Item N with a reasonably long name for the row"):
| Items | Before | After |
|---|---|---|
| 2 | 1 | 1 |
| 10 | 2 | 2 |
| 40 | 3 | 3 |
| 80 | 4 | **5** |

With logo and seal, the repeated header is 300px tall (measured `.pdf-header` margin height, CSS px). After the change, the 80-item PDF has 21 / 22 / 22 / 15 item rows on pages 1–4, with Terms + MD on page 5. Page 1 holds 21 rows as before; later pages lose the header's height. With 80 items, Terms + MD no longer fit after the Total row and need a fifth page.

**XLSX (Excel side)**: an 80-item export reads back `ws.print_title_rows == '$1:$11'`. Row 11 is the column-titles row (`No` / `Item`), and rows 1–10 hold QUOTATION, Date and the SUPPLIER / END USER rows.

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `services/document-service/app/templates/quotation.html` | modified | The body is wrapped in `table.page-layout`: `<thead>` = `.pdf-header`, `<tbody>` = items table + Terms + MD block. That table has zero padding, no border and no spacing, so it is invisible. The `.pdf-footer` running element moved to the start of `<body>`, outside the wrapper. Its content and styling are unchanged. The old comments saying the header doesn't repeat (the `@page` comment and the `.pdf-header` comment) were rewritten. The `@page` comment still says not to go back to the `running()` + fixed-top-margin approach |
| `services/document-service/app/core/export_quotation.py` | modified | `ws.print_title_rows = f"1:{row}"` right after the column-titles row is written, with a comment noting that images don't repeat |
| `services/document-service/tests/test_header_repeats.py` | added | The 5 plan cases (case 3 runs for both 1 and 2 items). It imports the `layout` fixture and helpers from `test_quotation_visual_style.py` so the box inspection matches. The overlap check measures the real `.pdf-header` box bottom on every page against the column titles and first item row. It also checks that the director's address really wraps to 5+ lines |
| `services/document-service/tests/test_quotation_pdf_layout.py` | modified | **Inverted** `test_header_and_parties_are_never_duplicated_onto_a_later_page` → `test_header_and_parties_repeat_on_every_page`. With 2 / 10 / 80 items, every page has QUOTATION (exactly once), `SUPPLIER: <company>`, END USER, `Name <MD>` and QT-0001. **Rewrote the orphan helper**: `_assert_md_page_not_orphaned` now measures `_page_body()`, which is the page text minus the header (up to the END USER address), the column titles and the footer, with a threshold of more than 60 chars. Its intent is unchanged. **Added** `test_orphan_check_still_catches_an_md_only_page_under_the_repeated_header`, which proves the helper still rejects header + MD + footer, and header + column titles + lone Total row + MD + footer. The old 160-char threshold would have passed both falsely. **Also changed** one assertion in `test_normal_quotation_keeps_md_correctly_positioned`: `header_pages == [0]` → the header on every page. It encoded the same old rule and passed only because that 10-item fixture happens to fit on one page. The module docstring now notes the reversal |
| `services/document-service/tests/test_quotation_visual_style.py` | modified | **Inverted** `test_logo_only_on_first_page_and_md_seal_size_unchanged` → `test_logo_and_seal_on_every_page_and_md_seal_size_unchanged`. Every page has both 120px header images, and the 130px MD seal appears exactly once |
| `loop/history/report-iter-1.md` | moved | from `loop/report.md`, per LOOP.md 1.2 |
| `loop/evidence/iter-2-*.png` | added | A4 |

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | A2 (after template change) | `test_header_and_parties_are_never_duplicated…`: `found it on page(s) [0, 1, 2, 3]`. `test_logo_only_on_first_page…`: `assert not True` | Both tests encode the old page-1-only rule, which the plan inverts | Inverted both, as the plan says (§4) |
| 2 | A1 `test_header_without_logo_or_seal_repeats_without_gaps` | `heading sits 55.4px down … assert 55.35 <= 50` | **My test was wrong**: 55.4px is the 1.2cm page margin plus the existing 10px `.header-top` margin. Page 1 behaved the same before this iteration, and the existing `test_no_blank_header_row_when_company_has_no_logo` allows < 80px | The test now uses the existing `< 80px` bound and also requires the heading at the same y on every page. The app is unchanged |

After attempt 2, A1–A5 all passed.

## 6. Final verification output
```
==> [verify] pytest tests/ -v (BASE_URL=http://localhost:8080/api)
tests/test_auth.py::test_admin_login_ok PASSED                           [  7%]
tests/test_auth.py::test_wrong_password_rejected PASSED                  [ 14%]
tests/test_auth.py::test_protected_route_without_token_rejected PASSED   [ 21%]
tests/test_auth.py::test_protected_route_with_garbage_token_rejected PASSED [ 28%]
tests/test_catalogue_smoke.py::test_category_and_product PASSED          [ 35%]
tests/test_documents_smoke.py::test_quotation_lifecycle PASSED           [ 42%]
tests/test_documents_smoke.py::test_quotation_pdf_supplier_block_shows_company_heading_and_selected_md PASSED [ 50%]
tests/test_health.py::test_service_reachable_via_gateway[auth-service] PASSED [ 57%]
tests/test_health.py::test_service_reachable_via_gateway[document-service] PASSED [ 64%]
tests/test_health.py::test_service_reachable_via_gateway[catalogue-service] PASSED [ 71%]
tests/test_health.py::test_service_reachable_via_gateway[search-service] PASSED [ 78%]
tests/test_health.py::test_web_ui_served PASSED                          [ 85%]
tests/test_search_smoke.py::test_product_becomes_searchable PASSED       [ 92%]
tests/test_search_smoke.py::test_document_becomes_searchable PASSED      [100%]
============================== 14 passed in 2.48s ==============================
==> [verify] docker compose down
verify.sh: PASSED
```
Unit tests: `pytest services/document-service/tests` → `159 passed in 63.68s`. The stack is down.

## 7. Found but not fixed (out of scope)
- **A page can hold just the column titles and the Total row.** In the layout tests' fixture, 80 short-named items produce a last page with the header, column titles, `Total: 8,000.00`, then Terms + MD. The Total row is separated from its items. Item rows are `break-inside: avoid` but have no `break-before: avoid` on the total row. This happened before this iteration too, and the orphan check treats the Terms on that page as real content. Possible fix: `tr.total-row { break-before: avoid; }` (keeps at least the last item with the Total).
- **Longer documents get more pages** (80 items: 4 → 5). This is inherent to repeating a 300px header. If it matters, a later iteration could use a more compact header from page 2 onwards (e.g. smaller logo), but that needs a second header variant, which `<thead>` can't do on its own.
- **`docs/SECURITY_HARDENING_LOG.md:4214`** describes the old header behaviour historically. It's left as is because it's a log, not a spec.
- Still open from iteration 1: the MinIO image decision (out of scope per PLAN), unit tests needing Python 3.12 (`psycopg2-binary==2.9.9`), and the stale `CompanyCreate.position` comment.

## 8. Blockers / decisions needed
None.

## 9. Suggestions for the next iteration
- Ask the user to review `loop/evidence/iter-2-page1.png`, `-page2.png` and `-last.png`, especially whether the full-size logo and seal on every page is wanted, or a smaller repeat from page 2 onwards.
- Consider keeping the Total row with at least one item row (§7, first bullet).
