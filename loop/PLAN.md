# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **3** |
| Goal | Quotation page margins: **0.5 inch (12.7 mm) on all four sides** |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## What the user wants
Every printed thing on the page, including the logo/seal, the table and the
Customer Service footer, stays **0.5 in (12.7 mm) from each paper edge**,
and content starts right at that line. No extra hidden gaps.

Current PDF: `@page { margin: 1.2cm 11mm 3cm 11mm; }` in
`services/document-service/app/templates/quotation.html` (line ~21).
The user sees more white space at the top than wanted.

## Rules
| Edge | Target |
|---|---|
| Left / Right | 12.7 mm. Usable width = 210 − 25.4 = **184.6 mm**. Update every hard-coded `188mm` (e.g. `.pdf-footer` width, comment at line ~19) to match |
| Top | The top of the first visible element (logo/seal, or QUOTATION if there is no logo) is 12.7 mm from the top edge on **every** page. Remove stacked extra gaps (body margin, `.header-top` margins, h3/UA default margins above the first element) |
| Bottom | The **footer's bottom edge** (last "Phone:" line) is 12.7 mm from the bottom edge on every page. The footer lives in the bottom margin box, so `margin-bottom` must be 12.7 mm + the footer's real height. Compute it from the footer's actual size, don't guess, and add a short comment with the math. Body content must never overlap the footer |

Keep: header repeating on every page (iteration 2), 1–2 item quotes on 1 page,
the `.terms` / `.md-block` break rules, the footer content and style.

## Excel (XLSX)
In `services/document-service/app/core/export_quotation.py`, set
`ws.page_margins` left/right/top/bottom = 0.5 (openpyxl uses inches), and
keep header/footer margins inside that (≤ 0.3). Keep `print_title_rows`.

## Tests
- Add `services/document-service/tests/test_page_margins.py`, using WeasyPrint
  layout boxes like `test_quotation_visual_style.py`, with 40 items, logo and seal:
  1. On every page, the leftmost and rightmost content boxes sit at 12.7 mm ± 0.5 mm from the edges.
  2. On every page, the topmost visible box (logo image) starts at 12.7 mm ± 0.5 mm.
  3. On every page, the footer's last line ends 12.7 mm ± 1 mm from the bottom, and no body box overlaps the footer box.
  4. No logo/seal: QUOTATION title starts at 12.7 mm ± 0.5 mm.
  5. 2 items → still 1 page.
  6. XLSX: `ws.page_margins` left/right/top/bottom == 0.5.
- Existing tests that hard-code the old margins (11 mm, 188 mm, 1.2 cm, 3 cm)
  may be updated to the new values. List each one in the report.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | New tests pass | `pytest services/document-service/tests/test_page_margins.py -v` |
| A2 | No regressions | `pytest services/document-service/tests`: all pass |
| A3 | Smoke passes | `./scripts/verify.sh` exits 0 |
| A4 | Visual proof | 40-item quote with logo, seal and MD. Save page 1 and the last page at `-r 70` to `loop/evidence/iter-3-page1.png` / `iter-3-last.png`. In the report, give the measured mm from each edge on page 1 |
| A5 | Page counts | Report page counts for 2 / 10 / 40 / 80 items, before vs after |

## Out of scope
MinIO image decision; Total-row orphan (iteration 2 report §7) — the user decides later.
