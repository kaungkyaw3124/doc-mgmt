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


@app.on_event("startup")
def on_startup():
    enforce_production_secrets(settings.environment, _secret_problems())

    # NOTE: back to create_all() for now. Alembic migrations are set up
    # (see /alembic) but shelved until a later session — see project-status.md.
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
