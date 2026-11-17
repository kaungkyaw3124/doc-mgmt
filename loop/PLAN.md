# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **19** (part 1 of 2 — iteration 20 is queued in `loop/queue/`) |
| Goal | **Department data separation.** Documents, products, customers and projects each belong to ONE group (department: Operations, CityMart, …). A user sees and can touch only their own group's records; anything a user creates is automatically owned by their group. Superusers see all |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## What the user wants
They run different departments in one app. "The operation user group can see only operations data — if an operation user
creates a document, product, customer or project, only operation group users can see it. If another group like CityMart
does the same, it stays in its own group." Records are put in the creator's group **automatically**. A superuser can later
**move** records from one group to another (that is iteration 20).

## Decisions already made (do not ask the user again)
- **Owner group on every top-level record**: add a nullable, indexed `group_id` (UUID, no FK across services; the value is a `groups.id` from auth-service) to
  `documents`, `customers`, `projects` (document-service) and `products` (catalogue-service). Child rows inherit through their parent
  (line items, audit entries, sub-item links, directors) — no column on them.
- **Shared, NOT separated**: companies (supplier identities, logos, seals, MDs) and product categories stay global for all groups.
- **Groups already exist** in auth-service (`Group`, `UserGroup` with `is_group_admin`, roles per group). Reuse them; do not create a new concept.
- **Visibility rule** (applied on top of the existing role/service access and project-access rules — keep those working unchanged):
  - superuser → all groups (and rows with no group);
  - everyone else → only rows whose `group_id` is one of the groups they belong to (active groups only);
  - a non-superuser in **no** active group sees nothing and cannot create (clear 403 message: "You are not in any group — ask an admin");
  - rows with `group_id IS NULL` ("unassigned") are visible to superusers only.
- **Auto-assign on create**: a new document/customer/project/product gets the creator's group. If the user belongs to exactly one group → that one.
  If they belong to several → the request must carry a `group_id` they belong to (the forms show a "Group" select for multi-group users); missing → 422.
  Superusers creating records must pick a group in the form (select, required; default = their first group if any).
  Bulk imports (products, categories excluded) assign every created row to the importer's group the same way.
- **No cross-group links**: a document's customer and project must be in the same group as the document; reject otherwise (422, clear message). Line items keep a
  **snapshot** of the product name/description/price they were created with — verify this; a document must still render and export correctly for its group even if the product belongs to
  another group or was deleted. If line items depend on live product rows, fix that first and report it. The product picker in a document only offers products of the document's group.
- **Identity travels through the gateway**: auth-service's verify (auth_request) response also returns the user's active group ids and superuser flag; nginx forwards them as headers
  (e.g. `X-Group-Ids: id1,id2` and `X-Is-Superuser: 1|0`, empty/overwritten on every route so a client can never spoof them — same pattern as `X-Allowed-Projects`). Services trust them only with the existing
  `X-Internal-Secret`. Service-to-service calls (catalogue → document-service `visible-product-ids`, search indexing) must forward the scope or be explicitly superuser-scoped; fail **closed** if scope is missing.
- **One choke point per service**: a single helper (e.g. `app/core/group_scope.py`) that returns the caller's scope and filters a SQLAlchemy query; every endpoint uses it. No endpoint may build its own filter.
  Outside-scope records return **404** (not 403) for get/update/delete/restore so existence is not revealed.
- **Search** (Meilisearch via search-service) must filter by group server-side: index `group_id` on documents/products, make `/search` apply the filter from the caller's headers (superuser = none), and re-index existing data after the migration. This is the easiest leak to miss — test it.
- **Everything that reads these tables is in scope**: list, get, update, delete (soft/permanent/trash/restore), duplicate-check, duplicates review page, autocomplete/suggestions, document PDF/XLSX export,
  document number sequences stay per company (unchanged), file/image URL endpoints (check group before returning a presigned URL), audit log (per-document and any global view), dashboards/counts, bulk import, and the DEV-ONLY
  Delete ALL endpoints (they delete only rows in the caller's scope; superuser = all).
- **Existing data**: the migration only adds the columns (NULL = unassigned, visible to superusers only). Provide the way to assign it:
  `POST /admin/assign-unassigned` style superuser-only endpoint (or one per service) taking `{group_id}` that sets `group_id` on all rows where it is NULL (reports counts per entity), plus `scripts/assign-unassigned.sh <group-name>`
  that calls it inside the stack and then triggers the search re-index. Nothing is auto-assigned silently. The UI for this is in iteration 20; for now API + script + docs (`docs/GROUPS.md`).
- **The VM has an uncommitted, VM-only edit to `services/auth-service/app/core/authz.py`** (group project pools, non-superusers with no grants see no projects) plus scripts `seed-data-setup/scripts/seed_city_mart.py`, `migrate_projects_to_group.py`, `test_group_isolation.py`. They are NOT in git and you cannot see them.
  Build the new rule from scratch on top of what is in this repo. Keep `get_user_allowed_project_ids` behaviour as it is in the repo (do not copy the VM's variant). Mention in the report that the VM must discard its local `authz.py` edit before pulling.
- Backups (iteration 16) need no change: they copy whole databases, `group_id` comes along.

## Tasks
1. **Inspect** (write findings in the report): every endpoint of document-service and catalogue-service touching the four tables; how auth-service verify builds headers; nginx header handling; how search indexing/filters work; how line items store product data; where Alembic runs for each service.
2. **Auth + gateway**: verify returns group ids/superuser flag; nginx forwards and always overwrites `X-Group-Ids` / `X-Is-Superuser`; add the check to the gateway auth tests.
3. **Schema**: Alembic migrations adding `group_id` (nullable, indexed) to the four tables (single clean head per service; backfill nothing). SQLite-compatible for tests.
4. **Scope helper + enforcement** in both services; auto-assign on create; same-group validation for document ↔ customer/project; scoped bulk import, duplicate checks, exports, file URLs, audit, delete-all.
5. **Search**: index + filter by group; re-index command used by `assign-unassigned`.
6. **Forms (minimal UI)**: a required "Group" select only when the user may choose (superuser or multi-group); show the group name as a small column/label in the four lists (read-only). No move UI yet.
7. **Assign-unassigned** endpoint + script + `docs/GROUPS.md` (how groups/ownership work, how to assign old data, how to add a department).
8. Tests and evidence (below).

## Tests (throwaway `loop-test` stack ONLY — never the user's stack/volumes)
Build one reusable fixture: groups **Operations** and **CityMart**, a user in each (editor role with access to documents/products), one user in both, one in no group, one superuser, plus company/categories shared.
- **Isolation matrix** (parametrised over document, product, customer, project and over list / get / update / delete / restore / trash / duplicate-check / duplicates / export PDF+XLSX / file-url / audit / search):
  an Operations user creates records; a CityMart user gets empty lists, **404** on direct id access, no hits in search, no suggestions; the reverse; superuser sees both; the no-group user sees nothing and gets 403 on create.
- Auto-assign: created records carry the creator's group; multi-group user without `group_id` → 422, with a foreign `group_id` → 403/422; superuser must pass `group_id`.
- No cross-group links: a document cannot use another group's customer/project (422); the product picker/list is scoped; a document whose product was deleted or is in another group still renders and exports correctly.
- Header spoofing: a client sending its own `X-Group-Ids` / `X-Is-Superuser` through nginx gets them overwritten; direct service calls without `X-Internal-Secret` are refused (existing behaviour still holds).
- Fail closed: if the scope headers are missing on a request that should have them, the service returns nothing / 403, never everything.
- Migration: upgrade on a DB with existing rows leaves them NULL → only the superuser sees them; `assign-unassigned` assigns all (counts correct), re-index makes them searchable by the right group; running it twice is harmless.
- Shared data: companies and categories visible to both groups; quotation numbering unaffected.
- Playwright `tests/test_ui_group_isolation.py`: two users in two groups, each creates a product + customer + project + document in the UI → each sees only their own in all four lists and in Search; the Group select appears for the superuser and is required.
- All existing tests still pass (update fixtures where tests assumed "everyone sees everything"); `./scripts/verify.sh` passes.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Isolation matrix passes (4 entities × all operations incl. search/export/file URLs) | test output |
| A2 | Auto-assign + validation rules | tests |
| A3 | Gateway headers cannot be spoofed; fail-closed | tests |
| A4 | Migration + assign-unassigned + re-index | tests + script output in the report |
| A5 | UI: Group select + group label; two-user flow | Playwright + `loop/evidence/iter-19-*.png` (ops view, citymart view, superuser create form) |
| A6 | No regressions | verify.sh, all service suites, UI suites; report states which stack tests ran on |
| A7 | Endpoint inventory | report lists every endpoint touched and confirms none is left unscoped (grep proof that no router query on the four models bypasses the helper) |

## Out of scope — record under "Found but not fixed"
- Moving records between groups and the "Unassigned data" admin card (iteration 20).
- Postgres row-level security as a second layer.
- Per-group companies/categories.
