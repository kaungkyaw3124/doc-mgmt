# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **6** |
| Goal | Add the text **"Best Regards,"** directly above the MD seal, horizontally centered on the seal (PDF + XLSX) |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## What the user wants
At the bottom right of the quotation, the MD signature block shows the MD's
seal (or the name + position text when no seal is uploaded). Add one line,
**`Best Regards,`**, just above it. The text's horizontal center must line up
with the seal's center. If there is no seal, center it over the name/position
lines instead.

## PDF: `services/document-service/app/templates/quotation.html`
- The MD block is `.md-block` (right-aligned) > `.md-block-inner` (inline-block,
  200px, `text-align: center`) > `.md-seal` (130px) or `.md-name` / `.md-title`.
- Put the new line **inside** `.md-block-inner`, before the seal, so it inherits
  the same center. Use the body font (Arial 10.5pt, normal weight, color
  `#1a1a1a`) with a small gap (~6px) above the seal.
- Show it only when the MD block is shown (a director is selected). No director →
  no MD block and no text (current behaviour).
- Keep the break rules: `.md-block` stays attached to Terms (`break-before: avoid`)
  and is never alone on a page. The text must move with the seal; it must never
  end up on a different page from the seal.

## XLSX: `services/document-service/app/core/export_quotation.py` (around line 336)
- Write `Best Regards,` in the row just above the seal / name, centered across
  the same columns the seal covers (the seal is anchored at `G{row}`, the
  name/position fallback merges `F:G`). Arial, normal weight. Leave a blank row
  between it and the seal if needed, so the image doesn't cover the text.

## Tests
Add `services/document-service/tests/test_md_regards.py`:
1. PDF with director + seal: `Best Regards,` appears exactly once, on the same
   page as the seal, above the seal (its box bottom ≤ seal box top), and its
   horizontal center equals the seal's center ± 1 mm (WeasyPrint layout boxes,
   same style as `test_quotation_visual_style.py`).
2. PDF with director, no seal: text is above the MD name and centered on it ± 1 mm.
3. PDF without director: `Best Regards,` does not appear.
4. Page sweep (1, 3, 5, 8, 12, 20, 30, 80 items): text and seal always on the
   same page; the existing MD-orphan tests still pass.
5. XLSX with seal: the `Best Regards,` cell is above the seal's anchor row and
   in the seal's columns. XLSX without director: text absent.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | New tests pass | `pytest services/document-service/tests/test_md_regards.py -v` |
| A2 | No regressions | `pytest services/document-service/tests`: all pass |
| A3 | Smoke passes | `./scripts/verify.sh` exits 0 |
| A4 | Visual proof | Crop of the bottom-right MD area of the last page (with a real-looking seal PNG) saved to `loop/evidence/iter-6-md-regards.png`; report the measured center offset in mm |
| A5 | XLSX proof | Paste the cells around the seal (row/column of `Best Regards,` and the seal anchor) in the report |
