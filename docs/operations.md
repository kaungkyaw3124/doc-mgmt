# Operations

## Starting / stopping the stack

```bash
cd infra
docker compose up -d          # start everything
docker compose restart nginx  # required after ANY nginx.conf edit — no hot reload
docker compose down            # stop (bind-mounted data in ./data/ survives)
```

Full data reset (destructive — confirm before running against anything you
care about):

```bash
docker compose down
rm -rf ./data/postgres ./data/minio ./data/meili
docker compose up -d
```

## Logging

No centralized logging (no ELK/Loki/CloudWatch integration found). Each
service logs to stdout/stderr, captured by Docker's default log driver —
retrieve with:

```bash
docker compose logs -f <service-name>       # e.g. document-service
docker compose logs -f nginx
```

Application-level logging is ad hoc: some modules create a module-level
`logger = logging.getLogger(__name__)` (e.g.
`services/document-service/app/core/catalogue_client.py`,
`app/core/search_client.py`, `app/routers/documents.py`) and log warnings on
failed cross-service calls or indexing failures; others (e.g.
`catalogue-service/app/main.py`'s Meilisearch startup try/except) simply
`pass` silently. There is no structured/JSON logging, no request-id
correlation across services, and no access-log configuration beyond Nginx's
and Uvicorn's own defaults. **This makes tracing a single user's request
across services (Nginx → auth-service → document-service → catalogue-service)
non-trivial** — there's no shared trace/correlation ID propagated between
them.

Two things worth knowing when debugging:

- `auth-service` logs a **startup warning** (not a runtime error) if
  `JWT_SECRET` or `SEED_ADMIN_PASSWORD` are still at their insecure
  defaults (`app/main.py:23-35`) — check container startup logs after any
  deploy to a new environment.
- Search-indexing failures (Meilisearch unreachable, etc.) are logged via
  `logger.exception(...)` in `search_client.py` in both document-service
  and catalogue-service, but **never surfaced to the API caller** — a
  document/product can be created successfully while silently failing to
  become searchable. If search results look stale/incomplete, check these
  logs before assuming a data problem.

## Health checks

Every service exposes `GET /health` → `{"status": "ok"}` (see
[api.md](api.md#health-checks)). None of these are wired into
`docker-compose.yml` as Compose `healthcheck:` blocks, and none are used by
any `depends_on` ordering — `depends_on` in this compose file only
sequences **container start order**, not "wait until healthy," so a service
can start before its dependency (e.g. Postgres) is actually ready to accept
connections. Each service's `create_engine(..., pool_pre_ping=True)`
(`app/core/db.py` in every service) mitigates this somewhat by testing
connections before use and reconnecting, but a hard failure on the very
first startup query is possible on a slow-booting host.

Manual health check sweep:

```bash
for s in auth-service document-service catalogue-service search-service; do
  echo -n "$s: "; docker compose exec "$s" curl -sf localhost:8000/health || echo FAIL
done
```

## Seed / clear demo data

Two standalone scripts, standard-library only (`urllib`), driven entirely
through the public API — not direct DB access (`seed-data-setup/scripts/`):

```bash
# populate: a company, 4 customers, 10 products, 3 projects, 7 documents
python3 seed_data.py
# BASE_URL / ADMIN_USERNAME / ADMIN_PASSWORD env vars override the
# localhost:8080 / admin / changeme defaults

# wipe: hard-deletes ALL documents, customers, products, projects, companies
# (deletes documents FIRST, since the others can't be deleted while referenced)
# does NOT touch user accounts, groups, or roles
python3 clear_data.py
```

Both scripts log in as `ADMIN_USERNAME`/`ADMIN_PASSWORD` first and fail fast
with a clear message if that login doesn't succeed. `clear_data.py` is
**irreversible** — hard deletes, not recycle-bin trashes; there is no
confirmation prompt built into the script itself.

## Common operational tasks

| Task | How |
|---|---|
| Create the first superuser | Automatic on first boot if `users` table is empty, from `SEED_ADMIN_USERNAME`/`SEED_ADMIN_PASSWORD`. After that, promote nobody else this way — there is no promote-to-superuser endpoint (see [known-issues.md](known-issues.md)); it requires direct DB access (`UPDATE users SET is_superuser = true WHERE username = '...';` against the `auth` database). |
| Rotate `JWT_SECRET` | Update `services/auth-service/.env`, `docker compose up -d --build auth-service` (or restart). **Invalidates every existing token immediately** — every logged-in user is forced to re-login. |
| Apply the optional performance indexes | `docker compose exec postgres psql -U docmgmt -d docmgmt -f /path/to/add_performance_indexes.sql` and again with `-d catalogue` — see [deployment.md](deployment.md#database-bootstrap). Safe to re-run (`IF NOT EXISTS`). |
| Change the Meilisearch master key | Must be updated in **four** places consistently: `infra/.env` (`MEILI_MASTER_KEY` used by the `meilisearch` container) and each of `document-service/.env`, `catalogue-service/.env`, `search-service/.env` — there is no single source of truth (`infra/docker-compose.yml:56-58`). |
| Inspect MinIO contents | `postgres`, `minio`, and `meilisearch` are **not** published to the host (see [deployment.md](deployment.md#containers)) — only reachable from other containers on the compose network. For a one-off look at the MinIO console, forward the port for just that session: `docker compose exec minio sh` for a shell inside the container, or temporarily run `docker run --rm --network infra_default -p 127.0.0.1:9001:9001 --name minio-console-tunnel alpine/socat TCP-LISTEN:9001,fork,reuseaddr TCP:minio:9001` from another terminal, browse `http://localhost:9001` (login: `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`), then `docker stop minio-console-tunnel` when done (same pattern with `9000`/`TCP:minio:9000` for the S3 API, or `7700`/`TCP:meilisearch:7700` for Meilisearch). Do not add `ports:` back to `infra/docker-compose.yml` for routine access — that reopens the same host exposure this was closed to prevent (see [known-issues.md](known-issues.md)). |
| Inspect Postgres directly | `docker compose exec postgres psql -U docmgmt -d <docmgmt\|catalogue\|auth>` — works without any port forwarding since it runs a client inside the same compose network. For an external tool (DBeaver, a local `psql`), use the same temporary `alpine/socat` tunnel pattern as MinIO above (`TCP:postgres:5432`), not a permanently published port. |
| Recover a soft-deleted document/product/customer/project/company | Via the UI's "Recycle bin" button on each list view, or `PATCH /api/<resource>/{id}/restore` directly. |
| Force-remove a customer/project/company that a document still references | Not directly supported — the API returns `400` ("...reference this X... reassign or delete those documents first") by design (`IntegrityError` caught in each router's `DELETE` handler) rather than cascading. Reassign or delete the referencing document(s) first. |

## Known operational gaps

- **No automated backups** of Postgres, MinIO, or Meilisearch data — see
  [deployment.md](deployment.md#whats-absent). Until one exists, treat the
  `infra/data/` bind mounts as the only copy of production data.
- **No monitoring/alerting** (no Prometheus/Grafana/health-check-based
  paging found).
- **No rate limiting** on any endpoint other than `/api/auth/login` (rate
  limited as of `docs/SECURITY_HARDENING_LOG.md` Task 1) — see
  [known-issues.md](known-issues.md).
- **Nginx config changes require a manual restart** (see above) — easy to
  forget and ship a config change that silently doesn't take effect.
