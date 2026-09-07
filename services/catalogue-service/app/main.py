import logging

from fastapi import FastAPI

from app.core.config import settings
from app.core.db import Base, engine
from app.core.storage import ensure_bucket_exists
from app.core.search_client import _client as meili_client, _INDEX_NAME as MEILI_PRODUCTS_INDEX
from app.core.secrets_check import db_password_from_url, enforce_production_secrets, is_insecure
from app.routers import products, categories

logger = logging.getLogger(__name__)

app = FastAPI(title="Catalogue Service", version="0.1.0")

app.include_router(products.router)
app.include_router(categories.router)


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
    return problems


@app.on_event("startup")
def on_startup():
    enforce_production_secrets(settings.environment, _secret_problems())

    # Matches document-service's current approach: create_all() for now,
    # Alembic migrations to be introduced properly in a later session.
    Base.metadata.create_all(bind=engine)
    ensure_bucket_exists()

    try:
        meili_client.index(MEILI_PRODUCTS_INDEX).update_filterable_attributes(["category"])
    except Exception:
        logger.warning(
            "couldn't set Meilisearch filterable attributes on startup — "
            "category filtering won't work until this succeeds (Meilisearch "
            "may not be up yet, or MEILI_MASTER_KEY may be wrong)",
            exc_info=True,
        )


@app.get("/health")
def health():
    return {"status": "ok"}
