# Architecture

## System architecture

```mermaid
flowchart TB
    Browser["Browser<br/>web/index.html (static SPA)"]

    subgraph Gateway["Nginx gateway — infra/nginx/nginx.conf — the only published port (8080)"]
        Nginx["nginx:alpine<br/>serves /web as static files<br/>reverse-proxies /api/*<br/>auth_request → auth-service /verify"]
    end

    subgraph Services["Internal services — expose: 8000, no host port"]
        Auth["auth-service<br/>FastAPI<br/>login, RBAC, /verify"]
        Doc["document-service<br/>FastAPI<br/>documents, customers,<br/>projects, companies, exports, audit"]
        Cat["catalogue-service<br/>FastAPI<br/>products, categories, sub-items"]
        Search["search-service<br/>FastAPI<br/>Meilisearch proxy"]
    end

    subgraph Data["Data stores"]
        PgAuth[("Postgres: auth DB")]
        PgDoc[("Postgres: docmgmt DB")]
        PgCat[("Postgres: catalogue DB")]
        Minio[("MinIO<br/>buckets: documents, products")]
        Meili[("Meilisearch<br/>indices: documents, products")]
    end

    Browser -- "HTTPS/HTTP :8080" --> Nginx
    Nginx -- "auth_request /_verify" --> Auth
    Nginx -- "/api/documents,/customers,/companies,/projects" --> Doc
    Nginx -- "/api/products,/categories" --> Cat
    Nginx -- "/api/search" --> Search
    Nginx -- "/api/auth/*, /api/admin/*" --> Auth

    Doc -- "HTTP: get_product, sub-items,<br/>file-content, download-bundle" --> Cat
    Cat -- "HTTP: visible-product-ids" --> Doc
    Search -- "HTTP: visible-product-ids" --> Doc

    Auth --> PgAuth
    Doc --> PgDoc
    Cat --> PgCat
    Doc --> Minio
    Cat --> Minio
    Doc --> Meili
    Cat --> Meili
    Search --> Meili
```

Key structural facts, all verifiable in `infra/docker-compose.yml` and
`infra/nginx/nginx.conf`:

- **Nginx is the single trust boundary.** Every browser request goes through
  it; internal services (`auth-service`, `document-service`,
  `catalogue-service`, `search-service`) have no published host port and are
  only reachable on the Docker Compose network.
- **Downstream services trust Nginx's forwarded headers unconditionally.**
  `document-service`, `catalogue-service`, and `search-service` read
  `X-Allowed-Projects`, `X-Access-Level`, `X-Has-Audit-Log`,
  `X-Has-Category-Access`, and `X-Username` directly off the request with no
  independent verification — these are only meaningful because Nginx's
  `auth_request` step populated them from `auth-service`'s `/verify`
  response first (`infra/nginx/nginx.conf:75-159`). Any direct,
  non-gateway access to a service's port would bypass authorization
  entirely — see [known-issues.md](known-issues.md).
- **No message queue, cache layer, or event bus.** Confirmed absent by
  repository-wide search — no Redis, Celery, RabbitMQ, or Kafka client in
  any `requirements.txt` (a stray `infra/data/redis/` entry in `.gitignore`
  suggests Redis was considered at some point but never implemented).
- **Search indexing is synchronous and best-effort.** Each write to a
  document or product calls Meilisearch inline, in the same request, and
  swallows failures (logs only) — see `app/core/search_client.py` in both
  `document-service` and `catalogue-service`.

## Request flow

Every protected API call follows the same shape. Example: `GET /api/documents`.

```mermaid
sequenceDiagram
    participant B as Browser
    participant N as Nginx
    participant A as auth-service (/verify)
    participant D as document-service

    B->>N: GET /api/documents<br/>Authorization: Bearer <jwt>
    N->>A: GET /verify<br/>Authorization: <jwt><br/>X-Service: documents
    A->>A: decode JWT, load user,<br/>check is_active/is_approved,<br/>check service access + level,<br/>compute allowed project ids
    A-->>N: 200 OK<br/>X-Allowed-Projects, X-Access-Level,<br/>X-Has-Audit-Log, X-Username
    N->>D: GET /documents<br/>(headers forwarded, rewritten path)
    D->>D: query documents table,<br/>filter by project_id IN allowed<br/>OR project_id IS NULL
    D-->>N: 200 OK [documents...]
    N-->>B: 200 OK [documents...]
```

If `/verify` returns `401` (missing/invalid/expired token) or `403` (valid
token, no access to that service), Nginx's `auth_request` short-circuits and
the downstream service is never called (`infra/nginx/nginx.conf:24-31`,
`services/auth-service/app/routers/auth.py:113-171`).

A handful of routes skip `auth_request` entirely because they are public or
self-authorizing:

- `POST /api/auth/login`, `POST /api/auth/register`, `GET
  /api/auth/groups-public` — public, no token needed
  (`infra/nginx/nginx.conf:34-54`).
- `/api/auth/users`, `/api/admin/*` — proxied straight to `auth-service`
  with only the raw `Authorization` header forwarded; `auth-service`'s own
  route handlers (`app/core/deps.py`, `app/routers/admin.py`) perform the
  JWT decode and permission checks themselves
  (`infra/nginx/nginx.conf:56-71`).

## Authentication flow

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as auth-service

    B->>A: POST /api/auth/login {username, password}
    A->>A: look up user by username,<br/>verify_password (bcrypt)
    alt invalid credentials
        A-->>B: 401
    else not yet approved
        A-->>B: 403 "pending administrator approval"
    else disabled
        A-->>B: 403 "account has been disabled"
    else OK
        A->>A: create_access_token(subject=user.id)<br/>HS256, exp = now + JWT_EXPIRE_MINUTES
        A-->>B: 200 {access_token, token_type: bearer}
    end
    Note over B: token stored in localStorage (ledger_token)<br/>sent as Authorization: Bearer <token> on every request
```

Notable details:

- The JWT `sub` claim is the user's **immutable UUID**, not their username
  (`services/auth-service/app/routers/auth.py:109`,
  `app/core/jwt_utils.py:8-11`) — this was a deliberate fix (commit
  `b6687c9`) so that renaming a user cannot cause a still-valid old token to
  resolve to a different account.
  Access token lifetime defaults to 60 minutes
  (`app/core/config.py:16`, `JWT_EXPIRE_MINUTES`). **There is no refresh
  token or revocation list** — a token remains valid until it expires, even
  if the user is disabled *after* issuance, except that
  `get_current_user`/`/verify` re-check `is_active`/`is_approved` on every
  request, so a disabled user is denied on their very next call even with
  an unexpired token (`app/core/deps.py:35-38`,
  `app/routers/auth.py:152-156`).
- Passwords are hashed with bcrypt via passlib
  (`app/core/security.py`); the JWT secret and algorithm are configured via
  `JWT_SECRET` / `JWT_ALGORITHM` (default `HS256`) in
  `app/core/config.py:14-15`. `main.py:23-28` logs a startup warning if the
  default insecure `JWT_SECRET` is still in use.
- On first boot, if the `users` table is completely empty, a superuser is
  seeded from `SEED_ADMIN_USERNAME`/`SEED_ADMIN_PASSWORD`
  (`app/main.py:37-54`).

## Permission (authorization) flow

```mermaid
flowchart TD
    Start["Request with X-Service = documents|products|search"] --> Super{is_superuser?}
    Super -- yes --> Allow["Access granted, access_level = edit,<br/>allowed_projects = ALL"]
    Super -- no --> Grant["Any UserRole → Role → RoleAccess<br/>where Role.is_active AND Role.group.is_active<br/>AND RoleAccess.service_name = X-Service?"]
    Grant -- no --> Deny["403 — no access to this service"]
    Grant -- yes --> Level["access_level = edit if ANY matching<br/>grant is 'edit', else 'view'<br/>(most-permissive-wins across roles)"]
    Level --> Proj["UserProjectAccess rows for this user?"]
    Proj -- none --> All["allowed_projects = ALL (unrestricted)"]
    Proj -- some --> Restricted["allowed_projects = that list<br/>(opt-in restriction)"]
    All --> Allow2["Access granted"]
    Restricted --> Allow2
```

This logic lives entirely in `services/auth-service/app/core/authz.py`
(`user_has_service_access`, `get_user_access_level`,
`get_user_allowed_project_ids`) and is evaluated fresh on every `/verify`
call — there is no caching of permission decisions. See
[workflows.md](workflows.md#permission-model) for the full group/role/grant
data model and [data-model.md](data-model.md) for the schema.

## Deployment diagram

```mermaid
flowchart TB
    subgraph Host["Single Docker host (Docker Compose)"]
        subgraph Net["docmgmt Compose network"]
            NginxC["nginx :8080→80"]
            AuthC["auth-service :8000 (internal)"]
            DocC["document-service :8000 (internal)"]
            CatC["catalogue-service :8000 (internal)"]
            SearchC["search-service :8000 (internal)"]
            PgC["postgres :5432 (internal only)"]
            MinioC["minio :9000, :9001 (internal only)"]
            MeiliC["meilisearch :7700 (internal only)"]
        end
        VolPg[["bind mount ./data/postgres"]]
        VolMinio[["bind mount ./data/minio"]]
        VolMeili[["bind mount ./data/meili"]]
    end
    Client["Admin / staff browser"] -->|":8080"| NginxC
    PgC --- VolPg
    MinioC --- VolMinio
    MeiliC --- VolMeili
```

See [deployment.md](deployment.md) for the full container table, port list,
and environment variable reference.
