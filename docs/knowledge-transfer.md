# Knowledge Transfer — Your First Day

Written for a senior engineer joining this project tomorrow, with zero prior
context. Read this first; it links out to everything else in `docs/` for
depth.

## System mental model

This is **four small FastAPI services behind one Nginx gateway**, plus **one
static HTML file** as the frontend. There is no framework magic, no message
queue, no cache, no build pipeline. If you've used FastAPI + SQLAlchemy +
plain JS before, you already know 90% of the toolset.

Think of it as three business capabilities wearing a shared security layer:

1. **Catalogue** (catalogue-service) — what you sell.
2. **Documents** (document-service) — the paperwork you generate about what
   you sell (quotations/invoices/proposals), plus customers/projects/
   companies as supporting entities.
3. **Identity & access** (auth-service) — who can do the above, scoped by
   Group → Role → Service-grant, with an optional per-Project visibility
   restriction layered on top.

**Search** (search-service) is a thin, stateless proxy over Meilisearch that
exists mainly so the frontend has one endpoint to hit — it holds no data of
its own.

**Nginx is the only thing the browser ever talks to** (port `8080`). It does
two jobs: serve `web/index.html` as static files, and gate every `/api/*`
call through `auth-service`'s `/verify` endpoint before proxying it to the
right backend. Once a request clears `/verify`, the backend services trust
Nginx's headers completely — they do not re-check the token themselves. See
[architecture.md](architecture.md) for the diagrams.

## How requests flow

For any protected read/write: Browser → Nginx (`auth_request` to
`auth-service /verify`) → the target service → Postgres (its own database) →
back through Nginx → Browser. Full sequence diagram in
[architecture.md](architecture.md#request-flow). The one thing worth
internalizing immediately: **there is no separate service or repository
layer** — a router function *is* the whole request handler, doing
validation, the SQLAlchemy query, and the response shaping in one function.
Don't go looking for a `DocumentRepository` or `DocumentService` class; it
doesn't exist (see [modules.md](modules.md)).

## Where features belong

| If you're changing... | It lives in... |
|---|---|
| What a document/customer/project/company looks like, or how it's created/exported | `services/document-service/app/routers/*.py`, `models.py`, `schemas.py` |
| What a product/category/sub-item looks like, bulk import | `services/catalogue-service/app/routers/*.py` |
| Who can log in, register, or what a group/role/permission means | `services/auth-service/app/routers/{auth,admin}.py`, `app/core/authz.py` |
| What shows up in search results | `services/search-service/app/routers/search.py`, plus the indexing code in `document-service`/`catalogue-service`'s `app/core/search_client.py` (indexing happens where the data is written, not in search-service) |
| Any UI change | `web/index.html` — there's only one file |
| Which route requires which permission | `infra/nginx/nginx.conf` (the `$service_name` tag per location) **and** `auth-service`'s `VALID_SERVICES`/`RoleAccess` model — both need to agree |

## Where business rules live

Business rules are **not centralized** — they live directly in the router
function that enforces them, as plain `if`/`raise HTTPException` statements.
Examples worth knowing up front:

- Document status transitions have **no state machine** — any status can go
  to any other, the only check is membership in `VALID_STATUSES`
  (`documents.py:36`). Don't assume there's a workflow engine to hook into.
- Project-visibility ("who can see this project's documents/products") is
  computed **once per request** in `auth-service/app/core/authz.py` and
  handed to every downstream service as an `X-Allowed-Projects` header —
  it is not re-derived independently in each service. If you need a new
  kind of visibility rule, it almost certainly belongs there, not in
  document-service or catalogue-service.
- "Can this role do X" is always resolved via *active role in an active
  group* — a disabled group silently disables every role in it. This
  exact phrase/check appears in several places
  (`authz.py`, `admin.py`'s `whoami`) — keep it consistent if you touch
  any of them.

See [workflows.md](workflows.md) for the full business-domain writeup
(lifecycles, permission model, storage, audit logging) with diagrams.

## How to safely add a new feature

1. **Decide which service owns the new data.** If it's about what's sold →
   catalogue-service. If it's about a document/customer/project/company →
   document-service. Don't add a new cross-service foreign key — this
   codebase deliberately keeps services loosely coupled via plain
   UUID references resolved over HTTP (see
   [decisions.md](decisions.md#1-database-per-service-no-cross-database-foreign-keys)).
   Follow the existing `catalogue_client.py`/`document_client.py` pattern
   if you need to call another service.
2. **Add the SQLAlchemy model**, then **remember the migration story is
   broken**: `create_all()` runs on every startup and will create a
   brand-new table for you on a fresh database, but it will **not** add a
   new column to an existing table on an already-running deployment (see
   [known-issues.md](known-issues.md#10-alembic-migrations-are-not-applied-at-runtime--high-deployment-risk)).
   If you're touching a live environment, you currently need to hand-write
   and apply an `ALTER TABLE`, or fix the migration story first — don't
   assume your model change just takes effect.
3. **Add the router function**, following the file's existing shape: a
   `Depends(get_db)` session, explicit validation with `HTTPException`
   (not silent failure), and — if it's a write — check
   `X-Access-Level`/whatever header is relevant the same way sibling
   endpoints in that file already do.
4. **If it's a new deletable resource**, give it the same soft-delete shape
   every other resource has: `is_deleted` column, `/trash`, `/restore`,
   and a real `DELETE` guarded with `try/except IntegrityError` if other
   tables might reference it.
5. **If it needs to be searchable**, add it to the relevant
   `search_client.py`'s `index_*`/`remove_*` functions and call them from
   your router — indexing is synchronous, best-effort, and happens at the
   point of write, not via any background job.
6. **Update the frontend** by adding to `web/index.html` directly — a new
   `.view`, a `load*()`/`render*Table()` pair, and calls through
   `apiFetch()` (never raw `fetch()` for anything requiring auth). If your
   new list can be reloaded quickly (e.g. filters/refresh), use the
   `loadSeq` stale-response-guarding pattern already used everywhere else
   (`web/index.html:1479-1486`).
7. **Update `infra/nginx/nginx.conf`** if you're adding a new URL prefix —
   it needs its own `location` block with the right `$service_name` tag,
   or it won't be reachable through the gateway at all (and will bypass
   auth entirely if you accidentally give it no `auth_request`).
8. **There is no test suite to run.** Manually exercise the change through
   the running stack (`docker compose up -d`, log in via the UI or
   `curl`). Consider being the one to start a test suite for whatever
   you're touching, especially if it's authorization logic — see
   [known-issues.md](known-issues.md#16-no-automated-tests-anywhere--high-process-risk-not-a-runtime-bug).

## Common pitfalls

- **Don't assume a service you didn't touch will pick up your schema
  change.** `create_all()` is not a migration tool — see above and
  [known-issues.md](known-issues.md).
- **Don't add a new grantable permission without updating three places**:
  `VALID_SERVICES` in `auth-service/app/routers/admin.py`, the
  corresponding `X-Service`/header wiring in `infra/nginx/nginx.conf`,
  and whatever downstream router actually checks the resulting header.
  Missing any one of these silently no-ops the permission.
- **Nginx does not hot-reload its config.** If you change
  `nginx.conf` and nothing seems to take effect, you forgot `docker
  compose restart nginx` — this has bitten the original developers twice
  (see [operations.md](operations.md)).
- **Don't trust `Document.version`** — it looks like optimistic-locking
  support but is completely unused. Concurrent edits currently
  last-write-wins with no warning (see
  [known-issues.md](known-issues.md#6-no-optimistic-concurrency-control-on-document-edits--medium)).
- **Fail-open vs. fail-closed is intentional and inconsistent by design** —
  before copying a cross-service HTTP call pattern, check whether the call
  you're copying is protecting access (should fail closed) or just
  enriching a response (can fail open). See
  [decisions.md](decisions.md#5-fail-open-vs-fail-closed-is-chosen-per-call-site-not-globally).
- **The catalogue export endpoint is slow and this is known, not a
  regression you introduced** — if you're debugging a 30+ second document
  catalogue export, read
  [known-issues.md](known-issues.md#11-document-catalogue-export-makes-slow-sequential-per-product-http-calls--high-acknowledged-in-code)
  before assuming you broke something.
- **A group admin cannot delete a user account** — only remove them from a
  group. If a UI/API interaction seems to be "failing" to delete a user,
  check whether the caller is actually a superuser first.
- **`/api/customers`, `/api/companies`, `/api/projects` all require
  `documents` service access** — there's no separate "customers"
  permission, despite there being separate resources. Don't assume every
  resource type maps to its own grantable service.

## Where to go deeper

- [overview.md](overview.md) — what this system is and who uses it
- [architecture.md](architecture.md) — diagrams: system, request flow, auth flow, permission flow, deployment
- [modules.md](modules.md) — file-by-file tour of every service and the frontend
- [data-model.md](data-model.md) — full ERD, table-by-table breakdown, index/migration story
- [api.md](api.md) — every endpoint, by service
- [workflows.md](workflows.md) — business rules, lifecycles, permission model, storage strategy, more diagrams
- [deployment.md](deployment.md) — containers, ports, env vars, Dockerfiles
- [operations.md](operations.md) — running it, seeding/clearing data, common tasks
- [coding-standards.md](coding-standards.md) — patterns to match when you write new code here
- [decisions.md](decisions.md) — the "why" behind the non-obvious choices, with evidence
- [known-issues.md](known-issues.md) — code quality review: security, correctness, performance, maintainability
- [glossary.md](glossary.md) — every domain term, precisely as this codebase uses it
