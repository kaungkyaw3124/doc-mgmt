# report.md — Iteration 27

## 1. Summary
- **Status**: DONE
- **Goal**: "Not a duplicate" for products. On the "Products — possible duplicates" page the user can mark a "Same name" group as different products; it stops showing (until a product in it changes), without renaming anything.
- **Ponytail skill loaded**: yes
- **Branch / commit**: `dev` @ (this commit)
- **One-paragraph summary**: Each group in the Same-name section has a new **Not a duplicate** button (tooltip "These are different products — stop warning me"). Clicking it saves one dismissal per different-description pair (new table `product_duplicate_dismissals`, migration `0003_duplicate_dismissals`). Each row stores a sha256 fingerprint of each product's `norm(name)|norm(description)`. A pair stays hidden only while both fingerprints still match. If either product's name or description is edited, the group shows again. `GET /products/duplicates` leaves out a same-name group when every one of its different-description pairs is dismissed, and returns a new `hidden_count` (the number of same-name groups hidden). A collapsed **Hidden pairs (N)** section at the bottom of the page lists the hidden pairs, each with a **Restore** button. Exact duplicates cannot be dismissed: a request containing only an exact pair gets 422, and in a mixed request the exact pairs are skipped. Exact groups are listed as before. Dismissals are per group, need edit access, and give 404 outside the caller's scope.

### Task 1 findings
- `core/duplicates.py::groups()` groups by `(group_id, norm(name))`. Same-name groups need 2+ distinct descriptions. Its only caller is `GET /products/duplicates`.
- The UI page (`loadProductDuplicates` in `web/js/app.js`) renders the groups as tables with "Move to trash". The Products page's "Possible duplicates" link shows **no count**, so nothing there needed the new numbers.
- **Security gap found and fixed**: nginx's `/api/products` location never set `X-Username`, so a header sent by the client passed straight through to the move-audit log (`_move_products`). It is now always taken from `/verify`, the same as on other locations. The fix was needed for `created_by` anyway.

### Choices
- **3+ products, then a new one added**: the **whole group** reappears (the simpler option). One undismissed pair is enough to show it. Test: `test_group_of_three_then_a_fourth_shows_the_whole_group`.
- Dismissing requires every product to have the same normalized name (otherwise 422). The button only exists on same-name groups, and the rule keeps the stored data meaningful.
- Dismissing again refreshes the stored fingerprints (it is idempotent and does not add a second row), so a group can be dismissed again after an edit.
- Stale dismissals (a product edited since) are kept in the table but are ignored and not listed. Hard-deleting a product removes its dismissals through `ON DELETE CASCADE`.

## 2. Baseline (before any change)
```
COMPOSE_PROJECT_NAME=loop-test ... ./scripts/verify.sh --keep
======================= 92 passed, 21 skipped in 47.72s ========================
verify.sh: PASSED (stack left running: --keep)
```

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | Dismissed same-name group no longer listed; survives reload | PASS | unit `test_dismiss_hides_edit_brings_back_restore_brings_back` (`([], 1)` after dismiss); Playwright `test_ui_not_duplicate.py` (button gone, tick visible, still gone after `page.reload()`); `iter-27-duplicates-before.png` |
| A2 | Editing name/description brings the warning back | PASS | same unit test: PATCH description → `([["HD-1","HD-2"]], 0)` and `/dismissed` is `[]` |
| A3 | Restore from "Hidden pairs" works | PASS | unit: DELETE → 204, listed again, second DELETE → 404; Playwright: Hidden pairs → Restore → button is back; `iter-27-duplicates-hidden.png` |
| A4 | Exact duplicates unaffected and not dismissable | PASS | `test_exact_duplicates_are_not_dismissable_and_stay_listed`: exact pair → 422; mixed group → 2 pairs dismissed, exact group still `[["C-1","C-2"]]` |
| A5 | Per-group isolation and edit-access rules | PASS | `test_group_scope` (other group → 404 on dismiss and restore; group B's same names still listed; superuser can act on any group); `test_bad_requests` (view access → 403, two groups → 422, unknown id → 404) |
| A6 | Single migration head, no regressions, data safety | PASS | heads `['0003_duplicate_dismissals']`; Postgres check: create_all minus the table → stamp 0002 → upgrade head ×2 → table, both indexes and `uq_dismissal_pair` present, both FKs `ondelete: CASCADE`. verify.sh `92 passed, 22 skipped` PASSED; catalogue unit `106 passed`; UI `43 passed, 7 skipped`. **Data safety: all tests ran on the `loop-test` stack (port 18080), in-process SQLite, or a throwaway `m27_cat` database (dropped afterwards). The user's stack was only backed up and rebuilt.** |

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `services/catalogue-service/app/models.py` | modified | `ProductDuplicateDismissal` model |
| `services/catalogue-service/alembic/versions/0003_duplicate_dismissals.py` | added | guarded, idempotent migration (table, unique pair, 2 indexes, cascade FKs) |
| `services/catalogue-service/app/core/duplicates.py` | modified | `fingerprint`, `pair`, `effective_dismissals`; `groups()` filters dismissed groups and returns `hidden_count` |
| `services/catalogue-service/app/routers/products.py` | modified | `/duplicates` uses dismissals; `POST /duplicates/dismiss`, `GET /duplicates/dismissed`, `DELETE /duplicates/dismissed/{id}` |
| `services/catalogue-service/app/schemas.py` | modified | `DismissDuplicates` body (2–100 ids) |
| `infra/nginx/nginx.conf.template` | modified | `/api/products` now forwards `X-Username` from `/verify` (it used to pass the client's copy through) |
| `web/js/app.js` | modified | Not a duplicate button, success tick, Hidden pairs section with Restore |
| `services/catalogue-service/tests/test_duplicate_dismissals.py` | added | unit + scope tests |
| `services/catalogue-service/tests/test_concurrency.py` | modified | expected head moved from 0002 to 0003 (new migration) |
| `tests/test_catalogue_smoke.py`, `tests/test_group_isolation.py` | modified | account for the new `hidden_count` key (it is an int, not a list of groups) |
| `tests/test_ui_not_duplicate.py` | added | Playwright flow + evidence screenshots |
| `loop/evidence/iter-27-duplicates-{before,hidden}.png` | added | evidence |

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | catalogue unit `test_single_alembic_head` | `['0003_duplicate_dismissals'] != ['0002_concurrency']` | the test pins the head; a new migration moves it | expect 0003 |
| 2 | verify.sh `test_duplicate_product_is_blocked_and_reported`, `test_exports_files_audit_and_product_extras_are_separated[×2]` | `Extra items: 'hidden_count'` / `'int' object is not iterable` | both tests iterate over every response key; the plan adds `hidden_count` | key set updated; isolation test skips `hidden_count` |

## 6. Final verification output
```
COMPOSE_PROJECT_NAME=loop-test COMPOSE_OVERRIDE=infra/docker-compose.loop-test.yml GATEWAY=http://localhost:18080 ALLOW_DESTRUCTIVE_TESTS=1 ./scripts/verify.sh --keep
======================= 92 passed, 22 skipped in 50.88s ========================
verify.sh: PASSED (stack left running: --keep)
(the extra skip is test_ui_not_duplicate.py: no Playwright on the host)

catalogue-service unit (loop-test image + pytest/httpx): 106 passed in 2.88s
tests/test_ui_*.py (playwright/python:v1.65.1-noble, playwright==1.65.1, --network host, docker CLI + socket mounted, loop-test):
43 passed, 7 skipped in 168.09s
```

## 7. Found but not fixed (out of scope)
- Dismissing from the warning shown while typing a new product, or from the bulk-import "same name" warning. The product is not saved yet, so there is no pair to dismiss; this needs a different design.
- "Similar names" dismissals.
- Stale dismissal rows (a product edited since) stay in the table forever. They are harmless and ignored, but a cleanup could delete them once the fingerprints no longer match.
- Running the UI suite overwrites the committed `loop/evidence/iter-8-*.png` screenshots when `UI_SCREENSHOT_DIR` is set. I reverted them by hand.

## 8. Blockers / decisions needed
None.

## 9. Suggestions for the next iteration
- If the user wants it, a count on the Products page's "Possible duplicates" link (using the filtered numbers, `same_name_groups` + `exact_groups`).

App running at http://localhost:8080 (login: admin / CHANGE_ME, local only) — see the final step below.
