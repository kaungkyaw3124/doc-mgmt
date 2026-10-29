# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **4** |
| Goal | Quotation page margins: **0.5 in (12.7 mm) on all four sides** (top changes 7.6 mm → 12.7 mm) |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## What the user wants
After reviewing iteration 3, the user wants the top margin back at 0.5 in,
the same as the other sides. Left / right / bottom stay exactly as they are.

## Tasks (all places that hold the 7.6 mm / 0.3 in top value)
1. `services/document-service/app/templates/quotation.html`
   - `@page` `margin: 7.6mm 12.7mm 24.14mm 12.7mm;` → `12.7mm 12.7mm 24.14mm 12.7mm`
   - Update the comments that say "top 0.3in (7.6mm)" / "starts right at 7.6mm" / "below the 7.6mm top margin".
2. `services/document-service/app/core/export_quotation.py`
   - `PageMargins(top=0.3, ...)` → `top=0.5`; update the comment.
3. Tests that hard-code the old top value (change only the top value and names):
   - `tests/test_page_margins.py`: `TOP = 7.6 * MM` → `12.7 * MM`; rename the two `*_7_6mm*` tests; XLSX tuple `(0.3, 0.5, 0.5, 0.5)` → `(0.5, 0.5, 0.5, 0.5)`; fix the docstring.
   - `tests/test_quotation_visual_style.py` around lines 216 and 233–234: the 7.6 mm floor → 12.7 mm, and its comments. Check that `MAX_TOP_CONTENT_PX = 50` still holds (12.7 mm = 48 px).
4. Do not touch the logo/seal size (user: keep them as they are), the footer, the
   bottom 24.14 mm value, or anything else.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Margin tests pass | `pytest services/document-service/tests/test_page_margins.py -v` |
| A2 | No regressions | `pytest services/document-service/tests`: all pass |
| A3 | Smoke passes | `./scripts/verify.sh` exits 0 |
| A4 | Visual proof | 40 items with logo + seal: `loop/evidence/iter-4-page1.png` (`-r 70`) and the measured top / left / right / footer-bottom mm on page 1 in the report |
| A5 | Page counts | 2 / 10 / 40 / 80 items, iteration 3 vs now |
