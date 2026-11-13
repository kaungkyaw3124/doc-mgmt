# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **15** |
| Goal | A one-click **"Delete ALL products"** button on the Products page, for the user's development/testing stage. It is temporary and must be trivial to remove later |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## What the user wants
The user is still developing and wants to wipe the whole product catalogue between
tests without clicking Delete on every row. Their words: "just 1 click", "I want to
test and delete, after that I'm gonna remove that" (the button itself). Every row
already has its own Delete button (moves the product to the recycle bin). Nothing new
is needed on documents, projects, companies or customers — do **not** touch them.

## Decisions already made (do not ask the user again)
- **One click, no confirm dialog, no typed word.** The user explicitly asked for this.
- **Permanent delete** (not the recycle bin): the point is to clear the catalogue for
  the next test. This includes products already sitting in the recycle bin.
- **Dev-only guard (the one safety rail I'm adding)**: the button is rendered and the
  endpoint answers only when `ENVIRONMENT` is not `production` (the catalogue service
  already has `environment: str = "development"` in `app/core/config.py`; compose passes
  `ENVIRONMENT: ${ENVIRONMENT:-development}`). In production the endpoint returns 403 and
  the UI hides the button, so it cannot wipe the live VPS by accident. Do not add any other friction.
- Same permission rule as the existing product delete (`_require_edit_access`); a view-only user gets 403.

## Tasks
1. **Endpoint** in `services/catalogue-service/app/routers/products.py`:
   `DELETE /products` (collection, no id) — define it so it does not clash with
   `DELETE /products/{product_id}` or with `/trash`, `/duplicate-check` etc. (put it
   above the `/{product_id}` routes). It must:
   - return 403 when `settings.environment == "production"` or the user has view-only access;
   - delete every product row, including soft-deleted ones, and their sub-item links
     (check the FK / cascade on `product_sub_items`; do not leave orphans or fail on FK errors);
   - remove each product's Meilisearch entry (reuse `remove_product_from_index`, or one
     bulk call if the index code has one) and best-effort delete the stored product
     image/file from MinIO (never fail the request because a file is missing);
   - return `{"deleted": <count>}`.
   Categories are **not** deleted.
2. **Gateway/auth**: check `infra/nginx/nginx.conf.template` and the auth-service RBAC
   mapping so the call is allowed for the same users who can delete a product today and
   is not blocked by a method/path rule. Document-service must not be affected.
3. **Button** on the Products page (`web/index.html` next to the "+ New product" button,
   ~line 690, handler in `web/js/app.js`): red danger button labelled
   **"Delete ALL products (dev)"**. One click calls the endpoint, then refreshes the
   products list (and the recycle bin list if it is visible) and shows a short banner
   "Deleted N products". Hidden when the environment is production — expose the
   environment to the UI with the smallest existing mechanism (e.g. a field on an
   endpoint the UI already calls, or a small `GET /config`-style response); do not
   hard-code a hostname check.
4. **Easy to remove later**: wrap everything in clearly marked blocks so the user (or a
   later iteration) can delete the feature in one go — `# DEV-ONLY: delete-all-products BEGIN/END`
   in Python, `<!-- DEV-ONLY: delete-all-products -->` / `// DEV-ONLY ...` in the web files —
   and put the endpoint in the smallest possible code block. List every touched file and
   line range under "Changes made" in the report, plus the exact steps to remove it.
5. Do not change existing delete/trash/restore behaviour, and do not touch documents,
   projects, companies or customers.

## Tests
- `services/catalogue-service/tests/` (follow the existing pattern there): with 3 products
  (one in the recycle bin, one with sub-items) `DELETE /products` → 200 `{"deleted": 3}`, a
  following `GET /products`, `/products/trash` are empty, sub-item rows are gone, the search
  index removal was called for each product (mock), categories still exist; with
  `ENVIRONMENT=production` → 403 and nothing deleted; view-only access → 403 and nothing deleted;
  a MinIO error while deleting a file does not fail the request; an empty catalogue → `{"deleted": 0}`.
- Playwright `tests/test_ui_delete_all_products.py`: create 3 products through the API, sign in,
  open Products, click **Delete ALL products (dev)** once (no dialog may appear — fail if a
  `confirm` dialog fires), the table shows the empty state and the banner says "Deleted 3 products";
  a second test checks the button is absent when the environment is production (use whatever
  switch the stack supports; if it can't be switched in the test stack, test the UI logic with
  a stubbed response and say so in the report).
- All existing tests still pass.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Catalogue unit tests pass | the new catalogue tests + the whole catalogue suite |
| A2 | UI test passes | `pytest tests/test_ui_delete_all_products.py -v` |
| A3 | No regressions | `./scripts/verify.sh` passes; catalogue service unit suite passes |
| A4 | Proof | `loop/evidence/iter-15-products-before.png` (Products page with 3+ products and the red button), `iter-15-products-after.png` (empty list with the "Deleted N products" banner) |
| A5 | Removal steps | the report lists every DEV-ONLY block and the exact steps to remove the feature |

## Out of scope — record under "Found but not fixed" if you notice them
- Delete-all for documents, projects, companies, customers (the user did not ask).
- Wiping the recycle bin for other item types.
