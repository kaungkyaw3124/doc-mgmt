# Deployment

## Model

Single-host **Docker Compose** deployment — one `docker-compose.yml`
(`infra/docker-compose.yml`), no environment-specific overlays, no
Kubernetes/Helm artifacts, no multi-region or multi-host configuration
anywhere in the repository. The historical `Project status.md` (recovered
from the initial commit, see [decisions.md](decisions.md)) refers to the
target host as `gpu-server`, and `seed-data-setup/scripts/seed_data.py`/
`clear_data.py` default to `http://localhost:8080/api`, both consistent with
a single always-on dev/production box rather than ephemeral cloud
infrastructure.

## Containers

| Service | Image / build | Published ports | Internal port | Depends on |
|---|---|---|---|---|
| `postgres` | `postgres:16` | `5432:5432` | 5432 | — |
| `minio` | `minio/minio` | `9000:9000` (S3), `9001:9001` (console) | 9000/9001 | — |
| `meilisearch` | `getmeili/meilisearch:v1.10` | `7700:7700` | 7700 | — |
| `auth-service` | `../services/auth-service` | none (`expose: 8000`) | 8000 | `postgres` |
| `catalogue-service` | `../services/catalogue-service` | none | 8000 | `postgres`, `meilisearch`, `minio` |
| `document-service` | `../services/document-service` | none | 8000 | `postgres`, `minio`, `catalogue-service`, `meilisearch` |
| `search-service` | `../services/search-service` | none | 8000 | `meilisearch`, `document-service` |
| `nginx` | `nginx:alpine` | **`8080:80`** | 80 | `document-service`, `catalogue-service`, `search-service`, `auth-service` |

All services set `restart: unless-stopped`
(`infra/docker-compose.yml`). **`nginx` is the only container reachable from
outside the Docker host's own network** by design — every other published
port (`5432`, `9000`, `9001`, `7700`) is explicitly called out in the compose
file's own comments as convenient for local debugging and something to
"remove later" (`infra/docker-compose.yml:13-14`) — this has not yet been
done (see [known-issues.md](known-issues.md)).

## Persistent volumes

Bind mounts under `infra/data/` (relative to the compose file), **not**
Docker named volumes:

- `./data/postgres` → `/var/lib/postgresql/data`
- `./data/minio` → `/data`
- `./data/meili` → `/meili_data`

`.gitignore` excludes `infra/data/`, `infra/data/postgres/`,
`infra/data/minio/`, and (notably) `infra/data/redis/` — the last one is a
leftover reference to a service that does not exist anywhere in the current
compose file or code, suggesting Redis was considered and dropped at some
point (**not verified** — no other trace of it exists).

`docker compose down -v` does **not** clear these directories since they are
bind mounts, not named volumes — a full reset requires `docker compose
down && rm -rf ./data/postgres ./data/minio ./data/meili` (per the historical
project notes) before `docker compose up -d`.

## Database bootstrap

On a **first-ever** boot of the `postgres` container against an empty data
directory, Postgres runs everything in `infra/init-db/` in filename order:

1. `01-create-catalogue-db.sql` → `CREATE DATABASE catalogue OWNER docmgmt;`
2. `02-create-auth-db.sql` → `CREATE DATABASE auth OWNER docmgmt;`

The `docmgmt` database itself is created automatically by the official
Postgres image from `POSTGRES_DB=docmgmt` (`infra/docker-compose.yml:5`).
Table creation inside each database then happens at **application startup**
via SQLAlchemy `Base.metadata.create_all()` in each service's `main.py` —
not via Alembic, and not idempotently guarded against concurrent multi-
service startup racing to create the same tables (in practice, safe under
Postgres's `CREATE TABLE IF NOT EXISTS`-like idempotency, but genuinely
untested — see [known-issues.md](known-issues.md)). See
[data-model.md](data-model.md#migration-order) for the full migration-vs-
`create_all()` story.

`add_performance_indexes.sql` (repo root) is a **manual, optional,
post-deployment step** — it must be copied/pasted or piped into `psql`
against each of the `docmgmt` and `catalogue` databases by hand; it is not
referenced by any Dockerfile, compose service, or startup script.

## Environment variables

Each service reads its own `.env` file (via `env_file:` in
`docker-compose.yml`, pointing at `../services/<name>/.env` — **not**
committed to git; only `.env.example` is). `pydantic-settings`
(`app/core/config.py` in each service) supplies the defaults shown below if
a variable is unset.

### auth-service (`services/auth-service/.env.example`)

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql://docmgmt:docmgmt@postgres:5432/auth` | |
| `SEED_ADMIN_USERNAME` | `admin` | first-boot superuser, only used if `users` table is empty |
| `SEED_ADMIN_PASSWORD` | `changeme` | ⚠ warned about at startup if left default |
| `JWT_SECRET` | `local_dev_jwt_secret_change_me` | ⚠ warned about at startup if left default — anyone who knows it can forge tokens, including superuser tokens |
| `JWT_ALGORITHM` | `HS256` | |
| `JWT_EXPIRE_MINUTES` | `60` | |

### catalogue-service

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql://docmgmt:docmgmt@postgres:5432/catalogue` | |
| `DOCUMENT_SERVICE_URL` (config key `document_service_url`) | `http://document-service:8000` | for `visible-product-ids` calls |
| `MEILI_URL` | `http://meilisearch:7700` | |
| `MEILI_MASTER_KEY` | `local_dev_master_key_change_me` | ⚠ no startup warning if left default (inconsistent with `JWT_SECRET`'s warning) |
| `MINIO_ENDPOINT` | `minio:9000` | internal |
| `MINIO_PUBLIC_ENDPOINT` | `localhost:9000` | for presigned URLs |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | `minioadmin` / `minioadmin` | ⚠ no startup warning |
| `MINIO_BUCKET` | `products` | |
| `MINIO_SECURE` | `false` | http vs https for the S3 endpoint |

### document-service

Same `MEILI_*`/`MINIO_*` shape as catalogue-service (`MINIO_BUCKET=documents`),
plus:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql://docmgmt:docmgmt@postgres:5432/docmgmt` | |
| `CATALOGUE_SERVICE_URL` | `http://catalogue-service:8000` | |
| `SUPPLIER_NAME`, `SUPPLIER_POSITION`, `SUPPLIER_ADDRESS`, `SUPPLIER_CONTACT`, `SUPPORT_EMAIL`, `SUPPORT_PHONE` | `Trustwell International Co., Ltd.` / `Director` / empty / empty / empty / empty | Declared in `config.py` but **not referenced by any router or export code** — the actual PDF/XLSX exports pull supplier info from the `companies` table (per-document `company_id`) instead. Appears to be superseded, unused configuration — see [known-issues.md](known-issues.md). |

### search-service

| Variable | Default |
|---|---|
| `MEILI_URL` | `http://meilisearch:7700` |
| `MEILI_MASTER_KEY` | `local_dev_master_key_change_me` |
| `DOCUMENT_SERVICE_URL` (config key `document_service_url`) | `http://document-service:8000` |

### docker-compose-level overrides

| Variable | Default | Used by |
|---|---|---|
| `POSTGRES_PASSWORD` | `docmgmt` | `postgres` service |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | `minioadmin` / `minioadmin` | `minio` service |
| `MEILI_MASTER_KEY` | `local_dev_master_key_change_me` | `meilisearch` service — **must be kept in sync** with each service's own `MEILI_MASTER_KEY` `.env` value by hand; there is no single source of truth (`infra/docker-compose.yml:56-58` comment) |

These three are read from an `infra/.env` file if present (Compose
auto-loads it) — not committed to git.

## Dockerfiles

All four services build from `python:3.12-slim-bookworm`. Notable
per-service system packages:

- **auth-service**: `gcc`, `libpq-dev` (build psycopg2).
- **catalogue-service**: `gcc`, `libpq-dev`, plus **`unar`** — the CLI tool
  used to extract `.rar` catalogue archives during bulk import
  (`services/catalogue-service/app/routers/products.py:67-103`).
- **document-service**: `gcc`, `libpq-dev`, plus WeasyPrint's native
  dependencies — `libpango-1.0-0`, `libpangocairo-1.0-0`,
  `libgdk-pixbuf2.0-0`, `libffi-dev`, `shared-mime-info`,
  `fonts-liberation` (PDF rendering).
- **search-service**: no extra system packages — pure HTTP proxy.

Every Dockerfile ends with `CMD ["uvicorn", "app.main:app", "--host",
"0.0.0.0", "--port", "8000"]` — a single Uvicorn worker, no `--workers` flag,
no Gunicorn process manager, no `--reload` (production-style single-process
run, but with no horizontal replication configured either).

## Nginx as the deployment's edge

`infra/nginx/nginx.conf` is mounted read-only into the `nginx` container
alongside `../web` (the entire frontend directory, i.e. just
`index.html`) served as static content at `/`. Key operational details:

- `client_max_body_size 25m` — raised from Nginx's 1MB default so catalogue
  files, PDFs, and logos can actually upload; anything larger fails with a
  plain Nginx error page before reaching any service.
- `/api/documents` gets an extended `proxy_read_timeout 300s` specifically
  to tolerate the catalogue-export endpoint's slow, sequential per-product
  calls to catalogue-service (observed 30+ seconds each — see
  [known-issues.md](known-issues.md)).
- **Nginx does not hot-reload `nginx.conf` on `docker compose up -d`** if
  only the config file changed and the service definition didn't — this
  bit the original developers twice per the historical project notes.
  Any `nginx.conf` edit requires an explicit `docker compose restart
  nginx`.

## What's absent

- **CI/CD**: no `.github/workflows`, `.gitlab-ci.yml`, `Jenkinsfile`, or
  equivalent found anywhere in the repository.
- **Tests**: no test files, no test framework dependency in any
  `requirements.txt`.
- **Backups**: no backup scripts, cron jobs, or documented backup procedure
  for the Postgres/MinIO/Meilisearch bind-mounted data.
- **Secrets management**: no Vault/SSM/sealed-secrets integration — secrets
  are plain `.env` files on disk, excluded from git via `.gitignore`.
- **TLS**: `nginx.conf` listens on plain `:80` (mapped to host `:8080`) with
  no HTTPS/TLS termination configured anywhere in this repository.
  **Not verified**: whether TLS is terminated by something in front of this
  stack (e.g. a reverse proxy or load balancer on the host) that isn't part
  of this repo.
