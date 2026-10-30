# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **9** |
| Goal | **A)** Browsers always load the newest UI after an update (no hard refresh). **B)** Saved customers get an optional **Organization** separate from the Name |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## Problem (seen by the user)
After pulling iteration 7 and rebuilding, the New document form showed the new
"Saved customer / Enter manually" radios (new `index.html`), but clicking
"Enter manually" did nothing: the browser was still running the **old cached
`js/app.js`**, so the manual fields never appeared until a hard refresh
(Ctrl+Shift+R).

Cause: `infra/nginx/nginx.conf.template` serves `web/` (`location / { try_files … }`,
volume `../web:/usr/share/nginx/html:ro`) with **no `Cache-Control` header**,
so browsers apply heuristic caching from `Last-Modified` and can reuse stale
files for hours or days.

## Tasks
1. In `nginx.conf.template`, make the static UI files revalidate on every load:
   - `index.html` and everything under `/js/` (plus any `.css` served from `web/`):
     `Cache-Control: no-cache` (the browser may keep a copy but must check
     with the server first; nginx's ETag / Last-Modified make that a cheap 304).
   - **nginx `add_header` gotcha**: `add_header` in a location replaces inherited
     ones, and `location /` already does `include /etc/nginx/security-headers.conf;`.
     Every location that serves these files must still send **all** the security
     headers plus the new `Cache-Control`. Check this with `curl -sI`.
   - Don't change caching for `/api/*` or presigned MinIO downloads.
2. Cache-bust the script URL too, so users with an **already cached** old
   `app.js` pick up the new one on their next normal page load:
   `<script src="/js/app.js?v=…">` in `web/index.html`. Use the smallest approach
   that works without a build step. One option: nginx `sub_filter` replacing a
   placeholder with a value that changes when the files change. A manually bumped
   `?v=` constant is not acceptable, because people will forget to bump it. Explain the choice in the report.
3. `scripts/verify.sh`: after `docker compose up -d --build`, run
   `docker compose restart nginx` (iteration 8 report §7, fix (a)). Add a
   `.dockerignore` to each service ignoring `__pycache__`, `.pytest_cache`,
   `tests/` (fix (c)).
4. Add the same restart step to `docs/operations.md` / deployment docs, with the
   server update commands:
   `git pull` → `docker compose -f infra/docker-compose.yml up -d --build` →
   `docker compose -f infra/docker-compose.yml restart nginx`.

## Part A tests
- Smoke (`tests/test_health.py` or a new `tests/test_static_caching.py`), through the gateway:
  1. `GET /` and `GET /js/app.js` → `Cache-Control` contains `no-cache`, plus an
     `ETag` or `Last-Modified`.
  2. The same responses still carry every security header the current config sends.
     Read the list from `security-headers.conf` instead of hard-coding it.
  3. Conditional GET with the returned `ETag` → 304.
  4. The `index.html` script tag has a version query that changes when `app.js`
     changes. Prove it by touching or modifying `web/js/app.js` in the test stack,
     reloading, and checking that the value changed. Restore the file afterwards.
  5. `GET /api/...` responses are unchanged (no new `Cache-Control` added by this change).
- UI (Playwright): load the app, change `web/js/app.js` on disk (e.g. append a
  harmless `window.__cacheProbe = 2;`), reload normally (**not** a hard reload),
  check that `window.__cacheProbe === 2`, then restore the file.

## Part B — Organization on saved customers
The user wants this in the **New / Edit customer** form (today one field,
"Name / organization", is used as both):
- **Name** (required) and **Organization** (optional) are separate fields.
- If Organization is empty, the **Name is used as the Organization** everywhere
  it prints (the quotation END USER block). This is the same rule manual customers
  already follow (iteration 7).

Backend (`services/document-service`):
1. `Customer.organization` String(255), nullable. Add migration
   `0006_customer_organization.py`, guarded/idempotent like 0004/0005. Existing
   rows keep NULL, so they print their name as before.
2. `CustomerCreate` / `CustomerUpdate` / `CustomerOut` gain `organization`. Trim it;
   blank → NULL. **Store NULL, not a copy of the name**, so a later name change
   still flows through.
3. Exports: in `_gather_export_data` (documents.py ~line 756), add `organization`
   to the saved-customer dict. PDF (`quotation.html` ~line 213) and XLSX
   (`export_quotation.py` ~line 177) both print `organization or name`, for
   saved and manual customers alike. Check the PDF template: it currently prints
   `customer.organization`, which may be blank for saved customers that lack the
   key. Fix it so the fallback works in the PDF too.
4. Search: if documents or customers are indexed with the customer name, include
   the organization too (`app/core/search_client.py`).

Frontend (`web/index.html` ~line 1107, `web/js/app.js` customer form ~line 1587):
1. Rename the label "Name / organization" → **Name**. Add an **Organization**
   field (optional, placeholder "Same as name if empty") next to it. Keep the
   current grid layout.
2. Create and edit send / fill `organization`.
3. Customers table: add an **Organization** column showing `organization`, or a
   muted name when it's empty. Sorting and filtering keep working; the filter
   should match the organization too.
4. The doc form's saved-customer picker can stay as is (by name).

Part B tests (`services/document-service/tests/test_customer_organization.py`):
1. Create with an organization → stored; without → NULL; blank → NULL.
2. PDF and XLSX END USER: with org → Name row = name, Organization row = org;
   without org → both = name; manual customer behaviour unchanged.
3. Migration runs twice safely; an old customer row (no org) still exports name for both.
4. Smoke: create a customer with an org through the gateway, a quotation for it,
   export the PDF, check both texts, then clean up.
5. UI (Playwright): create a customer with only a Name, and one with Name + Organization;
   the table shows both correctly.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Headers | `curl -sI http://localhost:8080/` and `/js/app.js` pasted in the report: `Cache-Control: no-cache` plus all security headers |
| A2 | Normal reload picks up new JS | Playwright test passes |
| A3 | No regressions | all service unit suites pass |
| A4 | Smoke passes | `./scripts/verify.sh` exits 0 |
| A5 | Part B tests | `pytest services/document-service/tests/test_customer_organization.py -v` |
| A6 | Part B proof | `loop/evidence/iter-9-customer-form.png` (new form), `iter-9-pdf-org.png` (END USER block with a separate org) |

## Open question for the user (don't block on it)
Manual customer form: the user listed only Customer Name, Organization and
Address. Phone and Email stay for now, until the user decides.
