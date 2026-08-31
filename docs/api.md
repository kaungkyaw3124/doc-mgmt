# API Reference

All routes below are reached by the browser as `/api/...` through the Nginx
gateway (`infra/nginx/nginx.conf`), which rewrites the path and strips the
`/api` prefix before proxying to the owning service. The "Auth" column
describes what Nginx/`auth_request` enforces *before* the service is called;
"Controller" is the router function; there is no separate service or
repository layer in this codebase — **the router function talks to the ORM
directly** (see [modules.md](modules.md)), so "Service/Repository" is folded
into one "DB interaction" column. `db` always means a SQLAlchemy `Session`
via the `get_db` dependency in that service's `app/core/db.py`.

Every JSON error response follows FastAPI's default shape: `{"detail":
"..."}`, or for `422` validation errors, a Pydantic list of `{loc, msg,
type}` objects.

---

## auth-service

Base path inside the service: none (auth) / `/admin` (admin). Gateway
mapping: `infra/nginx/nginx.conf:34-71`.

### Public (no token)

| Endpoint | Method | Request | Response | Controller |
|---|---|---|---|---|
| `/api/auth/groups-public` | GET | — | `[{id, name}]` — every group's id/name | `app/routers/auth.py:55-62 list_groups_public` |
| `/api/auth/register` | POST | `{username, password, requested_group_id?}` | `201 {username, message}` | `app/routers/auth.py:65-91 register` |
| `/api/auth/login` | POST | `{username, password}` | `200 {access_token, token_type}` / `401` bad creds / `403` unapproved or disabled | `app/routers/auth.py:94-110 login` |

**Validation/business rules:** `register` rejects a duplicate `username`
(`409`) or a nonexistent `requested_group_id` (`400`); the account is
created with `is_approved=False` and `is_superuser=False` — self-registration
can never grant superuser. `login` checks password via bcrypt, then
`is_approved`, then `is_active`, in that order, each with a distinct message.

### Internal (called by Nginx, not the browser directly)

| Endpoint | Method | Request | Response | Controller |
|---|---|---|---|---|
| `/verify` | GET | `Authorization: Bearer <jwt>`, `X-Service: <name>` header | `200` + `X-Allowed-Projects`, `X-Username`, `X-Access-Level` (if `X-Service` given), `X-Has-Audit-Log`, `X-Has-Category-Access` response headers / `401` bad token / `403` no service access | `app/routers/auth.py:113-171 verify` |

This is Nginx's `auth_request` target (`infra/nginx/nginx.conf:24-31`) — see
[architecture.md](architecture.md#request-flow). It is reachable at
`http://auth-service:8000/verify` on the internal network but has no
`/api/auth/verify` gateway mapping, so it is not directly callable by the
browser through Nginx's public port.

### Bearer-token required, self-authorizing (proxied without `auth_request`)

Nginx forwards only the raw `Authorization` header for these; each handler
decodes the JWT and checks permissions itself.

| Endpoint | Method | Auth | Request | Response | Controller |
|---|---|---|---|---|---|
| `/api/auth/users` | POST | superuser (`require_superuser`) | `{username, password, is_superuser?}` | `201 {username, is_superuser}` / `409` duplicate | `app/routers/auth.py:174-198 create_user` |

### `/api/admin/*` — all require `get_current_user`; extra scoping noted per row

**Pending user approval**

| Endpoint | Method | Who | Request/Response |
|---|---|---|---|
| `GET /admin/users/pending` | GET | superuser: all; group admin: only requests for a group they administer; else `[]` | → `[{id, username, created_at, requested_group_id}]` |
| `POST /admin/users/{user_id}/approve` | POST | superuser or admin of the requested group | approves + auto-joins that group as a regular member → `{username, is_approved, added_to_group}` |
| `POST /admin/users/{user_id}/deny` | POST | same scoping as approve | `204`; **hard-deletes** the unapproved account; `400` if already approved |
| `POST /admin/users/{user_id}/reject` | POST | same scoping, functionally identical to `/deny` | `204`; hard-deletes |

**User management** (`_can_manage_users`: superuser, or group-admin of ≥1 group)

| Endpoint | Method | Notes |
|---|---|---|
| `GET /admin/users` | GET | superuser sees everyone; group admin sees only members of groups they administer |
| `GET /admin/users/{id}/roles` | GET | 403 unless caller is superuser or shares an administered group with the target |
| `PATCH /admin/users/{id}` | PATCH | `{username?, password?}` — never touches `is_superuser`; 403 if target is a superuser and caller isn't; `409` on duplicate username |
| `DELETE /admin/users/{id}` | DELETE | `204`; blocked (`403`) on superuser targets or self; cascades group/role memberships |
| `PATCH /admin/users/{id}/active` | PATCH | `{is_active}` — suspend/unsuspend; blocked on superuser targets (unless caller is superuser) and on self |

**Groups**

| Endpoint | Method | Who |
|---|---|---|
| `POST /admin/groups` | POST | superuser only; `{name}` → `201`; `409` duplicate |
| `GET /admin/groups` | GET | anyone; superuser sees all, others see only their own memberships |
| `PATCH /admin/groups/{id}/active` | PATCH | superuser only; `{is_active}` — a disabled group's roles all stop granting access |
| `DELETE /admin/groups/{id}` | DELETE | superuser only; cascades members/roles/grants; also strips any direct `UserProjectAccess` a member held *only* via this group's pool (keeps it if another shared group still grants it) |

**Group membership** (`can_manage_group`: superuser, or admin of that specific group)

| Endpoint | Method | Notes |
|---|---|---|
| `POST /admin/groups/{id}/users` | POST | `{username, password, is_group_admin?}` — creates a brand-new, pre-approved user *and* adds them to the group in one call |
| `POST /admin/groups/{id}/members` | POST | `{username, is_group_admin?}` — adds an *existing* user |
| `DELETE /admin/groups/{id}/members/{username}` | DELETE | removes membership + any role assignments the user held via that group's roles (does not delete the account) |
| `GET /admin/groups/{id}/roles` | GET | caller must be superuser or a member |
| `GET /admin/groups/{id}/members` | GET | caller must be superuser or a member |

**Roles**

| Endpoint | Method | Notes |
|---|---|---|
| `POST /admin/groups/{id}/roles` | POST | `{name}`; `409` duplicate within group |
| `GET /admin/roles/{id}` | GET | full detail incl. `services`, `service_levels`, `assigned_users`; caller must be superuser or a group member |
| `PATCH /admin/roles/{id}/rename` | PATCH | `{name}`; `409` on collision within the group |
| `PATCH /admin/roles/{id}/active` | PATCH | `{is_active}` — disables all grants this role provides without deleting anything |
| `DELETE /admin/roles/{id}` | DELETE | cascades access grants + user assignments |

**Role service-access grants**

| Endpoint | Method | Validation |
|---|---|---|
| `POST /admin/roles/{id}/access` | POST | `{service_name, access_level?}` — `service_name` must be in `{documents, products, search, audit-log, categories}` (`422` otherwise); `access_level` must be `view`/`edit`; upsert semantics (re-POST updates the level) |
| `DELETE /admin/roles/{id}/access/{service_name}` | DELETE | `404` if the grant doesn't exist |

**Project access — group pool (superuser only)**

| Endpoint | Method | Notes |
|---|---|---|
| `POST /admin/groups/{id}/project-access` | POST | `{project_id}` — adds to the group's pool; `409` duplicate |
| `DELETE /admin/groups/{id}/project-access/{project_id}` | DELETE | also strips any member's *direct* grant that came only from this group's pool |
| `GET /admin/groups/{id}/project-access` | GET | caller must be superuser or a member |

**Project access — per user** (scoped to `_can_manage_users`, further restricted to the intersection of groups the caller administers *and* the target belongs to)

| Endpoint | Method | Notes |
|---|---|---|
| `GET /admin/users/{id}/available-projects` | GET | which projects the *caller* may grant to this target — `{all: true}` for superuser, else the union of the caller's administered groups' pools that the target shares |
| `GET /admin/users/{id}/project-access` | GET | the target's current grants |
| `POST /admin/users/{id}/project-access` | POST | `{project_id}` — group admin must prove the project is in a shared administered group's pool first (`403` otherwise) |
| `DELETE /admin/users/{id}/project-access/{project_id}` | DELETE | mirrors the POST's scoping exactly (fixed in commit `bdf21bf` to close a gap where a group admin could revoke via an *unrelated* shared group) |

**Role assignment**

| Endpoint | Method | Notes |
|---|---|---|
| `POST /admin/roles/{id}/assign` | POST | `{username}` — target must already be a member of the role's group (`400` otherwise); `409` if already assigned |
| `DELETE /admin/roles/{id}/assign/{username}` | DELETE | `404` if not assigned |

**Self-info**

| Endpoint | Method | Response |
|---|---|---|
| `GET /admin/me` | GET | `{username, is_superuser, groups: [{group_id, is_group_admin}], roles: [...], service_access: [...]}` — `service_access` only counts active roles in active groups, mirroring `authz.user_has_service_access` exactly |

---

## document-service

Gateway mapping: `infra/nginx/nginx.conf:75-127`. All routes below require a
valid token and `documents` service access (`auth_request`); write routes
additionally require `X-Access-Level != view` via each router's
`_require_edit_access` helper.

### `/documents` (`app/routers/documents.py`)

| Endpoint | Method | Request | Response | Notes |
|---|---|---|---|---|
| `POST /documents` | POST | `DocumentCreate` (see below) | `201 DocumentOut` | Validates `currency ∈ {USD, MMK}`, and that `customer_id`/`project_id`/`company_id` (if given) exist. Auto-generates `doc_number` if omitted. Resolves each line item's `product_id` via catalogue-service (`422` if neither `product_id` nor `description`+`unit_price` given; `502` if catalogue-service is unreachable). Computes `subtotal`/`tax_total`/`total`. Indexes into Meilisearch, logs `"created"` |
| `GET /documents` | GET | query: `doc_type?`, `status?` | `200 [DocumentOut]` | Filters `is_deleted=false`; project-restricted callers only see documents with `project_id IS NULL OR project_id IN (allowed)` |
| `GET /documents/visible-product-ids` | GET | header `X-Allowed-Projects` | `{all: bool, product_ids: [...]}` | **Internal** — called by catalogue-service and search-service; a product is visible if it's on a line item of a document the caller could see |
| `GET /documents/by-customer/{id}` | GET | — | `[{document_id, doc_number, doc_type, project_name}]` | "used in" popup data |
| `GET /documents/by-product/{id}` | GET | — | same shape | "used in" popup data, sorted newest first |
| `GET /documents/next-number` | GET | query: `company_id?` | `{doc_number}` | Preview only — does not reserve the number; a real race is possible but caught at creation via `IntegrityError` → `409` |
| `GET /documents/trash` | GET | — | `[DocumentOut]` | soft-deleted only |
| `PATCH /documents/{id}/trash` | PATCH | — | `DocumentOut` | soft delete |
| `PATCH /documents/{id}/restore` | PATCH | — | `DocumentOut` | undo soft delete |
| `GET /documents/{id}` | GET | — | `DocumentOut` | `403` if project-restricted and out of scope; logs `"viewed"` |
| `PATCH /documents/{id}` | PATCH | `DocumentUpdate` | `DocumentOut` | Full replace of customer/project/company/currency/terms/line items (old items deleted via cascade, new ones re-validated identically to create); `doc_type`/`doc_number` are immutable here; logs `"edited"` |
| `PATCH /documents/{id}/status` | PATCH | query: `new_status` | `DocumentOut` | `422` unless `new_status ∈ VALID_STATUSES`; no state-machine transition rules — any status can go to any other; logs `"status_changed"` |
| `POST /documents/{id}/file` | POST | multipart `file` | `{file_object_key}` | filename sanitized (`_sanitize_filename`); object key `{doc_type}/{doc_number}/{filename}`; logs `"file_uploaded"` |
| `DELETE /documents/{id}` | DELETE | — | `204` | **hard delete**; cascades line items + audit log; removes from Meilisearch |
| `GET /documents/{id}/file-url` | GET | — | `{url}` | presigned MinIO URL, 1hr expiry |
| `GET /documents/{id}/audit-log` | GET | header `X-Has-Audit-Log` must be `"true"` | `[{username, action, created_at}]` | `403` without the `audit-log` grant |
| `GET /documents/{id}/export/quotation` | GET | — | XLSX binary | openpyxl, formula-injection-safe |
| `GET /documents/{id}/export/quotation-pdf` | GET | — | PDF binary | WeasyPrint + Jinja2 (autoescaped) |
| `GET /documents/{id}/export/catalogue` | GET | — | ZIP binary | bundles every product-linked line item's catalogue file(s), positionally numbered; `404` if nothing to bundle |

`DocumentCreate`: `{doc_type, doc_number?, customer_id?, project_id?,
company_id?, currency="USD", issue_date?, due_date?, doc_metadata?,
terms_and_conditions?, items: [{product_id?, description?, remark?,
unit="Nos", quantity, unit_price?, tax_rate=0}]}`.

### `/customers` (`app/routers/customers.py`)

| Endpoint | Method | Notes |
|---|---|---|
| `POST /customers` | POST | `CustomerCreate` → `201 CustomerOut` |
| `GET /customers` | GET | query `q?` (ILIKE on name) |
| `GET /customers/trash` | GET | soft-deleted only |
| `GET /customers/{id}` | GET | `404` if missing |
| `PATCH /customers/{id}` | PATCH | partial update |
| `PATCH /customers/{id}/trash` \| `/restore` | PATCH | soft delete/restore |
| `DELETE /customers/{id}` | DELETE | hard delete; `400` (not `500`) if a document still references it |

### `/companies` (`app/routers/companies.py`)

| Endpoint | Method | Notes |
|---|---|---|
| `POST /companies` | POST | `CompanyCreate`; first company ever, or `is_primary=true`, unsets every other company's `is_primary` first |
| `GET /companies` \| `/trash` | GET | list active / soft-deleted |
| `GET /companies/{id}` | GET | — |
| `PATCH /companies/{id}` | PATCH | setting `is_primary=true` unsets all others |
| `PATCH /companies/{id}/trash` \| `/restore` | PATCH | soft delete/restore |
| `DELETE /companies/{id}` | DELETE | `400` if referenced by a document |
| `POST /companies/{id}/logo` \| `/seal` | POST | multipart `file`; resized/padded to a fixed pixel size via Pillow (400×200 logo, 300×300 seal) unless SVG; stored as `standardized.png` |
| `GET /companies/{id}/logo-url` \| `/seal-url` | GET | presigned URL; `force_download=true` for SVGs (prevents inline-rendering a stored SVG in the browser's own origin) |

### `/projects` (`app/routers/projects.py`)

| Endpoint | Method | Notes |
|---|---|---|
| `POST /projects` | POST | `ProjectCreate` |
| `GET /projects` \| `/trash` | GET | project-visibility filtered |
| `GET /projects/{id}` | GET | `403` if out of scope |
| `PATCH /projects/{id}` | PATCH | partial update |
| `PATCH /projects/{id}/trash` \| `/restore` | PATCH | soft delete/restore |
| `DELETE /projects/{id}` | DELETE | `400` if referenced by a document |
| `GET /projects/{id}/documents` | GET | documents in this project |

All write endpoints above (`companies`, `customers`, `projects`) enforce
`X-Access-Level != view` via each file's own local copy of
`_require_edit_access` (duplicated three times — see
[known-issues.md](known-issues.md)).

---

## catalogue-service

Gateway mapping: `infra/nginx/nginx.conf:129-149`. Requires `products`
service access. Category writes additionally require
`X-Has-Category-Access: true`.

### `/products` (`app/routers/products.py`)

| Endpoint | Method | Request | Response | Notes |
|---|---|---|---|---|
| `POST /products` | POST | `ProductCreate` | `201 ProductOut` | SKU auto-generated from category's `short_term` if omitted (`{PREFIX}-{seq:04d}`); indexes into Meilisearch |
| `GET /products` | GET | query `category?`, `q?` (ILIKE name) | `[ProductOut]` | `is_deleted=false`; project-restricted callers see only products referenced by a document they can see (via a call to document-service) |
| `POST /products/bulk-import` | POST | multipart `excel_file`, `catalogue_zip?` | `{created: [...], warnings: [...]}` | Excel needs a `Name` column (case-insensitive); optional zip/rar of `N.ext` files matched positionally to spreadsheet rows |
| `GET /products/trash` | GET | — | `[ProductOut]` | soft-deleted, same visibility rule |
| `PATCH /products/{id}/trash` \| `/restore` | PATCH | — | `ProductOut` | soft delete/restore; `403` if out of project scope |
| `GET /products/{id}` | GET | — | `ProductOut` | `403` if out of scope |
| `PATCH /products/{id}` | PATCH | `ProductUpdate` | `ProductOut` | re-indexes into Meilisearch |
| `DELETE /products/{id}` | DELETE | — | `204` | hard delete; **no FK guard** — see [known-issues.md](known-issues.md); removes from Meilisearch |
| `POST /products/{id}/file` | POST | multipart `file` | `{image_object_key}` | object key `{sku}/{filename}` |
| `GET /products/{id}/file-url` | GET | — | `{url}` | presigned URL |
| `GET /products/{id}/file-content` | GET | — | raw bytes | **internal** — called by document-service to build catalogue exports; also reachable via `/api/products` (project-gated there) |
| `GET /products/{id}/sub-items` | GET | — | `[SubItemOut]` | ordered by `sequence_number` |
| `POST /products/{id}/sub-items` | POST | `{product_id}` | `201 SubItemOut` | `400` if a product references itself |
| `DELETE /products/{id}/sub-items/{sub_item_id}` | DELETE | — | `204` | renumbers remaining sequence numbers contiguously |
| `GET /products/{id}/download-bundle` | GET | — | ZIP binary | every sub-item's file, renamed to its sequence number |

### `/categories` (`app/routers/categories.py`)

| Endpoint | Method | Auth | Notes |
|---|---|---|---|
| `GET /categories` | GET | any product-access user | populates the product-form dropdown |
| `POST /categories` | POST | `X-Has-Category-Access: true` | `{name, short_term, description?}`; `409` on duplicate name/short_term |
| `PATCH /categories/{id}` | PATCH | `X-Has-Category-Access: true` | renaming cascades to every product carrying the old category string |

---

## search-service

Gateway mapping: `infra/nginx/nginx.conf:151-159`. Requires `search` service
access.

| Endpoint | Method | Request | Response |
|---|---|---|---|
| `GET /search` | GET | query: `q` (required), `index="all"\|"documents"\|"products"`, `doc_type?`, `project_id?`, `category?`, `limit=20 (≤100)` | `{documents?: [...], products?: [...], documents_error?, products_error?}` |

Filter values are escaped (`_escape_filter_value`) before being embedded in a
Meilisearch filter expression to prevent filter-injection. Project-restricted
callers: document hits are filtered via a Meilisearch `filter` clause
(`project_id IN [...] OR project_id IS NULL`); product hits are fetched
over-limit (up to 5×, capped at 200) from Meilisearch and then filtered in
Python against `visible_product_ids` fetched from document-service, then
truncated back to `limit` — done this way because Meilisearch has no
knowledge of cross-service visibility.

---

## Health checks

Every service exposes `GET /health` → `{"status": "ok"}`, unauthenticated,
not proxied through any `/api/*` Nginx route (only reachable on the internal
network or by `docker compose exec`/`curl`ing the container directly).
