# Overview

## What problem does this solve?

This is an internal **document management and product catalogue platform** for a
sales/trading business (seed data and default supplier name — "Trustwell
International Co., Ltd." — suggest an IT/electronics reseller in Myanmar; see
`seed-data-setup/scripts/seed_data.py` and
`services/document-service/app/core/config.py:21`). It lets staff:

- Maintain a **product catalogue** (SKUs, categories, prices, attached spec
  sheets/images, "bundle" products made of sub-items) —
  `services/catalogue-service`.
- Create **commercial documents** (quotations, invoices, proposals, catalogue
  hand-outs) built from customers, projects, and catalogue line items, and
  export them as branded **PDF/XLSX quotations** or a **zip of product
  catalogue files** — `services/document-service`.
- **Search** across documents and products from one box —
  `services/search-service`.
- Control **who can do what** via a group/role-based access system with
  optional per-project data visibility, self-service registration with admin
  approval, and a per-document **audit trail** — `services/auth-service`.

There is no evidence in the code of e-signature, payment processing, or
accounting/ledger reconciliation features — despite the frontend being titled
"Ledger" (`web/index.html:6`), it is a document/catalogue/CRM-lite tool, not a
bookkeeping ledger. **Not verified from repository**: the specific industry or
company this was built for beyond what the seed data and default supplier name
imply.

## Who are the users?

Inferred entirely from the permission model in
`services/auth-service/app/models.py` and `app/routers/admin.py`, and from
`Project status.md` (see [decisions.md](decisions.md)):

- **Superuser** — unrestricted administrator; the only role that can create
  top-level groups, grant a group a "pool" of visible projects, or create
  bare user accounts with no group.
- **Group admin** — manages one organizational group (e.g. a sales team):
  approves/creates/removes its members, creates roles scoped to that group,
  grants those roles service access (documents/products/search/audit-log/
  categories) and hands out project visibility drawn from the group's pool.
- **Regular member** — gets whatever access their assigned role(s) grant;
  can be `view`-only or `edit` on documents specifically (the only service
  with a view/edit distinction — `services/auth-service/app/models.py:71-86`).
- **"Director"-style reviewer** — implied by the `audit-log` grantable
  service (`services/document-service/app/routers/documents.py:585-609`),
  a role intended to review who viewed/edited/changed a document's status,
  without necessarily having edit rights themselves.
- **Self-registering prospective user** — anyone can `POST /register`
  (`services/auth-service/app/routers/auth.py:65-91`); the account is
  unusable until an admin approves it.

## Overall architecture

A **microservices** system behind a single **Nginx API gateway**, with each
backend service owning its own PostgreSQL database (database-per-service),
shared MinIO object storage (one bucket per service), and a shared
Meilisearch instance (one index per service). The frontend is a single static
HTML/CSS/JS file with no build step, served by the same Nginx.

See [architecture.md](architecture.md) for the full diagram and
request-flow/authentication-flow breakdown, [modules.md](modules.md) for a
per-service tour, and [data-model.md](data-model.md) for the database
structure.

## Tech stack

| Layer | Technology | Evidence |
|---|---|---|
| Backend language/framework | Python 3.12, FastAPI 0.115 | every `services/*/requirements.txt` |
| ORM / DB driver | SQLAlchemy 2.0.35 (sync), psycopg2-binary | `services/*/app/core/db.py` |
| Migrations | Alembic 1.13.3 — present but **not applied at runtime** (see [decisions.md](decisions.md)) | `services/document-service/alembic/`, `app/main.py:18-19` |
| Validation | Pydantic 2.9 / pydantic-settings 2.5 | `app/schemas.py`, `app/core/config.py` in each service |
| Auth | PyJWT 2.9 (HS256), passlib+bcrypt | `services/auth-service/app/core/jwt_utils.py`, `security.py` |
| Object storage | MinIO via boto3 1.35 (S3-compatible) | `services/*/app/core/storage.py` |
| Search | Meilisearch v1.10, `meilisearch` Python client 0.31.5 | `infra/docker-compose.yml:53-65`, `app/core/search_client.py` |
| Database | PostgreSQL 16, one server / three logical databases | `infra/docker-compose.yml:2-15`, `infra/init-db/*.sql` |
| Document export | openpyxl 3.1.5 (XLSX), WeasyPrint 62.3 + Jinja2 3.1.4 (PDF), Pillow 11.0.0 (image processing) | `services/document-service/app/core/export_*.py`, `requirements.txt` |
| Archive handling | Python `zipfile` (built-in) + `unar` CLI (RAR, shelled out) | `services/catalogue-service/app/routers/products.py:67-103`, `Dockerfile:8` |
| Gateway / reverse proxy | Nginx (alpine), `auth_request` module | `infra/nginx/nginx.conf` |
| Frontend | Vanilla HTML/CSS/JS, no framework, no bundler, no npm | `web/index.html` |
| Containerization | Docker, Docker Compose | `infra/docker-compose.yml`, each service's `Dockerfile` |
| CI/CD | **Not verified from repository** — no `.github/`, `.gitlab-ci.yml`, or similar found | — |
| Tests | **None found** — no test files, no test framework in any `requirements.txt` | repo-wide search |

## Design patterns

See [modules.md](modules.md#design-patterns-catalogue) for a file-by-file
catalogue. In summary: API Gateway, Dependency Injection (FastAPI `Depends`),
a thin Service/Controller-in-one layer (routers call the ORM directly — no
separate repository classes), HTTP Client/Adapter wrappers for cross-service
calls, and a uniform soft-delete ("recycle bin") pattern. No queue, cache,
event bus, or formal Observer/Factory/Strategy implementation was found
(Strategy-like branching exists only informally, e.g. zip-vs-rar extraction).

## Repository organization

```
doc-mgmt/
├── add_performance_indexes.sql      # optional, manually-run index script (not in migrations)
├── infra/                           # docker-compose, nginx, DB bootstrap SQL
│   ├── docker-compose.yml
│   ├── nginx/nginx.conf
│   └── init-db/*.sql                # creates the "catalogue" and "auth" databases
├── services/
│   ├── auth-service/                # identity, RBAC, gateway's auth_request target
│   ├── catalogue-service/           # products, categories, sub-items
│   ├── document-service/            # documents, customers, projects, companies, exports, audit log
│   └── search-service/              # thin Meilisearch proxy with project-visibility filtering
├── web/index.html                   # entire frontend — one static file
├── seed-data-setup/scripts/         # seed_data.py / clear_data.py — API-driven demo data tools
└── docs/                            # this documentation set
```

Each service under `services/*/app/` follows the same internal layout:
`main.py` (FastAPI app + startup), `models.py` (SQLAlchemy ORM), `schemas.py`
(Pydantic, document/catalogue services only), `core/` (config, db session,
cross-cutting helpers), `routers/` (the actual endpoints).

## Runtime architecture

Nine containers, one Docker network, one external port:

- `postgres` (16) — one server process, three databases (`docmgmt`,
  `catalogue`, `auth`), port `5432` published to the host.
- `minio` — object storage, ports `9000` (S3 API) and `9001` (console)
  published to the host.
- `meilisearch` (v1.10) — search engine, port `7700` published to the host.
- `auth-service`, `catalogue-service`, `document-service`, `search-service` —
  internal only (`expose: 8000`, no host port mapping).
- `nginx` — the **only** container with a published port (`8080:80`); serves
  `web/index.html` as static content and reverse-proxies `/api/*`.

All inter-service traffic (browser→services, and service→service, e.g.
document-service calling catalogue-service) happens over the Docker Compose
network by service name. See [deployment.md](deployment.md) for the full
container/port table and environment variables.

## Main modules

| Module | Responsibility | Owns data | Docs |
|---|---|---|---|
| `auth-service` | Login, registration/approval, JWT issuance, RBAC (groups/roles/access grants), per-user project visibility, the `/verify` endpoint Nginx gates every request through | `auth` DB | [api.md](api.md#auth-service), [workflows.md](workflows.md#permission-model) |
| `document-service` | Documents (invoice/quotation/proposal/catalogue), customers, projects, companies, line items, exports (PDF/XLSX/zip), audit log | `docmgmt` DB, `documents` MinIO bucket | [api.md](api.md#document-service), [workflows.md](workflows.md#document-lifecycle) |
| `catalogue-service` | Products, categories, product sub-items (bundles), bulk import | `catalogue` DB, `products` MinIO bucket | [api.md](api.md#catalogue-service) |
| `search-service` | Free-text search across documents and products via Meilisearch, with project-visibility filtering | none (Meilisearch only) | [api.md](api.md#search-service), [workflows.md](workflows.md#search-workflow) |
| `web/index.html` | The entire UI | — | [modules.md](modules.md#frontend) |

## Deployment model

Single-host **Docker Compose** deployment (`infra/docker-compose.yml`),
evidently intended for one on-premises/VPS host referred to as `gpu-server`
in `seed-data-setup/scripts/seed_data.py:7` and the historical
`Project status.md`. No Kubernetes manifests, no Helm charts, no
multi-environment (staging/prod) configuration, and no CI/CD pipeline were
found. See [deployment.md](deployment.md).

## External dependencies

- **PostgreSQL 16** (self-hosted via Compose) — primary datastore, one
  database per service.
- **MinIO** (self-hosted via Compose) — S3-compatible object storage for
  uploaded files (documents, product catalogue files, company logos/seals).
- **Meilisearch v1.10** (self-hosted via Compose) — search indexing/query
  engine.
- **Google Fonts** (`fonts.googleapis.com`) — the frontend loads Zilla Slab,
  Inter, and IBM Plex Mono from Google's CDN at runtime
  (`web/index.html:7-8`) — the only external network dependency for the UI,
  and the only outbound call to a third party found anywhere in the system.
- No third-party SaaS APIs, payment gateways, email/SMS providers, or
  external identity providers (SSO/OAuth) were found anywhere in the code.
