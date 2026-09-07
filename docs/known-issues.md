# Code Quality Review

Findings from reading the full codebase, ranked roughly by severity within
each category. "Severity" is a judgment call (Critical/High/Medium/Low)
based on blast radius and likelihood, not a formal CVSS score. Every item
below is grounded in code that was actually read — none are speculative.

## Security risks

### 1. Downstream services fully trust gateway headers with no independent check — Medium
**RESOLVED** — see `docs/SECURITY_HARDENING_LOG.md` Task 4: each of
`document-service`, `catalogue-service`, `search-service` now runs an
ASGI middleware (`app/core/gateway_auth.py`) rejecting any request that
doesn't carry a shared `X-Internal-Secret`, which only Nginx
(`infra/nginx/nginx.conf.template`, rendered via `envsubst` at container
start) and the services' own direct inter-service calls
(`app/core/document_client.py`/`catalogue_client.py`) are configured
with. A direct connection to a service's port (even from within the
Docker network, e.g. Task 3's `expose:`-only reachability) can no longer
forge `X-Allowed-Projects`/etc. and gain access — it's rejected before
reaching any router. This did NOT require touching every router
individually (contrary to the original Effort estimate below) — one
middleware registration per service was enough.
**Where** (historical): `document-service`, `catalogue-service`, `search-service` — every
router reading `X-Allowed-Projects`/`X-Access-Level`/`X-Has-Audit-Log`/
`X-Has-Category-Access`/`X-Username`.
**Why it matters**: this is a deliberate architecture decision (see
[decisions.md](decisions.md#7-single-gateway-unconditionally-trusted-headers-downstream)),
not a bug — but it means the *entire* authorization model collapses if any
of these services ever becomes reachable other than through Nginx (e.g. a
future host port publish, a debugging shortcut, a misconfigured k8s
Service if this is ever migrated off Compose). There is no defense in
depth: no shared secret between Nginx and the services, no mTLS, no
service-to-service allowlist.
**Recommended fix**: at minimum, document this trust boundary loudly in
deployment docs (done — see [architecture.md](architecture.md)); consider a
shared internal-only secret header Nginx injects and each service verifies,
so a direct connection to a service's port can't forge these headers.
**Effort**: Medium (one header + one check per service, but touches every
router).

### 2. Insecure defaults ship for every secret, with inconsistent startup warnings — Medium
**RESOLVED** — see `docs/SECURITY_HARDENING_LOG.md` Task 2 (commit
`59d09ce`): all four services now fail to start in production
(`ENVIRONMENT=production`) if any of these secrets are missing or still
set to a known/generic-placeholder default; development still only warns.
**Where** (historical): `services/auth-service/app/main.py:23-35` warns on default
`JWT_SECRET`/`SEED_ADMIN_PASSWORD`; **no equivalent warning exists** for
`MEILI_MASTER_KEY` or `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`, which default
to `local_dev_master_key_change_me` and `minioadmin`/`minioadmin` across
all four services (`.env.example` files).
**Why it matters**: a deployment that only reads auth-service's startup
logs could miss that MinIO and Meilisearch are still using publicly-known
default credentials — both are reachable if their ports (`9000`, `9001`,
`7700`) are exposed to the host or beyond.
**Recommended fix**: add the same startup-warning pattern to
catalogue-service, document-service, and search-service for their
Meilisearch/MinIO settings.
**Effort**: Low.

### 3. Postgres/MinIO/Meilisearch ports published to the host, acknowledged but not closed — Medium
**RESOLVED** — see `docs/SECURITY_HARDENING_LOG.md` Task 3: `ports:`
mappings removed for `postgres`, `minio`, and `meilisearch` (now
`expose:`-only); browser-facing MinIO presigned-download URLs are proxied
through Nginx instead (`/documents/`, `/products/` locations in
`infra/nginx/nginx.conf`), and `docs/operations.md` documents a temporary-
tunnel pattern for ad-hoc admin access.
**Where** (historical): `infra/docker-compose.yml:13-14, 26-27, 63-64` — comments say
"exposed for now... remove later."
**Why it matters**: on a host with any inbound network exposure, this
offers a direct, RBAC-bypassing path to raw data (see finding #1) and to
credential-guessing against MinIO/Postgres.
**Recommended fix**: remove the `ports:` mappings for `postgres`, `minio`,
and `meilisearch` in any non-local environment; access them via `docker
compose exec` or an SSH tunnel instead.
**Effort**: Low (config-only), but verify nothing external actually
depends on direct access first.

### 4. No rate limiting on `/login` (or anywhere else) — Medium
**Where**: `infra/nginx/nginx.conf` has no `limit_req`; `auth-service`'s
`/login` handler has no attempt counter or lockout.
**Why it matters**: unlimited brute-force attempts against any known
username, including the seeded `admin` account.
**Recommended fix**: add an Nginx `limit_req_zone` on `/api/auth/login`, and/or
an application-level failed-attempt lockout.
**Effort**: Low (Nginx-only fix) to Medium (app-level lockout with state).

### 5. Inconsistent Content-Type trust on file uploads — Low/Medium
**Where**: `document-service/app/routers/companies.py:46-52` derives the
stored `Content-Type` strictly from the sanitized file extension
(hardened); `document-service/app/routers/documents.py:544` and
`catalogue-service/app/routers/products.py:421` instead trust the
client-supplied `file.content_type` directly for document and product file
uploads, and neither uses `force_download` on their presigned URLs (unlike
company logo/seal SVGs, which explicitly force a download rather than
inline rendering).
**Why it matters**: a document or product file uploaded with a spoofed
`Content-Type` (e.g. an `.html` or `.svg` file served as
`text/html`/`image/svg+xml`) could render inline in the app's own browser
origin when opened via its presigned URL — a stored-content risk the
company-logo code path already closed but the document/product paths did
not.
**Recommended fix**: apply the same extension-derived Content-Type + or
`force_download` treatment used in `companies.py` to document and product
file uploads.
**Effort**: Low.

## Correctness / data-integrity risks

### 6. No optimistic concurrency control on document edits — Medium
**Where**: `services/document-service/app/routers/documents.py:447-503
update_document` overwrites `customer_id`/`project_id`/`company_id`/
`currency`/`terms_and_conditions`/`items` unconditionally.
**Why it matters**: two users editing the same document concurrently will
silently last-write-wins — the second save discards the first's changes
with no warning, no conflict error, and no merge. The `version` column
exists precisely for this purpose but is never used (see #7).
**Recommended fix**: check-and-increment `Document.version` on update
(`412 Precondition Failed` on mismatch), or a `updated_at`-based
compare-and-swap.
**Effort**: Medium — touches the update endpoint and the frontend's save
flow (needs to handle the new conflict response).

### 7. `Document.version` column is dead schema — Low
**Where**: `services/document-service/app/models.py:78` defines it,
`schemas.py` returns it, but a repository-wide search found **zero** other
references — nothing increments it, no history is kept.
**Why it matters**: misleading to a reader of the schema or API response —
implies a versioning feature that does not exist (see
[workflows.md](workflows.md#versioning-workflow)). Low severity because
it's inert, not actively wrong.
**Recommended fix**: either implement real versioning (bump on update,
optionally keep a history table) or remove the column and field to stop
implying a feature that isn't there.
**Effort**: Low (remove) or High (implement properly, see #6).

### 8. Cross-service references have no cleanup on delete — Medium
**Where**: deleting a Project in document-service leaves
`GroupProjectAccess`/`UserProjectAccess`/`RoleProjectAccess` rows in
auth-service pointing at a UUID that no longer exists (no cascade, no
cleanup job); deleting a Product in catalogue-service leaves
`line_items.product_id` dangling (though this is already handled
gracefully at read time via `ProductNotFoundError` catches in
`document-service/app/core/catalogue_client.py`).
**Why it matters**: an admin can "grant" or leave stale access to a
project/product that no longer exists, with no error and no visibility
into the inconsistency; over time this accumulates silent cruft in the
`auth` database.
**Recommended fix**: either an async cleanup job triggered by a delete
webhook/event, or accept it as a known limitation of the loose-coupling
design and periodically audit/prune (see
[decisions.md](decisions.md#1-database-per-service-no-cross-database-foreign-keys)).
**Effort**: Medium (requires either new inter-service messaging or a
scheduled job — neither exists today).

### 9. `product_sub_items.sub_product_id` has no `ondelete` clause — Low
**Where**: `services/catalogue-service/app/models.py:59`.
**Why it matters**: attempting to hard-delete a Product that is currently
referenced as another product's sub-item will raise a raw
`IntegrityError`/500 instead of the friendly `400` pattern used elsewhere
in the codebase (e.g. `companies.py:212-220`) — `products.py`'s own
`delete_product` handler has no `try/except IntegrityError` guard at all.
**Recommended fix**: add the same `IntegrityError` → `400` handling used
for companies/customers/projects, with a message pointing at the parent
product(s).
**Effort**: Low.

### 10. Alembic migrations are not applied at runtime — High (deployment risk)
**Where**: `document-service/app/main.py:16-20`,
`catalogue-service/app/main.py:18-22` — both use `Base.metadata.create_all()`
instead of running Alembic; `auth-service` has no migrations at all.
**Why it matters**: this works for a brand-new empty database but **cannot
apply schema changes to an already-running deployment** — `create_all()`
only creates missing tables, it never alters existing ones. Any future
model change (a new column, a new constraint) will silently fail to appear
in an already-deployed database, with no error at startup, until someone
notices a column doesn't exist. This is the single largest deployment-
correctness risk in the repository. See
[decisions.md](decisions.md#4-alembic-migrations-exist-but-are-deliberately-not-run).
**Recommended fix**: wire `alembic upgrade head` into each service's
container startup (or a dedicated migration-runner step in Compose/CI)
before `uvicorn` starts, and write the migrations that are currently
missing for tables added since `0001_initial` (`categories`,
`product_sub_items`, all of `auth`'s RBAC tables — none of which have any
migration at all today, only `create_all()`).
**Effort**: High — requires reconstructing the accumulated schema history
into real migrations and testing the upgrade path against existing data.

## Performance bottlenecks / N+1 queries

### 11. Document catalogue export makes slow, sequential per-product HTTP calls — High (acknowledged in code)
**Where**: `document-service/app/routers/documents.py:727-816
export_document_catalogue`, calling
`catalogue_client.get_product_sub_items`/`get_product_file_bytes`/
`get_product_download_bundle_bytes` once per product, sequentially, in a
loop.
**Why it matters**: explicitly flagged in the code itself — individual
calls "have been observed taking 30+ seconds each," which is why
`infra/nginx/nginx.conf:94` bumps `proxy_read_timeout` to `300s` just for
`/api/documents`. This is a real, observed production issue, not a
hypothetical one.
**Recommended fix**: parallelize the per-product fetches (e.g.
`httpx.AsyncClient` with `asyncio.gather`, which would also require
converting these handlers to `async def`), or investigate and fix the
root cause of the 30-second-per-call latency in catalogue-service's own
MinIO/boto3 path (the code comments suggest this hasn't been root-caused
yet).
**Effort**: Medium (parallelize) to High (root-cause the underlying
per-call latency).

### 12. N+1 query patterns in auth-service admin endpoints — Low/Medium
**Where**: `list_group_members` (`admin.py:688-702`), `get_user_roles`
(`admin.py:209-236`), and `whoami`/`/admin/me`
(`admin.py:1214-1234`) each loop over a list of IDs doing one `db.query(...)
.first()` per iteration instead of a single batched `IN (...)` query — the
same pattern catalogue-service's `products.py` explicitly avoids elsewhere
via `_sub_item_counts_map()` (one query for however many products, with a
comment noting exactly why: "instead of one query per product").
**Why it matters**: group/role member lists and `/admin/me` (called on
every login) scale linearly with membership size — fine for small teams,
a real cost at larger scale.
**Recommended fix**: batch these with `.filter(User.id.in_(ids))` /
`.filter(Role.id.in_(ids))`, following the existing
`_sub_item_counts_map` pattern already used elsewhere in the codebase.
**Effort**: Low.

### 13. No pagination anywhere — Medium (grows over time)
**Where**: every list endpoint (`GET /documents`, `/products`, `/customers`,
`/admin/users`, etc.) returns its full result set; only `search-service`'s
`/search` has a `limit`.
**Why it matters**: response size and query cost grow unbounded with data
volume; the frontend also renders every row into the DOM at once with no
virtualization.
**Recommended fix**: add `limit`/`offset` (or cursor) query params to the
highest-volume list endpoints (`documents`, `products`) and corresponding
frontend paging UI.
**Effort**: Medium (API + frontend both need to change).

## Code smells / maintainability

### 14. Large "god files" — Low/Medium
**Where**: `auth-service/app/routers/admin.py` (1,234 lines — every
user/group/role/access-grant endpoint in one file) and
`document-service/app/routers/documents.py` (816 lines — CRUD + status +
file + three export types + audit log in one file).
**Why it matters**: harder to navigate and review; unrelated concerns
(e.g. "project-access grant plumbing" vs. "pending user approval") are
interleaved in the same file.
**Recommended fix**: split `admin.py` into `users.py`, `groups.py`,
`roles.py`, `project_access.py` sharing one `APIRouter` prefix; split
`documents.py`'s export endpoints into their own `exports.py` router.
**Effort**: Medium (mechanical, but touches a lot of import paths).

### 15. Duplicated helper functions across services/files — Low
**Where**: `_parse_allowed_projects` is reimplemented nearly identically
in `document-service/app/routers/documents.py` and
`document-service/app/routers/projects.py`
(the latter's comment even says "kept local here to avoid a cross-router
import for one small function"); `_require_edit_access` is copy-pasted
across `documents.py`, `companies.py`, `customers.py`, `projects.py`;
`catalogue-service/app/core/document_client.py` and
`search-service/app/core/document_client.py` are **byte-for-byte
identical files**.
**Why it matters**: a future fix to one copy (e.g. the fail-closed logic in
`document_client.py`) is easy to apply inconsistently across the others —
this has already happened once (the `revoke_user_project_access` scoping
gap fixed in commit `bdf21bf` was exactly this kind of drift between two
near-identical code paths).
**Recommended fix**: within a single service, extract shared helpers into
`app/core/`. Across services, this is a harder call — a shared internal
Python package would reintroduce coupling between otherwise-independent
deployables; at minimum, add a comment cross-referencing the sibling copy
so a fix to one prompts a check of the other.
**Effort**: Low (within-service) / architectural decision needed
(cross-service).

### 16. No automated tests anywhere — High (process risk, not a runtime bug)
**Where**: repository-wide — no test files, no test framework in any
`requirements.txt`.
**Why it matters**: every fix so far (see the two hardening commits in
[decisions.md](decisions.md#8-iterative-delivery-ship-fast-then-dedicated-hardening-passes))
was found by manual review, not caught by CI. Regressions in permission
logic in particular (the most security-sensitive part of this system) have
no automated guardrail.
**Recommended fix**: start with the authorization logic in
`auth-service/app/core/authz.py` and the RBAC admin endpoints — highest
risk, most complex branching, and the part that has already needed
multiple correctness fixes.
**Effort**: High (starting a test suite from zero).

### 17. `SUPPLIER_*` settings in document-service appear unused/superseded — Low
**Where**: `services/document-service/app/core/config.py:19-26` declares
`supplier_name`, `supplier_position`, `supplier_address`,
`supplier_contact`, `support_email`, `support_phone`, but a
repository-wide search found no router or export code reading these
settings — exports pull supplier/company info from the per-document
`company_id` → `companies` table instead.
**Why it matters**: dead configuration that could mislead an operator into
thinking they need to set these env vars for exports to show correct
supplier info.
**Recommended fix**: remove the unused settings, or confirm and document
an intended fallback-when-no-company-chosen use case if one actually
exists.
**Effort**: Low.

## Not found (explicitly checked, absent)

To be precise about what was and wasn't investigated: no SQL injection was
found (SQLAlchemy ORM query building throughout, no raw string-interpolated
SQL); no CSRF risk (bearer-token-in-header auth, not cookies); no evidence
of a JWT algorithm-confusion vulnerability (`algorithms=[settings.jwt_algorithm]`
is passed explicitly to `jwt.decode`, not derived from the token itself).
