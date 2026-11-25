# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **33** |
| Goal | **Excel: make the header banner bigger** — to the red box the user drew (≈ 625 × 140 px, starting at the sheet's left edge) |
| Attempt limit | 5 per failing check |
| Status | READY FOR CLAUDE CODE |

## What the user wants
Screenshot `loop/evidence/iter-33-user-target-banner.png` (look at it; an INVOICE export of a "Winterfell" letterhead banner). The banner (logo + company name + address/phone line) is still small: today its visible part is ≈ 455 × 95 px and it starts at column B. The red rectangle shows the wanted size: it starts at the **left edge of column A** and ends inside column E (≈ 625 px wide), and is **≈ 140 px tall** (rows 1–7/8). The company seal on the right (column F) is NOT part of this change.

## What to change (`services/document-service/app/core/export_quotation.py`, letterhead banner; `BANNER_BOX = (560, 115)` and the placement code ~lines 143–260 from iteration 31)
- **Banner box = 625 × 140 px** on the default 987-px sheet (keep the "relative to table width" scaling: 625/987 ≈ 63.3 % wide, 140 px tall scaled the same way), fit-inside with the image's own aspect ratio (never stretched, no crop). For a ≈ 4.8:1 banner that makes it ≈ 625 × 130 px — about **1.37× bigger** than now.
- **Placement**: anchored at the **left edge of column A** (x ≈ 3 px), no longer at column B. Top at row 1.
- **Never touches the company seal**: keep a gap of at least 25 px between the banner's right edge and the company seal's left edge (the seal is ≈ 170 px wide, centre ≈ 777 → left edge ≈ 692; 3 + 625 = 628 leaves 64 px). If the seal is absent the banner may use the same box (do not grow it further).
- **Reserve rows** so the taller banner ends above the "QUOTATION / INVOICE" title row with a visible gap (≥ 1 empty row); recompute the header row reservation from the banner height ÷ row height (as iteration 31 does for images), not a fixed count; the rest of the sheet shifts down accordingly. Print area, "fit to one page wide", and the table layout stay correct.
- Squared-logo header style (no letterhead banner) is unchanged (170 px logo from iteration 31).
- Works with and without labour columns (scale by table width) and when the banner image is very wide or nearly square (fit-inside rules).

## Tests
- Update `services/document-service/tests/test_xlsx_image_sizes.py`: default layout banner box 625 × 140 (a 1600×300 test image → 625 × 117; a 1000×400 image → 350 × 140), left edge x ≈ 3 (±3 px, column A), right edge ≥ 25 px left of the seal's left edge, bottom above the title row; with labour layout the box scales with the wider table; no image covers text or another image (the five existing variants).
- `test_xlsx_print_setup.py` (one page wide) and all other XLSX tests pass; MD seal and company seal positions from iterations 31–32 unchanged.
- Evidence: LibreOffice render `loop/evidence/iter-33-xlsx-banner.png` of a letterhead quotation with seal (compare with the user's red box).
- Existing suites pass; `./scripts/verify.sh` passes (loop-test stack / in-process only).

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Banner fits a 625 × 140 box from column A, aspect kept | unit test + screenshot vs the user's red box |
| A2 | Gap to company seal ≥ 25 px; nothing overlaps text; title row below | unit tests |
| A3 | Seals unchanged, one page wide, no regressions | existing XLSX tests, `./scripts/verify.sh` |

## Out of scope — record under "Found but not fixed"
PDF, company seal, MD seal, squared-logo style.
