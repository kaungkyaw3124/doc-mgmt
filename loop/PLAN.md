# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **18** |
| Goal | **One image upload** per company. The existing **"Quotation header: Logo / Letterhead banner"** select decides how that same image is used: as a squared logo, or as a wide letterhead banner. Remove the separate letterhead upload field |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## What the user wants
Iteration 17 added a separate "letterhead" upload next to the logo upload. The user does not want two
fields. Their words: "when letter banner, we use the logo input image to be letter banner... the choose logo or
banner is good, keep that. I put the logo file, and if I choose logo it acts as a logo, and if I choose letter banner it
acts as a letter banner."
Real case that failed: a wide banner (≈720×190) was uploaded in the **Logo** field; the logo upload **stretches every
image to a fixed square** (`_standardize_image(..., STANDARD_LOGO_SIZE)` in `routers/companies.py`), so the PDF showed the banner squashed.

## Decisions already made (do not ask the user again)
- **One upload field** in the company form (label it **"Logo / banner image"**), one endpoint `POST /companies/{id}/logo`.
  The seal field stays separate and unchanged.
- **Keep both variants from the single upload** (no render-time image work, nothing lost when switching style):
  - `logo_object_key` = today's stretched/standardized square PNG (exactly as now; SVG left untouched as now) — used when `header_style="logo"`.
  - `letterhead_object_key` (existing column from iteration 17, **no new migration needed**) = the **proportional** original:
    keep the aspect ratio, downscale only if wider than the existing `LETTERHEAD_MAX_WIDTH`, re-save as PNG — used when `header_style="letterhead"`.
    Reuse `_standardize_letterhead`'s logic but make it **non-fatal** inside the logo upload: PNG/JPG/WebP get both variants; an SVG (or an image the letterhead path can't read) stores only the logo variant and the response says so.
  - Keep the existing 10 MB limit and the allowed-types rule; a clear 400 for bad files, as now.
- **Switching the select needs no re-upload**: changing `header_style` and saving is enough; export/gather logic from iteration 17 keeps working
  (letterhead style + no proportional variant → falls back to the logo, never crashes).
- **Companies uploaded before this change** only have the already-squared logo (the original is gone). They keep working as logos.
  In the form, when "Letterhead banner" is chosen and the company has a logo but **no** proportional variant, show a short hint:
  *"Re-upload your banner image once — the saved logo was squared."*
- **Remove the separate letterhead upload**: delete the `company-letterhead` file input, its JS (size/type check, upload call, `savedImages` entry),
  and `POST /companies/{id}/letterhead`. Keep `GET /companies/{id}/letterhead-url` (used for the preview and by the tick's "image loads back" check).
  Do not delete the DB column or stored objects. Update/remove tests of the removed endpoint accordingly.
- **Form behaviour**: select Logo → preview shows the saved logo; select Letterhead banner → the same file input stays visible and the preview shows
  the proportional banner (from `letterhead-url`) in a wide box; the tick after saving is shown only after the relevant saved image loads back
  (`verifySavedImages`) — for the banner style that means `letterhead-url`, for logo style `logo-url`.
- PDF/XLSX layout from iteration 17 (banner left of the seal, 148 × 30 mm box, seal stays) does **not** change.

## Tasks
1. Backend: make `POST /companies/{id}/logo` produce both variants (as above); remove `POST /companies/{id}/letterhead`; keep `GET .../letterhead-url`.
2. Frontend: one file input; the select only switches the preview/hint; remove the old letterhead input and code; update the hint text.
3. Make sure creating a **new** company (no id yet) works with the one input: the image is uploaded after the company is created, as the logo is today.
4. Tests and evidence (below). Update `docs/` if the company form is described.

## Tests
- Document-service unit tests: upload one 720×190 PNG through `/logo` → `logo_object_key` object is the stretched square, `letterhead_object_key` object has ≈3.79:1 proportions (±1%); a 3000×800 image → proportional variant downscaled to `LETTERHEAD_MAX_WIDTH` keeping the aspect; SVG upload → only the logo variant, 200 with a note, no crash; a non-image / >10 MB file → 400 and nothing stored; with `header_style="letterhead"` the exported PDF shows the banner at the right proportions **after a single `/logo` upload and no other call**; flipping `header_style` back to `logo` shows the squared logo again (compare with the existing logo-style layout numbers: unchanged); `POST /companies/{id}/letterhead` now returns 404/405; a company with only an old squared logo and `header_style="letterhead"` falls back to the logo without error.
- Playwright `tests/test_ui_letterhead_header.py` (rewrite; throwaway `loop-test` stack ONLY): create a company → upload a generated wide banner in the single input with "Logo" selected → save → quotation PDF shows the squared logo → edit the company, switch the select to "Letterhead banner", save (no new upload) → PDF page 1 has the banner rect in the top-left, left of the seal, aspect ratio ≈ the source (pymupdf `get_image_rects`) → the form shows only one image input and the tick appears. Also the re-upload hint shows for a company that has only an old logo.
- Generate banners with Pillow (synthetic circle + text); do not use the user's real logo.
- All existing tests pass; `./scripts/verify.sh` passes.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | One upload feeds both styles | the unit tests (variants, proportions) |
| A2 | Logo style unchanged | layout test numbers + `loop/evidence/iter-18-logo-p1.png` |
| A3 | Banner style from the same upload, proportions kept, seal stays | PDF test + `iter-18-banner-p1.png` |
| A4 | Form has a single image input, switch needs no re-upload, hint for old logos | Playwright + `iter-18-company-form-logo.png`, `iter-18-company-form-banner.png` |
| A5 | Removed endpoint/field leave no dead code | `grep -rn "company-letterhead\b\|/letterhead'" web services` shows nothing except `letterhead-url` and the column name |
| A6 | No regressions + data safety | `./scripts/verify.sh`, all unit suites; tests on `loop-test` only (report says which stack) |

## Out of scope — record under "Found but not fixed"
- Recovering the original of logos uploaded before this change (the file is gone).
- Any change to seal handling.
