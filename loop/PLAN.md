# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **32** |
| Goal | **Excel: move the MD (director) signature seal to the right** — into the last column (G), where the user drew the red box |
| Attempt limit | 5 per failing check |
| Status | READY FOR CLAUDE CODE |

## What the user wants
Screenshot `loop/evidence/iter-32-user-target-md-seal.png` (look at it). After iteration 31 the MD seal sits **centred on the F|G column boundary** (centre ≈ 773 px on the 987-px sheet; in the screenshot it covers the right part of F and the left part of G, and "Regards," is above it). The user says "move that to right like I show": the red box starts at **G's left edge** and is about as wide as the seal (≈ 190 px wide, ≈ 205 px tall) — i.e. the seal must sit **inside column G**, not straddling F and G. (Iteration 31 misread the earlier drawing; the user's new drawing is the rule.)

## What to change (`services/document-service/app/core/export_quotation.py`, signature block; `MD_SEAL_BOX` and the anchor code from iteration 31)
- MD seal box stays **180 × 200 px** (fit-inside, aspect ratio kept, never stretched).
- **Horizontal position**: the seal box's **left edge = column G's left edge + 4 px**; its centre is therefore ≈ G's left edge + 94 px. With labour columns (sheet has I as last column), "G" means the **last column** (Remark) — use the last column's left edge, so the layout is the same relative to the table.
- If the last column is narrower than 188 px (e.g. a custom width) clamp so the seal never extends past the table's right edge; the last column is 30 chars ≈ 215 px wide today, so the default fits.
- **"Regards,"** stays centred directly above the seal's centre (same x as the seal centre), not over the whole last column; it must not overlap the seal or the Terms text. Adjust the merged range / alignment accordingly (iteration 31 merged it over the last 3 columns; make its centre equal the seal centre).
- Vertical position/row reservation unchanged (≈ 10 rows, nothing overlaps text).
- **Company seal (top right) and the banner are NOT changed in this iteration.**
- The no-seal fallback (name/position text under "Regards,") keeps working and also centres on the same x.

## Tests
- Update `services/document-service/tests/test_xlsx_image_sizes.py`: MD seal left edge = last column's left edge + 4 (±3 px), width 180, height ≤ 200, right edge ≤ table right edge; centre of "Regards," == centre of the seal (±3 px); no image covers text or another image (existing overlap test still passes for all 5 variants, including the labour layout where the last column is I).
- Print: `test_xlsx_print_setup.py` still passes (1 page wide); the LibreOffice render check for page 1 still shows all three images.
- Evidence (LibreOffice render of a quotation with long item descriptions like the user's CCTV example, 3+ lines per item, so the signature area is on the first or second page): `loop/evidence/iter-32-xlsx-md-seal.png` — the seal must sit in the last column, as in the user's red box.
- Existing suites pass; `./scripts/verify.sh` passes (loop-test stack / in-process only).

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | MD seal fully inside the last column (left edge = G + 4 px), 180 × 200 box, aspect kept | unit test + screenshot compared with the user's red box |
| A2 | "Regards," centred over the seal; no overlaps; labour layout also correct | unit tests |
| A3 | Banner and company seal unchanged; still one page wide; no regressions | existing XLSX tests, `./scripts/verify.sh` |

## Out of scope — record under "Found but not fixed"
Anything about the banner, company seal, PDF.
