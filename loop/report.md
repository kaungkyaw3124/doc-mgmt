# report.md — Iteration 21

## 1. Summary
- **Status**: DONE
- **Goal**: Make simultaneous use safe: no duplicate numbers, no silent overwrites, no double saves, no half-saved records, no stale screens
- **Ponytail skill loaded**: yes
- **Branch / commit**: `dev` @ (this commit, "loop iter 21: …")
- **One-paragraph summary**:
  - **Unique numbers.** Document numbers and SKUs now come from a locked `counters` row (`UPDATE … RETURNING`). The bump happens inside the create's own transaction, so a create that rolls back gives its number back: 30 parallel creates get 1..30 with no gaps. A missing counter is seeded from the current maximum. A generated number that someone already typed by hand is retried once, past the highest number in use (using a savepoint). The unique constraints on SKU and doc number are guarded in the migrations. The New Document form no longer sends its previewed number back as a "manual" number (that would have bypassed the counter).
  - **Version check.** Documents, products, customers, projects and companies have `version` (starts at 1). It is enforced twice:
    - an explicit check of the `version` the client sends → 409 "Someone else changed this. Reload to see their changes.";
    - SQLAlchemy `version_id_col` (`UPDATE … WHERE version = <loaded>`), so two saves racing on the same version cannot both win, even without a version in the body. A `StaleDataError` handler maps it to the same 409.
    - Requests without `version` still work.
  - **One save per click.** The five Save buttons are disabled and say "Saving…" while the request runs. Create endpoints accept an `Idempotency-Key` (24 h). The key is claimed first in the transaction, so 10 parallel repeats block on it and then replay the first record. The UI keeps one key per form until its create succeeds.
  - **All or nothing.**
    - A document, its items and its `created`/`edited` audit entry commit together (the audit write used to be a separate session after the commit).
    - Bulk import uploads a row's file before committing that row. A failed upload drops the row; a failed commit deletes the uploaded object.
    - `POST /products/{id}/file` returns a clear 502 when storage is down.
    - In the UI, a new product whose file fails to upload is deleted again ("Nothing was saved").
    - Indexing runs after the commit and never fails a save. `scripts/reindex.sh` rebuilds both indexes.
  - **No stale screens.** Every `/api/*` answer has `Cache-Control: no-store`. Lists are re-fetched after save and delete. Reload after a 409 re-opens the form with current data.

## 2. Baseline (before any change)
```
$ COMPOSE_PROJECT_NAME=loop-test COMPOSE_OVERRIDE=infra/docker-compose.loop-test.yml GATEWAY=http://localhost:18080 ALLOW_DESTRUCTIVE_TESTS=1 ./scripts/verify.sh --keep
======================= 68 passed, 15 skipped in 25.99s ========================
verify.sh: PASSED (stack left running: --keep)
```

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | race tests (unique, gapless) | PASS | Gateway, real Postgres, threads (`tests/test_concurrency.py`): 30 parallel product creates in a fresh category → SKUs exactly 1..30. 30 parallel documents for one company today → exactly /001../030. 10 creates running alongside a 10-row bulk import → 1..20. A hand-typed `X-0002` makes the next generated SKU `X-0003`. **Destructive, loop-test only:** after `docker compose restart catalogue-service document-service` the next SKU and doc number continue (0002, /002). Units: seeding from the existing max (PRD-0041 → 0042, DOC…/007 → 008), preview takes nothing, typed-number retry for both services. |
| A2 | version conflict ×5 | PASS | Gateway `test_stale_edit_is_refused_for_every_entity[documents/products/customers/projects/companies]`: A saves; B saves the old version → 409 with the message, and the stored value is A's; B saves with the reloaded version → 200; without a version → 200. `test_parallel_saves_of_one_version_only_one_wins[documents/products]`: 10 parallel PATCHes of the same version → `[200] + [409]*9`. Units cover the same for all five, plus "an items-only document edit still bumps the version". |
| A3 | idempotency | PASS | Gateway: one key sent 10× in parallel → ten 201s, one id, one row (products, documents, customers); a different key → a second record. Units: repeat → same id; different key → new; key aged past 24 h → new record (all five entities). |
| A4 | atomicity / cleanup | PASS | Unit: an injected failure in the line-item INSERT (after the header INSERT, same flush) → 500. Afterwards there is no document, no items and no audit entry, and the next number is /002 (the number came back). Bulk import with MinIO down → that row is skipped and never created; the next row gets PRD-0001. Commit failure after upload → `delete_file` is called for the uploaded key and no product row remains. File upload with storage down → 502 "Nothing was changed". Index failure (`_client` raising) → create still returns 201. **Gateway (destructive):** Meilisearch stopped → create returns 201 → Meilisearch started → product not found by id → `scripts/reindex.sh` → found. UI: product file upload stubbed to 502 → "Nothing was saved", and the product is not in `/products`. |
| A5 | UI double-click + conflict banner | PASS | `tests/test_ui_concurrency.py` 3 passed (Playwright 1.65.1, loop-test). Two contexts edit one company: B gets the banner with **Reload**, B's typed address stays, and the DB has A's value. Reload shows A's value; B saves → OK. A held POST shows the button disabled with "Saving…"; a double click sends 1 request. `requestSubmit()` ×2 sends 2 POSTs with the same Idempotency-Key and creates 1 customer. Screenshots: `loop/evidence/iter-21-conflict-banner.png`, `iter-21-saving-disabled.png`, `iter-21-product-file-failed.png`. |
| A6 | migrations | PASS | document-service `0010_concurrency`, catalogue `0002_concurrency`, both guarded and idempotent; a single head each (unit `test_single_alembic_head`). **Destructive gateway test on a scratch Postgres DB per service:** build the old schema (no counters, idempotency keys or versions; a NULL `documents.version`), insert existing numbers, stamp the old head, `upgrade head` twice. Counters come out exactly `{"sku:COM": 12, "sku:PRD": 3}` and `{"doc:SS-20261101/": 9, "doc:AB-20261102/": 1}`, and every row has version 1. The loop-test DB itself migrated in place on startup (`alembic_version = 0010_concurrency`). |
| A7 | no regressions / isolation / stack | PASS | verify.sh **87 passed / 16 skipped** (68 + 19 new; the extra skip is the new UI test on the host). All 19 `test_group_isolation` tests pass. Units: document-service **317 passed** (303 + 14), catalogue **101 passed** (93 + 8). UI suite **35 passed / 6 skipped** (32 + 3; the usual short-session skips). Headers: `/api/*` returns `no-store`; `/` and `/js/app.js` still return `no-cache`. |

**Data safety:** every test ran on the throwaway `loop-test` stack (ports 18080/18443, `infra/data-loop-test/`), on SQLite/TestClient, or on scratch databases (`m21_*`) created and dropped inside loop-test's Postgres. The user's stack was only rebuilt afterwards (`up -d --build`, volumes kept), after a backup. See §10.

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `services/{document,catalogue}-service/app/core/concurrency.py` | added (identical copies) | `next_number`/`peek_number` (locked counter), `check_version`, `claim_idempotency_key`/`remember` |
| `…/app/models.py` (both) | `version` + `version_id_col` on 5 models; `Counter`, `IdempotencyKey` | A1–A3 |
| `…/alembic/versions/0010_concurrency.py`, `0002_concurrency.py` | added | tables, versions (NULL→1), unique guard, counters seeded from the max |
| `…/app/schemas.py` (both) | `version` in Out (5) and Update (5) | A2 |
| `…/app/main.py` (both) | `StaleDataError` → 409 | lost race = same answer as a stale version |
| `document-service/app/routers/documents.py` | counter numbers + preview; create = one transaction (items, audit, key, counter) with a savepoint retry; edit: version check, `updated_at` bump, audit in the same transaction | A1–A4 |
| `document-service/app/routers/{customers,projects,companies}.py` | Idempotency-Key on create, version check on edit | A2/A3 |
| `catalogue-service/app/routers/products.py` | `_add_with_next_sku` (counter + savepoint retry); idempotent create; version check; bulk import uploads before commit with cleanup; file upload 502 / orphan cleanup | A1–A4 |
| `catalogue-service/app/core/storage.py` | `delete_file` | orphan cleanup |
| `scripts/reindex.sh` | added | rebuild both search indexes from the DB |
| `infra/nginx/cors.conf` | `Cache-Control: no-store`; `Idempotency-Key` allowed in CORS preflight | A5/headers (the file is included by every `/api/*` location and nothing else) |
| `web/js/app.js` | helpers (`setBusy`, `idempotencyHeader`, `showSaveError` with Reload, scrolled into view); 5 forms send `version` and `Idempotency-Key`; doc-number preview not sent back; new product deleted if its file fails; company version refreshed after its own image steps; unread response bodies drained | A5, A4 |
| `docs/api.md` | "Simultaneous use" section | docs |
| tests: `services/*/tests/test_concurrency.py`, `tests/test_concurrency.py`, `tests/test_ui_concurrency.py` | added | A1–A7 |
| `services/*/tests/test_group_scope.py` | SQLite fixture: SQLAlchemy's documented pysqlite SAVEPOINT fix; catalogue `test_alembic_single_head_and_0001_migration` loads 0001 by name instead of asserting it is the only file | the retry uses a savepoint; a second revision now exists by design (the single head is asserted in the new test) |
| `tests/test_static_caching.py` | `test_api_responses_get_no_new_cache_control` → `test_api_responses_are_never_cached` (asserts `no-store`) | iteration 9 pinned "/api left alone"; this plan explicitly reverses that. Not a weakened test: it now asserts the new rule. |

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | catalogue unit `test_idempotency_key_replays_and_expires` | repeat → 409 instead of 201 | the duplicate-name check ran before the key was looked at | claim the key first (products and documents) |
| 2 | units: typed-number retry (both services) | `PendingRollbackError` after the savepoint | `begin_nested()` flushes pending objects *before* its SAVEPOINT, so the clash happened outside it | `add` inside the savepoint block |
| 3 | gateway: every document create | 409 "doc_number already exists" | the audit row was inserted before the document (no ORM relationship orders that FK; SQLite didn't enforce it). Found in the Postgres log. | add the audit entry after the document flush, before the single commit |
| 3 | `test_static_caching::test_api_responses_get_no_new_cache_control` | `cache-control` present | old rule (see §4) | test updated to the new rule |
| 4 | gateway: `stale_edit[/products]`, restart test, reindex test | 409 / connection reset / found before reindex | test bugs: fixed product names duplicated an earlier run's; the keep-alive connection was reset by the restart; Meilisearch matched an earlier run's "reindex-…" product on the shared word | unique values, fresh connections, compare by id |
| 5 | UI `test_ui_delete_all::test_buttons_hidden_in_production` | `networkidle` timeout | with `no-store`, Chromium keeps a response open until its body is read; the refresh-failure path never read it (also logout and three upload successes). Real effect: each unread body holds a connection until GC (~6 per host). | drain those bodies; this existing test is the regression test (it fails without the drain) |

## 6. Final verification output
```
$ COMPOSE_PROJECT_NAME=loop-test COMPOSE_OVERRIDE=infra/docker-compose.loop-test.yml GATEWAY=http://localhost:18080 ALLOW_DESTRUCTIVE_TESTS=1 ./scripts/verify.sh --keep
tests/test_concurrency.py::test_parallel_product_creates_get_distinct_gapless_skus PASSED
tests/test_concurrency.py::test_parallel_documents_get_distinct_gapless_numbers PASSED
tests/test_concurrency.py::test_simultaneous_create_and_bulk_import_share_one_sequence PASSED
…
tests/test_concurrency.py::test_counters_continue_after_a_restart PASSED
tests/test_concurrency.py::test_failed_index_update_still_saves_and_reindex_brings_it_back PASSED
======================= 87 passed, 16 skipped in 45.05s ========================
verify.sh: PASSED (stack left running: --keep)

document-service (loop-test image + requirements-dev + poppler-utils): python -m pytest tests → 317 passed in 216.71s
catalogue-service (loop-test image + requirements-dev):               python -m pytest tests → 101 passed in 2.97s
tests/test_ui_*.py (mcr.microsoft.com/playwright/python:v1.65.1-noble, --network host, loop-test, destructive allowed)
  → 35 passed, 6 skipped in 117.61s
```
auth-service, search-service and backup-service were not changed and were not re-run on their own (their paths are covered by verify.sh).

## 7. Found but not fixed (out of scope)
- **`idempotency_keys` is never purged.** An expired key is only deleted when the same key is reused. The table grows by one small row per UI create. Fix: delete rows older than 24 h in the nightly backup cron or on startup.
- **Idempotency keys are not bound to a user.** The replay goes through the caller's group scope (`get_or_404`), and keys are 128-bit random. Low risk; binding the key to `X-Username` would close it.
- **Bulk import holds the SKU counter lock during each row's file upload.** Creates in the same category wait for that upload. Fine at this size; if imports with large files become common, upload to a temp key first.
- **No idempotency on `POST /products/bulk-import`** (the plan's "Import" button is disabled while running instead). Rows that were already committed stay if the import fails halfway (it was already per-row).
- **Other writes bump the version too** (trash/restore, group move, status change, logo/seal/file uploads). An edit form left open across one of those gets the conflict banner. That is correct but may surprise.
- **The services don't set `Cache-Control` themselves.** Only nginx does, but all API traffic goes through it (services aren't published).
- Still open from iteration 19/20: the 500 on a line item for a product with no price; "Delete ALL companies" open to any editor; product move doesn't re-check duplicates in the target group.

## 8. Blockers / decisions needed
None.

## 9. Suggestions for the next iteration
- Purge expired idempotency keys (one `DELETE` in the backup cron).
- Add the two services' unit suites to `verify.sh` (they are run by hand in the service images today).

## 10. App running
(filled in after the deploy — see the follow-up commit)
