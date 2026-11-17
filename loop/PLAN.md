# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **17** |
| Goal | Per-company **header style**: a company can use either its normal **logo** or a wide **letterhead banner** (logo + company name + address in one image) in the top-left header area of the quotation PDF and XLSX. The seal stays where it is |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## What the user wants
Some companies' "logo" is really a wide letterhead (example: a banner with a round logo, the big
text "WINTERFELL Co., Ltd." and a two-line address underneath; roughly 720×190 px, aspect ≈ 3.8:1).
Today it is squeezed into the small logo slot. The user marked the target area on a quotation PDF with
a red rectangle: **the whole top-left header area, from the left page margin up to just before the
round seal on the right, about 28–30 mm tall**. The letterhead banner should fill that area (keeping
its proportions), and the seal stays at the top right as now. Companies that use a normal logo must look
exactly as they do today.
Earlier decision (user): when creating/editing a company the user chooses **Logo** or **Letterhead banner**.

## Decisions already made (do not ask the user again)
- Start from the existing, unmerged branch **`origin/letterhead-header`** (commit `8fae36b`,
  "Company header style: logo or full-width letterhead banner"): it already has `Company.header_style`
  (`"logo"|"letterhead"`, default `"logo"`), `Company.letterhead_object_key`, migration
  `0007_company_header_style_letterhead.py`, `POST /companies/{id}/letterhead`,
  `GET /companies/{id}/letterhead-url`, `_standardize_letterhead` (keeps proportions, downscales above 2000 px),
  gather logic, PDF/XLSX changes, UI select + upload field, and `tests/test_letterhead_header.py`.
  `git cherry-pick 8fae36b` (or merge) onto the current branch and resolve conflicts (the company form,
  `app.js`, `schemas.py`, migrations were changed since). **Re-number the Alembic migration** so it chains
  after the current head of document-service (check `alembic heads`; there must be exactly one head).
- **Difference from that branch: the seal is NOT dropped.** The branch put the banner across the full width and hid
  the seal. Now: banner in the left area, seal still on the right in its usual position. If the company has no seal, the banner may use the full width.
- Banner box: width = content width minus the seal column (and a small gap), max height ≈ 30 mm (≈ 115 px; use the
  same box the red rectangle shows, not a taller one). Scale to fit **inside** the box keeping the aspect ratio,
  align **left**, never stretch or crop. A very tall image is limited by the height, a very wide one by the width.
- Quotation text below the header (title, date, supplier/end-user) keeps its current spacing — the header area
  must not grow taller than today's logo area by more than a few mm.
- XLSX: banner image placed in the same top-left header rows (A:E or what the current layout uses), seal stays right.
  Row heights reserved from the image's aspect ratio so nothing overlaps.
- Company form (`web/index.html` / `web/js/app.js`): a **"Quotation header"** select (Logo / Letterhead banner).
  Choosing Letterhead shows the letterhead upload (with a preview of the saved image) and hides the logo upload,
  choosing Logo does the opposite; both images are kept in storage when switching so nothing is lost.
  Saving shows the success tick from iteration 15 only after the saved image loads back (`verifySavedImages`).
  Accept png/jpg/webp; show a clear error for other types or files above a sensible size limit.
- Backups (iteration 16) already include MinIO objects, so the letterhead file is covered; verify with a quick round-trip test if cheap.
- No change to documents' data model or API payloads except the new company fields.

## Tasks
1. Bring in `letterhead-header` (cherry-pick), fix conflicts, fix the migration chain, run the existing tests.
2. Change the PDF template/export so the banner fills the left header box and the seal stays at the right (see decisions).
3. Same for XLSX.
4. Finish the company form UX (select, upload, preview, tick, validation errors).
5. Tests and evidence (below).
6. Update `docs/` where the company form is described, if such a section exists.

## Tests
- Document-service unit tests (extend `tests/test_letterhead_header.py`): header_style default `logo`; switching to `letterhead` without an uploaded banner falls back to the logo (no crash); upload png/jpg/webp ok, text file / huge file rejected; 3000×800 image downscaled keeping the aspect ratio; migration up/down works on SQLite/Postgres as the other migrations do.
- PDF layout (walk the WeasyPrint box tree like the other layout tests): with a 720×190 banner → banner box inside the left area (right edge left of the seal's left edge, height ≤ ~30 mm), aspect ratio within 1% of the source, left edge on the left page margin; seal still present at its usual right position; with a very wide (1600×120) and a very tall (200×400) image → still inside the box, no overflow into the title/date; a company with `header_style="logo"` renders **identically** to before (compare the box positions of logo and seal against the current layout test numbers); no banner bleeding past the page margins; 1-page and multi-page quotations both fine; the existing tail-layout tests still pass.
- XLSX: banner anchor row/col, size in the reserved rows, seal still placed, no overlap with the "QUOTATION" title row.
- Playwright `tests/test_ui_letterhead_header.py` (throwaway `loop-test` stack ONLY): create a company → choose Letterhead → upload a generated banner → tick appears → preview visible → switch back to Logo and the logo field returns → create a quotation and fetch the PDF → page 1 contains the banner image (use pymupdf `get_images`/`get_image_rects` to check the rect is in the top-left, left of the seal rect).
- Generate test banners with Pillow (a synthetic 720×190 image with a circle, big text and two text lines). Do **not** use the user's real logo.
- All existing tests pass; `./scripts/verify.sh` passes.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Banner fills the left header box, proportions kept, seal stays right | PDF layout tests + `loop/evidence/iter-17-letterhead-p1.png` (rendered quotation page 1) |
| A2 | Logo companies unchanged | layout test comparing logo/seal boxes with the current numbers + `iter-17-logo-p1.png` |
| A3 | XLSX correct | test output + a short cell/image-anchor dump in the report |
| A4 | Company form works end to end | Playwright test + `iter-17-company-form-letterhead.png` |
| A5 | Migration is a single clean head | `alembic heads` output in the report |
| A6 | No regressions + data safety | `./scripts/verify.sh`, all unit suites, tests on `loop-test` only (report says which stack) |

## Out of scope — record under "Found but not fixed"
- Showing the letterhead on pages 2+ (the header repeats only if it already does for the logo; keep current behaviour).
- Any other header changes (fonts, title position).
