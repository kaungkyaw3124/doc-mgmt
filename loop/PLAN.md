# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **28** |
| Goal | **Four small fixes found in earlier reports.** Each is small; do all four, test each |
| Attempt limit | 5 per failing check |
| Status | READY FOR CLAUDE CODE |

## 1. Superuser locked out with ~100 groups (iteration 24 report §7)
`/verify` returns every group id in `X-Group-Ids` for a superuser; past nginx's `proxy_buffer_size` (4k) every API call returns 500 (`upstream sent too big header`).
- Fix: **superusers do not need the list** (they see everything) — but document-service uses their group ids to validate the **group a record is created in** (`group_for_create`) and the Move target. So keep the data, fix the transport: raise `proxy_buffer_size` / `proxy_buffers` / `proxy_busy_buffers_size` for the auth subrequest and the API locations (e.g. 32k) in `infra/nginx/nginx.conf.template`. 
- Test: unit/integration on `loop-test` with ≥ 150 groups → a superuser call through nginx returns 200 (test creates the groups, then cleans up).

## 2. Cart lost on reload (iteration 25 report §7)
`cartItems` lives only in memory. Keep it in `sessionStorage` (per tab; try/catch every access, works without storage). Restore on load, update on add/remove/qty/empty; clear on logout and when the logged-in user changes (never show user A's cart to user B). Drop cart lines whose product no longer exists in the list (after products load).
- Test (Playwright, `loop-test`): add 3 products, reload → badge and cart still 3; log out/in as another user → empty.

## 3. 500 on a line item whose product has no price (iteration 19 report)
`_process_items` in `services/document-service/app/routers/documents.py`: a catalogue product with no price (bulk-imported products have none) makes document create/update crash with 500. Required behaviour: the line must carry a unit price from the request; if neither the request nor the product has one, return **422** with a clear message naming the line ("Line 3: enter a unit price"). If the request has a price, use it. Never 500.
- Test: API tests for the three cases.

## 4. `scripts/loop-test-down.sh`
`docker compose down -v` does not remove the bind-mounted `infra/data-loop-test/` folders, so the throwaway stack keeps old data (iteration 24 report). Add a script that tears down ONLY the `loop-test` project and deletes ONLY `infra/data-loop-test/` (using a throwaway alpine container for files owned by the container user). It must refuse to run if the compose project name is not `loop-test` and must never touch `infra/data/`. Update `loop/LOOP.md` to use it. Test: run it twice; second run is a no-op; `infra/data/` untouched (check it exists before and after).

## Tests / evidence
Existing suites and `./scripts/verify.sh` pass on the `loop-test` stack only. Report one A-row per fix (A1–A4) plus A5 "no regressions; data safety".

## Found but not fixed (record in the report)
Anything else you notice; do not widen scope.
