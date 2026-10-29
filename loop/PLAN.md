# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **8** |
| Goal | Stop duplicate products: **block** exact duplicates, **warn** on same/similar names, **skip + report** duplicates in bulk import, and a **"Possible duplicates"** review page |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## Background
`services/catalogue-service`: `Product.sku` is unique, but SKUs are generated
(`_generate_sku`), so two products with the same name and description can be
created freely, one by one or through `POST /api/products/bulk-import`.

## Definitions (use one shared helper, e.g. `app/core/duplicates.py`)
- `norm(text)`: `None`/blank → `""`; otherwise casefold, strip, and collapse all
  whitespace runs to one space. Nothing else.
- **Exact duplicate**: another **active** (`is_deleted == False`) product with
  `norm(name)` **and** `norm(description)` equal.
- **Same name**: another active product with equal `norm(name)` but a different description.
- **Similar**: another active product whose `norm(name)` has
  `difflib.SequenceMatcher(...).ratio() >= 0.88` (not exact or same-name). Return the
  top 5 by ratio. Use stdlib `difflib`; no new dependency. Fine for catalogue
  sizes up to ~10k. Add a `shortcut:` comment noting the limit.
- Trashed products are ignored everywhere except restore (below).

## Backend: `services/catalogue-service/app/routers/products.py`
1. **Create** (`POST /api/products`): exact duplicate → **409** with detail
   `{"message": "This item already exists", "existing": {"id", "sku", "name"}}`.
2. **Edit** (`PATCH /api/products/{id}`): run the exact check **only if name or
   description changed**, excluding the product itself. (Existing duplicate pairs
   must still allow price/category/file edits.) Same 409.
3. **Restore** (`PATCH /{id}/restore`): if restoring would create an exact
   duplicate of an active product → 409, same shape.
4. **New** `GET /api/products/duplicate-check?name=&description=&exclude_id=`
   → `{"exact": [...], "same_name": [...], "similar": [...]}`. Each item has
   `id, sku, name, description, category`. Same auth/visibility filter as
   `GET /api/products`. Declare the route **before** `/{product_id}` so it isn't
   treated as an id.
5. **New** `GET /api/products/duplicates` → groups across the whole active
   catalogue: `{"exact_groups": [[...],...], "same_name_groups": [[...],...]}`,
   with only groups of 2 or more, sorted by name. Same auth/visibility filter.
   Similar-name groups are not needed here.
6. **Bulk import** (`POST /bulk-import`):
   - Skip a row if it is an exact duplicate of an active product, **or** of an
     earlier row in the same file. Add it to `skipped: [{row, name, reason, existing_sku}]`.
     `reason` is `"already exists"` or `"duplicate of row N"`.
   - A row that only matches by name is still created, but added to
     `warnings: [{row, name, reason: "same name as <sku>"}]`.
   - Keep the existing response fields and the file-number/row-position rules
     exactly (a skipped row still "uses up" its position number, as blank rows do).
   - Check the Nginx gateway routes `/api/products/duplicate-check` and
     `/api/products/duplicates` through the same auth as the other product routes
     (`infra/nginx/nginx.conf*`). They should, via the `/api/products` prefix.

## Frontend: `web/index.html` + `web/js/app.js`
1. **Product form (create/edit)**: before submitting, call `duplicate-check`
   (pass `exclude_id` when editing).
   - `exact` not empty → show "This item already exists: <sku> <name>", with a
     link that opens the existing product, and don't submit.
   - otherwise, `same_name` or `similar` not empty → show a warning dialog listing
     them (sku, name, short description), with **Use existing** (opens that product,
     closes the form) and **Create anyway** (submits).
   - Also handle a 409 from the server (race) with the same "already exists" message.
2. **Bulk import result**: after upload, show counts (created / skipped /
   warnings) and a list of skipped rows with reason and warnings.
3. **"Possible duplicates" page**: a button in the Products section opens a view
   listing `exact_groups` first (red label "Exact duplicates"), then
   `same_name_groups` ("Same name"). Each row shows sku, name, description,
   category, and the existing **Move to trash** action. The groups refresh after
   trashing.
4. Match the existing UI style; no new libraries.

## Tests
- `services/catalogue-service/tests/test_duplicates.py`:
  1. `norm` cases: case, extra spaces, None vs "".
  2. Create exact dup → 409 with the existing sku. Same name + different description → 201.
  3. Edit: changing price on one of an existing duplicate pair → 200; renaming into an exact dup → 409.
  4. Restore into an exact dup → 409.
  5. `duplicate-check`: returns exact / same_name / similar correctly, `exclude_id`
     works, a trashed product is not returned, typo `"Cantilver Shelf 750mm"` finds
     `"Cantilever Shelf 750 mm"` as similar.
  6. `duplicates`: groups are correct and exclude trashed and singletons.
  7. Bulk import: existing-dup row and within-file dup row are skipped with the right
     reasons; a same-name row is created with a warning; file numbering is unchanged
     (a file for a row after a skipped row still attaches to the right product).
- Smoke (`tests/test_catalogue_smoke.py`): through the gateway, create a product,
  then creating it again → 409; `duplicate-check` and `duplicates` → 200; clean up
  (trash) what you created.
- UI (Playwright, like iteration 7; skip if unavailable): create a product, try to
  create the same one again → the "already exists" message is shown; create one with
  the same name and a different description → the warning dialog appears → Create anyway works.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | New unit tests pass | `pytest services/catalogue-service/tests/test_duplicates.py -v` |
| A2 | No regressions | `pytest services/*/tests`: all pass (run each service's suite) |
| A3 | Smoke passes | `./scripts/verify.sh` exits 0 |
| A4 | UI proof | `loop/evidence/iter-8-exact-block.png`, `iter-8-warning.png`, `iter-8-import-result.png`, `iter-8-duplicates-page.png` |
| A5 | Existing data | With 2 pre-existing exact duplicates in the DB: the list still loads, both appear on the duplicates page, and editing the price of one works |

## Out of scope
Merging duplicates (re-pointing documents to the kept product). Users trash the extras manually.

## Also from iteration 7 (no action needed)
The new "Customer" column in the documents list stays (user to confirm).
