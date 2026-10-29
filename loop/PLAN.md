# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **5** |
| Goal | Quotation **top** margin 0.3 in (7.6 mm). Left / right / bottom stay 0.5 in (12.7 mm) |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## What the user wants
After seeing the 0.5 in top margin on the real PDF, the user wants the top
0.2 in smaller: **0.3 in = 7.6 mm**. This is the same top value as iteration 3
(see `git show` of the `loop iter 3` commit for exactly what changed then).
Everything else stays the same.

## Tasks (only the top value: 12.7 mm / 0.5 in → 7.6 mm / 0.3 in)
1. `services/document-service/app/templates/quotation.html`: `@page` `margin: 12.7mm 12.7mm 24.14mm 12.7mm;` → `7.6mm 12.7mm 24.14mm 12.7mm`. Update the comments about the top value.
2. `services/document-service/app/core/export_quotation.py`: `PageMargins(top=0.5, …)` → `top=0.3`. Update the comment.
3. Tests, changing only the top value and test names:
   - `tests/test_page_margins.py`: `TOP` → `7.6 * MM`; rename the two `*_12_7mm*` top tests to `*_7_6mm*`; XLSX tuple → `(0.3, 0.5, 0.5, 0.5)`; fix the docstring.
   - `tests/test_quotation_visual_style.py`: the top floor → `7.6 * MM`, plus its comments.
4. Do not touch the logo/seal size, the footer, left/right/bottom, or anything else.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Margin tests pass | `pytest services/document-service/tests/test_page_margins.py -v` |
| A2 | No regressions | `pytest services/document-service/tests`: all pass |
| A3 | Smoke passes | `./scripts/verify.sh` exits 0 |
| A4 | Visual proof | `loop/evidence/iter-5-page1.png` (40 items, logo + seal, `-r 70`) and the measured top / left / right / footer-bottom mm on page 1 in the report |
