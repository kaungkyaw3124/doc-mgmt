# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **21** |
| Goal | Make simultaneous use safe: no duplicate numbers, no silent overwrites, no double saves, no half-saved records, no stale screens |
| Attempt limit | 5 per failing check |
| Status | READY FOR CLAUDE CODE |

## What the user wants
"Some requests may get lost halfway… if requests come from two computers at the same time, the job may get confused." (Brainstormed; the user said "ok you have fixes".)
No job queue, no Redis cache for now (decided: not needed at this size; revisit only if a load test shows a problem).

## Decisions already made
1. **Unique numbers from the database**
   - Product SKU (`_generate_sku` in `catalogue-service/app/routers/products.py` reads the max number then inserts — a race) and the document/quotation number (company short name + date + sequence in `document-service/app/routers/documents.py`) must come from a **locked counter** (a small counters table updated with `SELECT … FOR UPDATE` / atomic `UPDATE … RETURNING`, per prefix / per company+day), never "read max then insert".
   - Add **unique constraints** in the database (SKU; document number) as a second line of defence; on a rare conflict retry once with a fresh number, otherwise a clear 409.
   - Existing data: build each counter from the current maximum in the migration so numbering continues without gaps or reuse. Alembic migrations in both services, single head each.
2. **Version check on edit (optimistic locking)** for documents, products, customers, projects and companies: each row has a `version` integer (starts at 1, +1 on every update). The UI sends the version it loaded; if it is stale the API returns **409** with a clear message and the UI shows "Someone else changed this. Reload to see their changes" with a Reload button (the user's typed edits stay in the form so nothing is lost). No silent last-write-wins. Requests without a version keep working for now (so scripts/bulk import don't break) but the web UI always sends it.
3. **One save per click + idempotency**: Save/Create/Import buttons are disabled while the request runs (and show progress). Create endpoints accept an optional `Idempotency-Key` header (UI generates one per form open); a repeated key within a short window returns the first result instead of creating a second record (small `idempotency_keys` table with expiry, shared helper in each service).
4. **All-or-nothing saves**: a document with its line items, sub-items and audit entry is saved in a **single database transaction**; product create + file upload: if the file upload or index update fails, roll back / clean up (delete the orphan object; keep the DB consistent) and return a clear error. Search-index updates happen only after the commit succeeds; a failed index update is logged and retried by a re-index command instead of failing the save.
5. **No stale data**: API responses get `Cache-Control: no-store` (nginx + services), lists are re-fetched after each save/delete, and a refresh after a 409 shows current data.
6. Group scoping from iteration 19 must keep working everywhere these changes touch (counters per company are global; owner group unchanged).

## Tests (throwaway `loop-test` stack ONLY)
- **Race tests with real parallelism** (threads/asyncio against a real Postgres, not SQLite): 30 simultaneous product creates in one category → 30 distinct SKUs with no gaps; 30 simultaneous documents for one company+day → 30 distinct numbers; simultaneous create + bulk import; counters continue correctly after a restart and after the migration on existing data.
- **Version check**: two clients load the same record; A saves; B saves the old version → 409 and the data is A's; B reloads and saves → OK. For all five entities. A request without a version still works.
- **Idempotency**: the same `Idempotency-Key` twice (and ten times in parallel) → exactly one record; a different key → a second record; expired key → treated as new.
- **Atomicity**: force a failure after the document header insert (inject an error in the line-item step) → nothing is stored; force a MinIO failure during product create → no product row and no orphan object; search-index failure → the save succeeds and the item shows up after the re-index command.
- **UI (Playwright)**: double-click Save → one record; two browser contexts edit the same company → the second gets the "Someone else changed this" message with Reload and keeps their typed text; buttons are disabled during save.
- Headers: `Cache-Control: no-store` on API responses (not on static files/PDF downloads that need it otherwise — check).
- All existing tests pass; `./scripts/verify.sh` passes; evidence screenshots in `loop/evidence/iter-21-*.png`.

## Acceptance checks
A1 race tests (numbers unique, gapless) · A2 version conflict for 5 entities · A3 idempotency · A4 atomicity/cleanup · A5 UI double-click + conflict banner · A6 migrations (counters seeded from existing max, single head per service) · A7 no regressions, group isolation tests still pass, report says which stack tests ran on.

## Out of scope
Live editing locks (Google-Docs style), Redis/job queue, load testing (later, only if needed).
