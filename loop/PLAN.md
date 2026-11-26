# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **37** |
| Goal | **Excel: remove the empty rows between the header images and the QUOTATION/INVOICE title** |
| Attempt limit | 5 per failing check |
| Status | READY FOR CLAUDE CODE |

## What the user wants ("so much space in there, remove that space, it looks ugly")
Screenshot `loop/evidence/user-iter-37-xlsx-gap.png`: the red box covers rows **9–13**, five empty rows between the header images (banner ends at row 8, company seal ends around row 9–10) and the title "QUOTATION" in row 14. Expected: the title starts right below the lowest image, with at most one small gap.

## Root cause (visible in `services/document-service/app/core/export_quotation.py` ~lines 274–300)
For a letterhead banner the code reserves the **whole box** (`header_h = BANNER_BOX[1] * scale + ROW_PX`, i.e. 197 + 20 px) instead of the **fitted image height `h`**. Since iteration 36 trims the banner's blank margins, the fitted banner is only ≈ 140–150 px high, so ≈ 3 rows of pure padding remain below it; plus `reserved = ceil((header_h + 10) / ROW_PX)` adds another row.

## What to change
- Reserve rows from the **actual fitted heights**: `header_h = max(banner h (fitted), squared-logo h, company-seal h)` — never the box height. Then `reserved = ceil((header_h + 6) / ROW_PX)` (explicit 15 pt rows as today), so the title row begins directly under the tallest image with a gap of **less than one row (≤ ~20 px)**. With the user's data (seal 190 px) that means the title in row 11 instead of 14.
- Keep the guarantees: nothing overlaps text (images end above the title row), banner/seal sizes and positions from iterations 31–36 unchanged (banner box 677 × 197 is still the **maximum** it can fill; the trimmed artwork fills its width first), print setup (one page wide), END USER block alignment from iteration 34, `print_title_rows` (which includes the header rows — the repeat on continuation pages gets shorter too, which is fine).
- Check the **same gap logic in the squared-logo header style** (no letterhead): reserved from the fitted logo/seal height, no extra empty rows.
- PDF is not part of this iteration.

## Tests
- Update `services/document-service/tests/test_xlsx_image_sizes.py` (and any layout test that hard-codes the title row): for letterhead + seal, letterhead without seal, squared logo + seal, and labour layout: compute the lowest image bottom (px) and the title row's top (px) → the gap is **≥ 0 and ≤ 20 px** (one row), and no image rectangle covers any non-empty cell (existing overlap test). With a tall banner (e.g. 1000×400 image → fitted 492 × 197) the reservation follows the banner's real height; with a wide banner (1600×300) the seal (190 px) decides.
- Row-count regression: for the iteration-31 fixture the number of empty rows between the last image and the title equals what the formula gives (document the expected numbers in the test).
- `test_xlsx_print_setup.py` still passes (one page wide, print titles); labour/units/tax/footer tests pass.
- Evidence: LibreOffice render of the quotation in the user's screenshot (Rangoon banner + seal): `loop/evidence/iter-37-xlsx-top.png`.
- Existing suites pass; `./scripts/verify.sh` passes (loop-test stack / in-process only).

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Title starts ≤ 20 px below the tallest header image (all header variants) | unit tests + screenshot |
| A2 | Nothing overlaps; sizes/positions from iterations 31–36 unchanged | existing overlap/size tests |
| A3 | One page wide, no regressions; data safety | existing XLSX suites, `./scripts/verify.sh` |

## Out of scope — record under "Found but not fixed"
The Excel repair message "String properties from /xl/worksheets/sheet1.xml" (open question with the user: needs the error log from Excel; do not change the footer in this iteration), PDF spacing.
