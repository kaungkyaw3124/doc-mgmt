# Modules

A tour of every service and the frontend, file by file, with what each file
is responsible for. Line counts are from the actual repository (`wc -l`) at
the time this was written.

## auth-service (`services/auth-service/`, ~1,850 LOC)

| File | Role |
|---|---|
| `app/main.py` | FastAPI app, startup: creates tables, warns on insecure defaults, seeds the first superuser if `users` is empty (`app/main.py:37-54`) |
| `app/models.py` | SQLAlchemy models: `User`, `Group`, `UserGroup`, `Role`, `RoleAccess`, `RoleProjectAccess`, `GroupProjectAccess`, `UserProjectAccess`, `UserRole` |
| `app/core/config.py` | `pydantic-settings` — DB URL, seed admin creds, JWT secret/algorithm/expiry |
| `app/core/db.py` | SQLAlchemy engine/session factory, `get_db()` FastAPI dependency |
| `app/core/deps.py` | `get_current_user` (decodes JWT, loads/validates the user), `require_superuser` |
| `app/core/jwt_utils.py` | `create_access_token` / `decode_access_token` (PyJWT, HS256) |
| `app/core/security.py` | bcrypt password hash/verify (passlib) |
| `app/core/authz.py` | The authorization engine — `user_has_service_access`, `get_user_access_level`, `get_user_allowed_project_ids`, `can_manage_group`, `is_group_admin_of`, `is_member_of` |
| `app/routers/auth.py` | Public/self-service: `/login`, `/register`, `/groups-public`, `/verify` (internal — Nginx's `auth_request` target), `/users` (superuser bare-account creation) |
| `app/routers/admin.py` (1,234 lines — the largest file in the repo) | Everything under `/admin`: pending-user approval, full user CRUD, groups, group membership, roles, role→service access grants, group project pools, per-user project grants, role assignment, `/admin/me` |

## catalogue-service (`services/catalogue-service/`, ~1,060 LOC)

| File | Role |
|---|---|
| `app/main.py` | FastAPI app, startup: creates tables, ensures the MinIO bucket exists, declares `category` as a Meilisearch filterable attribute |
| `app/models.py` | `Product`, `Category`, `ProductSubItem` |
| `app/schemas.py` | Pydantic request/response models |
| `app/core/config.py` | DB URL, document-service URL, Meilisearch/MinIO settings |
| `app/core/db.py` | Same pattern as auth-service |
| `app/core/document_client.py` | HTTP client to document-service's `/documents/visible-product-ids` — resolves which products a project-restricted caller may see; **fails closed** (empty set) if document-service is unreachable |
| `app/core/search_client.py` | Meilisearch index/remove for the `products` index |
| `app/core/storage.py` | Two boto3 S3 clients (internal vs. public MinIO endpoint — see [workflows.md](workflows.md#storage-strategy)), upload/presign/download helpers |
| `app/routers/categories.py` | Category CRUD; renaming a category cascades to every product carrying the old name string |
| `app/routers/products.py` (620 lines) | Product CRUD, SKU auto-generation, soft delete/restore, file upload/download, **bulk import** (Excel + zip/rar archive), sub-item management, sub-item zip bundling |

## document-service (`services/document-service/`, ~2,550 LOC — the largest service)

| File | Role |
|---|---|
| `app/main.py` | FastAPI app, startup: creates tables (Alembic present but unused — see [decisions.md](decisions.md)), ensures MinIO bucket, declares Meilisearch filterable attributes |
| `app/models.py` | `Project`, `Company`, `Customer`, `Document`, `LineItem`, `AuditLogEntry` |
| `app/schemas.py` | Pydantic request/response models |
| `app/core/config.py` | DB URL, catalogue-service URL, Meilisearch/MinIO settings, default supplier profile fields |
| `app/core/db.py` | Same pattern as the other services |
| `app/core/audit.py` | `log_action()` — writes an `AuditLogEntry` on its **own independent DB session** so it can never corrupt the caller's pending transaction; swallows all exceptions |
| `app/core/catalogue_client.py` | HTTP client to catalogue-service: `get_product`, `get_product_sub_items` (fails open → `[]`), `get_product_file_bytes` / `get_product_download_bundle_bytes` (fail open → `None`, generous timeouts up to 90s) |
| `app/core/export_pdf.py` | Renders `quotation.html` via Jinja2 (autoescaped) + WeasyPrint → PDF |
| `app/core/export_quotation.py` | Builds the XLSX quotation via openpyxl, including `_safe_str()` formula-injection guarding |
| `app/core/search_client.py` | Meilisearch index/remove for the `documents` index |
| `app/core/storage.py` | Same dual-client MinIO pattern as catalogue-service, plus `force_download` support for presigned URLs |
| `alembic/`, `alembic.ini` | Migration scaffolding — **exists but is not run automatically**; `main.py` uses `Base.metadata.create_all()` instead (see [decisions.md](decisions.md)) |
| `app/templates/quotation.html` | Jinja2/CSS template for the PDF export (paged-media header/footer, running elements) |
| `app/routers/documents.py` (816 lines — 2nd largest file) | Document CRUD, status transitions, soft delete/restore, file upload, three export endpoints, audit log, `visible-product-ids` (used by catalogue-service and search-service) |
| `app/routers/customers.py` | Customer CRUD + soft delete/restore |
| `app/routers/companies.py` | Company CRUD, logo/seal upload with image standardization (Pillow), soft delete/restore |
| `app/routers/projects.py` | Project CRUD, soft delete/restore, project→documents listing |

## search-service (`services/search-service/`, ~156 LOC — the smallest service)

| File | Role |
|---|---|
| `app/main.py` | Minimal FastAPI app, one router |
| `app/core/config.py` | Meilisearch + document-service URLs |
| `app/core/document_client.py` | Identical logic to catalogue-service's client of the same name (visible-product-ids, fail-closed) |
| `app/routers/search.py` | The single `GET /search` endpoint — queries the `documents` and/or `products` Meilisearch indices, escapes filter values, applies project-visibility filtering |

This service holds **no database of its own** — it is a stateless proxy in
front of Meilisearch plus one HTTP call to document-service.

## Frontend (`web/index.html`, ~4,880 lines — one file)

There is no build step, package manager, or framework — plain HTML, inline
`<style>`, and one `<script>` block. Structural breakdown:

- **Styling** (`web/index.html:9-459`) — CSS custom properties for a
  warm "paper and rust-stamp" theme, table/card/modal/skeleton-loader
  styles, all hand-written.
- **Login/register screen** (`:460-495`) — two forms toggled by JS, no
  page navigation.
- **App shell** (`:497-959`) — a sidebar with `.nav-item[data-view]`
  entries and matching `.view` panels for **Documents, Products,
  Customers, Projects, Company, Admin, Search**; the Admin nav item is
  hidden by default and shown only if `/admin/me` says the user is a
  superuser or a group admin (`:1657-1670`).
- **Modals** (`:960-1468`) — audit log, edit user, edit role, and one
  create/edit modal plus a recycle-bin modal per resource (documents,
  products, customers, projects, companies), plus the shopping-cart modal
  and category management modals.
- **JavaScript** (`:1469-4877`) — no modules, everything in global scope:
  - `apiFetch()` (`:1543-1562`) — the single fetch wrapper: attaches the
    Bearer token, auto-logs-out on `401`, formats FastAPI/Pydantic `422`
    validation errors into readable text.
  - Per-list **stale-response guarding** via sequence counters
    (`loadSeq`, `startLoad`, `isStaleLoad`, `:1479-1486`) so a slow older
    request can't overwrite a table with stale data after a faster newer
    one already rendered — added in commit `bdf21bf` after this exact bug
    was observed in the group members/roles admin panels.
  - One `load*()` + `render*Table()` pair per resource (documents,
    products, customers, projects, companies, categories, trash lists,
    admin users/groups/roles).
  - A client-side, non-persisted **shopping cart** for products
    (`addToCart`/`renderCartModal`/`:3041-3137`) that can be imported as
    line items into a new document.
  - The **admin/RBAC panel** logic (`:4117-4813`) mirrors auth-service's
    admin API almost 1:1 — group/role selection, member/role tables,
    per-role service-access checkboxes with a view/edit selector for
    `documents`, project-access checkboxes for both groups (pools) and
    individual users.

### Design patterns catalogue

| Pattern | Where |
|---|---|
| **API Gateway** | `infra/nginx/nginx.conf` — single entry point, per-route service tagging, `auth_request` |
| **Dependency Injection** | FastAPI `Depends(get_db)`, `Depends(get_current_user)`, `Depends(require_superuser)` throughout every router |
| **Adapter / HTTP client wrapper** | `catalogue_client.py`, `document_client.py` (×2) — typed exceptions (`ProductNotFoundError`, `CatalogueServiceUnavailableError`), explicit fail-open vs. fail-closed policy per call |
| **Soft delete / recycle bin** | `is_deleted` boolean + `/trash`, `/{id}/trash`, `/{id}/restore`, hard `DELETE` — applied uniformly to `Product`, `Document`, `Project`, `Company`, `Customer` |
| **Policy / centralized authorization** | `auth-service`'s `authz.py`, evaluated once per request via `/verify`, not duplicated per downstream service |
| **Strategy (informal)** | `_extract_archive_entries()` picks zip vs. rar extraction by file extension (`catalogue-service/app/routers/products.py:106-114`) |
| **Data Transfer Object** | Pydantic schemas (`schemas.py`) separate wire format from ORM models |

Patterns **not found** anywhere in the codebase: Factory, Observer/event
system, message Queue, Cache layer, formal Repository interfaces (routers
call `db.query()` directly — there is no repository abstraction between
router and ORM).
