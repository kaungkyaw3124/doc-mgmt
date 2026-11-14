# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **15** (revised: now covers 5 pages) |
| Goal | A one-click **"Delete ALL …"** button on each of the Products, Documents, Customers, Projects and Companies pages, for the user's development/testing stage. Temporary, and must be trivial to remove later |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## What the user wants
The user is still developing and wants to wipe data between tests without clicking Delete
on every row. Their words: "just 1 click", "I want to test and delete, after that I'm gonna
remove that" (the buttons themselves). Later request: "in delete all product, we also need
to add delete all in documents, customers, projects, company."
Every row already has its own Delete button; do not change those.

## Decisions already made (do not ask the user again)
- **One click, no confirm dialog, no typed word.** The user explicitly asked for this.
- **Permanent delete** (not the recycle bin), including rows already in the recycle bin.
- **Dev-only guard (the one safety rail)**: the button is shown and the endpoint answers only
  when `ENVIRONMENT` is not `production` (both services have `environment: str = "development"`
  in `app/core/config.py`; compose passes `ENVIRONMENT: ${ENVIRONMENT:-development}`).
  In production every endpoint returns 403 and the UI hides the buttons. No other friction.
- Same permission rule as the existing single delete on each entity; view-only user gets 403.
- Button labels: **"Delete ALL products (dev)"**, **"Delete ALL documents (dev)"**,
  **"Delete ALL customers (dev)"**, **"Delete ALL projects (dev)"**, **"Delete ALL companies (dev)"**.
  Red danger style, next to each page's "+ New …" button
  (`web/index.html`: `toggle-product-form` ~690, `toggle-doc-form` ~617, `toggle-customer-form` ~740,
  `toggle-project-form` ~784, `toggle-company-form` ~829).
- Each click calls its endpoint, refreshes that list, shows a banner "Deleted N <things>".
- **Related data is never a blocker** (this is a wipe, not a safe delete):
  - Documents: delete rows; `line_items` and `audit_log_entries` go by FK cascade (verify, add explicit deletes if the DB/SQLite does not cascade); remove each document from the search index (`search_client.remove_document_from_index`) and best-effort delete its stored files (PDF/attachments/MinIO) — never fail because a file is missing.
  - Customers / Projects: first set `documents.customer_id` / `documents.project_id` to NULL, then delete. Documents are kept.
  - Companies: first set `documents.company_id` and `documents.director_id` to NULL, delete the company's directors (CASCADE exists on `company_directors.company_id`; verify), best-effort delete logo/seal/letterhead files from MinIO, then delete companies. Documents are kept.
  - Products: delete all rows including soft-deleted, and sub-item links (check FK on `product_sub_items`), remove each from Meilisearch, best-effort delete files. Categories are kept.
  - Do NOT cross-delete: "Delete ALL customers" does not delete documents, and so on.
- Documents/Customers/Projects/Companies live in **document-service**, Products in **catalogue-service**: two services, two guards, same pattern.

## Tasks
1. **Endpoints**: `DELETE /products` (catalogue-service, `routers/products.py`),
   `DELETE /documents`, `DELETE /customers`, `DELETE /projects`, `DELETE /companies`
   (document-service routers). Collection routes with no id: define them above the
   `/{id}` routes so they don't clash with `/trash`, `/duplicate-check`, etc. Each returns
   403 in production or for view-only access, else `{"deleted": <count>}`.
2. **Gateway/auth**: check `infra/nginx/nginx.conf.template` and the auth-service RBAC
   mapping so `DELETE` on these five collection paths is allowed for the same users who can
   delete a single item today, and is not blocked by a method/path rule.
3. **Buttons + handlers** in `web/index.html` and `web/js/app.js`, hidden in production.
   Expose the environment to the UI with the smallest existing mechanism (a field on an
   endpoint the UI already calls, or a tiny `GET /config`-style response); do not hard-code a hostname check.
4. **Easy to remove later**: wrap everything in marked blocks —
   `# DEV-ONLY: delete-all BEGIN/END` in Python, `<!-- DEV-ONLY: delete-all -->` and
   `// DEV-ONLY: delete-all BEGIN/END` in web files. Keep each endpoint in the smallest block.
   The report must list every touched file and line range and the exact removal steps.
5. Do not change existing delete/trash/restore behaviour.

## Tests
- Catalogue (`services/catalogue-service/tests/`) and document-service (`services/document-service/tests/`), following existing patterns. For each entity:
  - several rows incl. related data → `DELETE` → 200 with correct count; list endpoints are empty afterwards; related rows handled as above (line items/audit gone; documents kept with NULL references for customers/projects/companies; directors gone; categories kept);
  - `ENVIRONMENT=production` → 403, nothing deleted; view-only → 403, nothing deleted;
  - a MinIO/search error does not fail the request; empty table → `{"deleted": 0}`.
- Playwright `tests/test_ui_delete_all.py`: for each of the 5 pages create rows through the API, open the page, click the button once (fail if a `confirm` dialog fires), the list shows the empty state and the banner says "Deleted N …". One test checks buttons are absent when production (if the stack can't be switched, test the UI logic with a stubbed response and say so).
- All existing tests still pass.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Catalogue + document-service unit tests pass | new tests + both whole suites |
| A2 | UI test passes | `pytest tests/test_ui_delete_all.py -v` |
| A3 | No regressions | `./scripts/verify.sh` passes |
| A4 | Proof | `loop/evidence/iter-15-<page>-before.png` and `-after.png` for products, documents, customers, projects, companies |
| A5 | Removal steps | the report lists every DEV-ONLY block and the exact removal steps |

## Out of scope — record under "Found but not fixed" if you notice them
- Delete-all for categories, users, or the recycle bins of other types.
- Changing the per-row delete buttons.
