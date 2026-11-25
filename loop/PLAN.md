# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **36** |
| Goal | **PDF (and Excel): remove the empty space around logos and seals.** Auto-trim the blank margins inside the banner, logo, company seal and director seal images, and tighten the gaps (header → title, "Regards," → seal) |
| Attempt limit | 5 per failing check |
| Status | READY FOR CLAUDE CODE |

## What the user wants ("so much space in there, need to fix")
Look at both screenshots (committed): `loop/evidence/user-iter-36-img1-regards-gap.png` and `user-iter-36-img2-header-gaps.png`. They are **PDF** pages.
1. **img 1 (end of the quotation)**: "Regards," is at the top right and the director's seal/signature starts ≈ 100 px lower — a big hole between "Regards," and the signature (red arrow). The seal image (130 × 130 px, `.md-seal`) contains a large blank margin, mostly at the top, so the visible signature sits low. Expected: the visible signature/seal directly under "Regards," (gap ≈ 6–10 px).
2. **img 2 (top of page 1)**: the red box is the banner image. The banner artwork only fills part of it (its line ends at ≈ 60 % of the box width); the rest is white margin baked into the uploaded image. The arrows point at the dead space **right of the banner text / left of the company seal** and the **gap between the banner and the "QUOTATION" title** (≈ 40 px). Expected: the banner artwork fills its box, the title sits a small, even distance below it, and the seal sits close to the banner.

## Root cause (to confirm in the code first)
The uploaded images carry transparent or white padding; the templates/exporters size the **image box**, not the visible artwork. (`_standardize_image` in `routers/companies.py` and the letterhead banner path keep that padding.)

## What to build
1. **One shared helper `trim_blank_margins(image_bytes) -> bytes`** (new `services/document-service/app/core/image_trim.py`, Pillow): removes fully transparent borders **and** near-white borders (all channels ≥ 245, or alpha < 10) from PNG/JPEG/WebP; keeps a small padding (≈ 2 % of the shorter side, max 6 px, min 2 px); never returns an empty image (if everything is blank, return the original); keeps the format and alpha; SVG is skipped (returned unchanged); a corrupt image returns the original bytes (never raises). Idempotent (trimming twice = same result).
2. **Apply it at render time to every company image used in documents**: banner/logo (`logo_bytes`), company seal, **director (MD) seal** — in `export_pdf.py` (the data URIs fed to `quotation.html`: logo, seal, `director_seal_data_uri`) and in `export_quotation.py` (`_open_image` or where the XLSX images are opened). Render-time (not upload-time) so **existing** uploads are fixed without re-uploading and the stored originals stay untouched.
3. **Size boxes stay the same**; the trimmed artwork then fills them (fit-inside, aspect kept). PDF limits stay (`.letterhead` max 30 mm high, 148 mm wide with seal; `.logo`/`.seal` 120 px; `.md-seal` 130 px); Excel boxes from iterations 31–34 stay. Expected visible result: banner artwork a bit bigger/closer to the seal, signature right under "Regards,".
4. **Tighten the gaps in the PDF template** (`templates/quotation.html`): header block → "QUOTATION/INVOICE" title: reduce to about **5 mm** (look at the current margin/padding between the header row and the title); banner ↔ seal horizontal gap ≈ 5 mm (already in the CSS comment) — make sure no extra padding adds to it; "Regards," → seal: **6 px** (the CSS already says 6 px; after trimming it will be visible), no extra blank row above/below the seal. Keep the iteration 14 "tail stays together" and header-repeat behaviour and all page-margin math (the footer reservation) unchanged — **do not let the header get taller than before**; if trimming makes the page header shorter, the saved space just moves content up.
5. Excel: the same trimmed images are used; keep the iteration 31–34 box sizes and positions; re-verify the MD seal is inside the last column and the banner/seal gaps.
6. Add a short note in `docs/` (where company images are described) that blank margins are trimmed automatically.

## Tests
- `services/document-service/tests/test_image_trim.py`: table of cases — fully transparent border, white border, mixed, no border (unchanged within a couple of px padding), all-white image (returns original), tiny 1×1, JPEG with white margins, RGBA seal with transparent margins, corrupt bytes (returns original, no exception), SVG (unchanged), idempotent, padding rule (2 %, 2–6 px).
- PDF layout test (existing WeasyPrint box-tree style, `test_letterhead_header.py`, `test_quotation_pdf_layout.py`): with a test banner that has 40 % white padding on the right and a director seal with 60 % blank on top: the rendered banner `<img>` data is trimmed (decode the data URI and assert the trimmed size), the gap between the "Regards," box bottom and the seal's top is ≤ 12 px, the gap between the header block bottom and the title top ≤ 6 mm, nothing overflows the page width; the letterhead header still fits (`test_letterhead_header.py` passes), tail-together and header-repeat tests pass.
- Excel: image px size ratios correspond to the trimmed aspect (e.g. padded 1600×300 banner with 25 % margin → trimmed aspect used), existing `test_xlsx_image_sizes.py` expectations updated where the aspect changes; overlap test and one-page-wide print check still pass.
- Evidence (render a PDF with the user's Rangoon-style banner + seals, padded test images): `loop/evidence/iter-36-pdf-header.png` and `iter-36-pdf-regards.png` (compare with the user's screenshots) and one XLSX render.
- Existing suites pass; `./scripts/verify.sh` passes (loop-test stack / in-process only).

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Trim helper correct, safe (never raises/empties), idempotent | unit table |
| A2 | PDF: banner artwork fills its box; header→title gap ≤ 6 mm; banner–seal gap small | layout test + screenshot vs img 2 |
| A3 | PDF: "Regards," → visible signature gap ≤ 12 px | layout test + screenshot vs img 1 |
| A4 | Excel uses the trimmed images; sizes/positions from iterations 31–34 intact | XLSX tests |
| A5 | No regressions (tail-together, header repeat, one page wide); data safety | existing suites, `./scripts/verify.sh` |

## Out of scope — record under "Found but not fixed"
Trimming at upload time and re-saving originals; a per-company "image padding" setting; SVG trimming; changing which images exist.
