# report.md — Iteration 20

## 1. Summary
- **Status**: DONE
- **Goal**: Superuser can **move** documents, products, customers and projects from one group to another (one or many at a time) and sees/assigns unassigned data in the UI
- **Ponytail skill loaded**: yes
- **Branch / commit**: `dev` @ (this commit, "loop iter 20: …")
- **One-paragraph summary**:
  - **API (superuser only):** `POST /{documents|customers|projects|products}/{id}/move` and `POST /{entity}/move {ids}`, with body `{group_id, with_links=true, dry_run=false}`.
    - The target must be an active group (422 otherwise). Unknown ids give 404.
    - A move is all or nothing (one commit). A row already in the target group is a no-op.
    - `dry_run` only counts what would move; the modal uses it to show "Will move: 1 document, 1 customer, 1 project".
  - **Link rules** live in one function per service: document-service `app/core/group_move.py` and catalogue-service `products.py` `_move_products`.
    - A move works on the *connected set*: document → customer and project, customer or project → its documents, followed transitively.
    - With the box ticked, the whole set moves together. Unticked, the move is refused with 409 and a clear message whenever a link would cross groups.
    - Products follow the same rule over bundle/sub-item links. Document line items keep their snapshots, and a document move never moves products.
  - **Audit and search:** every moved row writes a `group_move user=… <entity>=<id> from=… to=…` log line. Every moved document also gets a `moved_group` entry in its audit log. Moved documents and products are re-indexed at once.
  - **UI:**
    - The four lists get a **Group** column. Superusers also get a checkbox, **Move…** on each row, a group filter (including "Unassigned") and **Move selected to group…**.
    - The move modal has a target select, the link checkbox, a live dry-run preview, and the iteration-15 tick or cross.
  - **Unassigned data card:** Admin → Groups shows the counts and an **Assign all to group…** button, which uses the iteration-19 endpoints.

## 2. Baseline (before any change)
```
$ COMPOSE_PROJECT_NAME=loop-test COMPOSE_OVERRIDE=infra/docker-compose.loop-test.yml GATEWAY=http://localhost:18080 ALLOW_DESTRUCTIVE_TESTS=1 ./scripts/verify.sh --keep
======================= 63 passed, 14 skipped in 26.58s ========================
verify.sh: PASSED (stack left running: --keep)
```

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | move API + rules | PASS | document-service `tests/test_group_move.py` **7 passed**. It covers: doc + links, dry run, refusal without the box, a lone doc, a customer/project used by docs (409, then "move together" including a second document), bulk atomic with an unknown id → 404 and nothing moved, unassigned rows, 403/422/404. Catalogue `tests/test_group_move.py` **4 passed**: single, bulk atomic, bundle with sub-items (409 / together), 403/422. Gateway `tests/test_group_move.py` **5 passed** through Nginx. |
| A2 | audit + search consistency | PASS | Unit: caplog sees `group_move user=boss document=<id> from=<ops> to=<city>` (3 lines for doc + customer + project) and `product=…`. Gateway: `GET /documents/{id}/audit-log` contains `moved_group`. Search is polled after the move: the new group finds the document/product and the old group no longer does (`test_move_document_with_links_reindexes_and_audits`, `test_move_products_single_and_bulk`). |
| A3 | UI flow | PASS | `tests/test_ui_group_move.py` 1 passed (Playwright 1.65.1, loop-test). The superuser filters Documents by Operations, clicks Move…, and the preview shows "Will move: 1 document, 1 customer, 1 project". Unticking shows the 409 message; re-ticking and Move gives the tick, and the row now shows CityMart. Bulk: two checked customers are moved back. The Operations user no longer sees the document or customer; the CityMart user does. Neither has move controls. Screenshots: `loop/evidence/iter-20-move-modal.png`, `iter-20-after-move-superuser.png`, `iter-20-ops-no-longer-sees.png`, `iter-20-citymart-sees-moved.png`. |
| A4 | unassigned card | PASS | The card's counts equal `GET /documents/unassigned-counts` + `/products/unassigned-counts`; Assign all → counts 0 (same UI test). Unit: the counts include a NULL-group customer and drop after a move. Screenshot `iter-20-unassigned-card.png`. |
| A5 | permissions | PASS | A non-superuser (also group admin of CityMart and a member of both groups) gets 403 on single and bulk moves for customers and products, and on both unassigned-counts endpoints (gateway test). Units cover documents/customers bulk and single. The UI hides the controls for non-superusers. |
| A6 | no regressions / data safety | PASS | verify.sh **68 passed / 15 skipped** (the extra skip is the new UI test, which runs in the Playwright container). Units: document-service **303 passed** (296 + 7), catalogue **93 passed** (89 + 4). UI suite **32 passed / 6 skipped** (the usual short-session skips). |

**Data safety:** every test ran on the throwaway `loop-test` stack (ports 18080/18443, `infra/data-loop-test/`) or on SQLite/TestClient. No schema change and no migration. The user's stack was only rebuilt (`up -d --build`, volumes kept), after a backup.

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `services/document-service/app/core/group_move.py` | added | connected-set move, link rules, audit log line |
| `services/document-service/app/routers/moves.py`, `app/main.py` | added; included first | the 6 move routes + `GET /documents/unassigned-counts` (must match before `/documents/{id}`) |
| `services/document-service/app/schemas.py`, `services/catalogue-service/app/schemas.py` | `MoveToGroup`, `BulkMoveToGroup` | request bodies |
| `services/catalogue-service/app/routers/products.py` | `_move_products`, `/products/move`, `/products/{id}/move`, `/products/unassigned-counts` | product moves (sub-item links) |
| `services/document-service/alembic/env.py` | `disable_existing_loggers=False` | iteration 19's "Found but not fixed": without it the new audit log lines (and every request log) vanish after startup |
| `web/index.html`, `web/js/app.js` | Group column, filter, Move… / checkbox / bulk button, move modal, Unassigned data card; name-side group tag replaced by the column | UI |
| `docs/GROUPS.md`, `docs/api.md` | move rules + endpoints | docs |
| tests: `services/*/tests/test_group_move.py`, `tests/test_group_move.py`, `tests/test_ui_group_move.py` | added | A1–A5 |

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | catalogue `test_move_one_product_reindexes_and_audits` | indexed list had 2 entries | the test's index stub also recorded the product's creation | test clears the list after setup |
| 2 | gateway `test_move_products_single_and_bulk` | `{'products': 1} != {'products': 2}` | test bug: p2 was already in the target group (no-op is correct) | expected 1 |
| 3 | `test_ui_group_move` | timeout on `.nav-item[data-view=admin]` | wrong selector (`#admin-nav-item`, view `adminpanel`) | selector fixed |

(In the first unit-test run, document-service had 11 collection errors: `pypdf` was missing because I hadn't installed `requirements-dev.txt` in the runner. That was a runner setup issue, not a code issue.)

## 6. Final verification output
```
$ COMPOSE_PROJECT_NAME=loop-test COMPOSE_OVERRIDE=infra/docker-compose.loop-test.yml GATEWAY=http://localhost:18080 ALLOW_DESTRUCTIVE_TESTS=1 ./scripts/verify.sh --keep
======================= 68 passed, 15 skipped in 25.42s ========================
verify.sh: PASSED (stack left running: --keep)

document-service: python -m pytest tests  → 303 passed in 186.84s
catalogue-service: python -m pytest tests → 93 passed in 2.84s
tests/test_ui_*.py (Playwright 1.65.1 container, loop-test, destructive allowed; runner: tests/requirements.txt + playwright==1.65.1)
  → 31 passed, 7 skipped; the 7th skip was test_ui_letterhead_header (runner lacked pymupdf/pillow),
    re-run with them installed → 1 passed. Total 32 passed, 6 skipped (the usual short-session skips).
```
search-service and auth-service were not changed and were not re-run.

## 7. Found but not fixed (out of scope)
- **Moving a product into a group that already has an identical product** creates a per-group duplicate. The duplicate check runs on create and edit, not on move. Fix: run `_raise_if_exact_duplicate` against the target group before committing, or show the duplicates in the dry run.
- **The audit "from/to" lives in the service logs, not in the database.** `audit_log_entries` has no detail column, and customers, projects and products have no audit table. Documents get a persistent `moved_group` entry; the who/from/to line is in `docker compose logs`. If a persistent cross-entity audit trail is wanted, add a small `group_moves` table (one migration per service).
- Still open from iteration 19: the 500 on a line item for a product with no price (`documents.py` `_process_items`), and the dev-only "Delete ALL companies" being open to any editor.

## 8. Blockers / decisions needed
None. Two choices I made that the plan didn't spell out:
- **Transitive link rule:** "move together" follows links transitively. Moving a document whose customer has other documents moves those documents too, and the preview shows the real counts. Anything else would leave cross-group links.
- **Product sub-items:** products use the same box for bundle/sub-item links, because iteration 19 forbids cross-group sub-items.

## 9. Suggestions for the next iteration
- Decide whether a persistent cross-entity move audit table is wanted (§7).
- Add a duplicate check on product moves (§7).

APP_RUNNING_PLACEHOLDER
