# report.md — Iteration 15

## 1. Summary
- **Status**: DONE
- **Goal**: A one-click **"Delete ALL products"** button on the Products page, for the user's development/testing stage. It is temporary and must be trivial to remove later
- **Ponytail skill loaded**: yes (loaded with the Skill tool at the start)
- **Branch / commit**: `dev` @ (this commit, see `git log`)
- **One-paragraph summary**: `DELETE /products` (catalogue-service) permanently deletes every product, including the ones in the recycle bin. It removes their sub-item links first, because `sub_product_id` has no ON DELETE CASCADE, then removes each product from Meilisearch and makes a best-effort attempt to delete its stored file from MinIO. It returns `{"deleted": N}` and leaves categories alone. It returns 403 when `ENVIRONMENT=production` or the user has view-only access. A new `GET /products/dev-config` → `{"delete_all_enabled": bool}` tells the UI whether to show the red **Delete ALL products (dev)** button next to "+ New product". One click (no confirm) calls the endpoint, refreshes the list (and the recycle bin if it is open) and shows "Deleted N products". All code sits inside `DEV-ONLY: delete-all-products` markers. Nginx/auth-service needed no change: `/api/products` has no per-method rule, so the call gets the same `products` RBAC check as the existing single delete.

## 2. Baseline (before any change)
```
$ ./scripts/verify.sh
======================== 24 passed, 8 skipped in 5.50s =========================
verify.sh: PASSED
```
(the 8 skips are the Playwright UI tests — Playwright isn't installed in verify.sh's venv)

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | Catalogue unit tests pass | PASS | in the `infra-catalogue-service` image + `requirements-dev.txt`: `python -m pytest tests -q` → `80 passed, 99 warnings in 2.17s` (6 new in `tests/test_delete_all_products.py`) |
| A2 | UI test passes | PASS | in `mcr.microsoft.com/playwright/python:v1.65.1-noble` (`--network host`, against the verify.sh stack): `pytest tests/test_ui_delete_all_products.py -v` → `2 passed in 5.78s`. The production case stubs `/api/products/dev-config` with `page.route`, because the test stack can't run with `ENVIRONMENT=production` (secrets_check refuses its placeholder secrets). The server-side 403 is unit-tested |
| A3 | No regressions | PASS | `./scripts/verify.sh` → `24 passed, 9 skipped` (the 9th skip is the new UI test file, same Playwright reason); catalogue unit suite 80 passed |
| A4 | Proof | PASS | `loop/evidence/iter-15-products-before.png` (3 products + red button), `loop/evidence/iter-15-products-after.png` (empty list + "Deleted 3 products"), taken by the UI test |
| A5 | Removal steps | PASS | §4 below |

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `services/catalogue-service/app/routers/products.py` L427–469 | added (DEV-ONLY block) | `GET /products/dev-config` + `DELETE /products`, placed above the `/trash` and `/{product_id}` routes |
| `web/index.html` L690–692 | added (DEV-ONLY block) | the red button, hidden by default |
| `web/js/app.js` L310 | added (one line, DEV-ONLY comment) | `initDeleteAllProducts()` call in `enterApp()` |
| `web/js/app.js` L4398–4425 | added (DEV-ONLY block) | dev-config check + click handler |
| `services/catalogue-service/tests/test_delete_all_products.py` | added | unit tests (SQLite harness from `test_duplicates.py`) |
| `tests/test_ui_delete_all_products.py` | added | Playwright tests + A4 screenshots |

**To remove the feature later**:
1. `grep -rn "DEV-ONLY: delete-all-products" services web` → delete each BEGIN…END block (products.py, index.html, app.js) and the single `initDeleteAllProducts(); // DEV-ONLY…` line in `web/js/app.js`.
2. Delete `services/catalogue-service/tests/test_delete_all_products.py` and `tests/test_ui_delete_all_products.py`.
3. `grep -rn "delete-all\|dev-config\|delete_all" services web tests` should return nothing. No migration, nginx or auth-service change to undo.

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | A2 (environment, not code) | `chrome-headless-shell: error while loading shared libraries: libnspr4.so` | host WSL has no Chromium system libs | ran the UI tests in `mcr.microsoft.com/playwright/python:v1.65.1-noble` with `--network host` against the running stack |

## 6. Final verification output
```
$ ./scripts/verify.sh --keep
======================== 24 passed, 9 skipped in 4.27s =========================
verify.sh: PASSED (stack left running: --keep)
```

$ pytest tests/test_ui_delete_all_products.py -v     (Playwright image)
tests/test_ui_delete_all_products.py::test_one_click_deletes_all_products PASSED [ 50%]
tests/test_ui_delete_all_products.py::test_button_hidden_in_production PASSED [100%]
============================== 2 passed in 5.78s ===============================

## 7. Found but not fixed (out of scope)
- **Single `DELETE /products/{id}` probably fails with an FK error (500) for a product that is another product's sub-item**: `product_sub_items.sub_product_id` has no `ondelete` (`services/catalogue-service/app/models.py:59`), and `delete_product` (`products.py`) doesn't clear those links. I found this while writing delete-all, which works around it. Suggested fix: `ondelete="CASCADE"` plus a migration, or delete the links in `delete_product`.
- **Single delete leaves the MinIO file behind**: `delete_product` never removes `image_object_key`. Delete-all does.
- **Running the UI test wipes the stack's catalogue**: it is the feature, but anyone who runs `tests/test_ui_delete_all_products.py` against a local stack with real dev data loses that data. verify.sh skips it today only because Playwright isn't in its venv.
- The extra header button pushes "+ New product" onto a second toolbar line at 1280px (see `iter-15-products-before.png`; `web/index.html` already warns about this). I left it, since the button is temporary and goes away when the feature is removed.
- Delete-all ignores `X-Allowed-Projects` (the plan says every row), so in dev a user restricted to some projects can still wipe products they can't see.

## 8. Blockers / decisions needed
None.

## 9. Suggestions for the next iteration
- Fix the sub-item FK on single delete (see §7).
- Remove this feature before the first production deploy.
