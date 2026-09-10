import os

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI

from app.core.config import settings
from app.core.db import Base, engine
from app.core.storage import ensure_bucket_exists
from app.core.search_client import _client as meili_client, _INDEX_NAME as MEILI_DOCS_INDEX
from app.core.secrets_check import db_password_from_url, enforce_production_secrets, is_insecure
from app.core.gateway_auth import verify_gateway_secret
from app.routers import documents, customers, companies, projects

app = FastAPI(title="Document Service", version="0.1.0")

app.middleware("http")(verify_gateway_secret)

app.include_router(documents.router)
app.include_router(customers.router)
app.include_router(companies.router)
app.include_router(projects.router)


def _secret_problems() -> list[str]:
    """Pure check, no I/O — kept separate from on_startup so it can be
    tested without real Postgres/MinIO/Meilisearch, and so it runs and
    can fail BEFORE any of on_startup's I/O below it."""
    problems = []
    if is_insecure(settings.meili_master_key, {"local_dev_master_key_change_me"}):
        problems.append("MEILI_MASTER_KEY")
    if is_insecure(settings.minio_access_key, {"minioadmin"}):
        problems.append("MINIO_ACCESS_KEY")
    if is_insecure(settings.minio_secret_key, {"minioadmin"}):
        problems.append("MINIO_SECRET_KEY")
    if is_insecure(db_password_from_url(settings.database_url), {"docmgmt", "postgres"}):
        problems.append("DATABASE_URL password")
    if is_insecure(settings.internal_shared_secret, {"local_dev_internal_secret_change_me"}):
        problems.append("INTERNAL_SHARED_SECRET")
    return problems


def _run_migrations() -> None:
    """
    Runs `alembic upgrade head` in-process before anything else touches
    the database. See docs/SECURITY_HARDENING_LOG.md's "Documents API
    500s" entry: this project used to rely solely on
    `Base.metadata.create_all()`, which only ever creates *missing
    tables* — it never adds a column to a table that already exists.
    That's exactly what broke GET /documents and GET /documents/trash
    the moment migration 0003 added Document.deleted_by/deleted_at: any
    already-running deployment's `documents` table already existed, so
    create_all() silently did nothing for those two columns, and the ORM
    started querying for columns the real database didn't have.

    Every migration in alembic/versions is now guarded/idempotent (checks
    what already exists before creating/altering anything — see 0001's
    own module docstring for why that had to be fixed too), so this is
    safe to run unconditionally on every startup, from any starting
    state: a brand-new empty database, one that only ever saw
    create_all() and was never stamped, or one already correctly stamped
    at some earlier revision. Deliberately NOT wrapped in a try/except —
    a database left behind by a failed or partial migration is not a
    state this service should silently keep running against.
    """
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")
    alembic_cfg = AlembicConfig(config_path)
    alembic_command.upgrade(alembic_cfg, "head")


@app.on_event("startup")
def on_startup():
    enforce_production_secrets(settings.environment, _secret_problems())

    _run_migrations()

    # create_all() runs AFTER the real migrations, as a safety net only —
    # it catches a table/column that exists in models.py but doesn't yet
    # have a migration written for it (this has happened before, see
    # migration 0002's own docstring), never as the primary way schema
    # changes reach a real deployment.
    Base.metadata.create_all(bind=engine)
    ensure_bucket_exists()

    # Meilisearch requires filterable attributes to be declared explicitly
    # before they can be used in search filters (e.g. ?doc_type=invoice).
    try:
        meili_client.index(MEILI_DOCS_INDEX).update_filterable_attributes(
            ["doc_type", "status", "customer_id", "project_id"]
        )
    except Exception:
        pass  # Meilisearch may not be up yet on very first boot; safe to skip


@app.get("/health")
def health():
    return {"status": "ok"}
