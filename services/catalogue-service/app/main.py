import logging
import os

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import Base, engine, get_db
from app.core.concurrency import CONFLICT_DETAIL
from app import models
from app.core.storage import ensure_bucket_exists
from app.core.search_client import _client as meili_client, _INDEX_NAME as MEILI_PRODUCTS_INDEX, rebuild_index
from app.core.secrets_check import db_password_from_url, enforce_production_secrets, is_insecure
from app.core.gateway_auth import verify_gateway_secret
from app.routers import products, categories

logger = logging.getLogger(__name__)

app = FastAPI(title="Catalogue Service", version="0.1.0")

app.middleware("http")(verify_gateway_secret)


@app.exception_handler(StaleDataError)
def _lost_update(request, exc):
    """Two saves of the same version raced: the second one's UPDATE matched no
    row (models' version_id_col). Same answer as a stale version in the body."""
    return JSONResponse(status_code=409, content={"detail": CONFLICT_DETAIL})

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
    if is_insecure(settings.internal_shared_secret, {"local_dev_internal_secret_change_me"}):
        problems.append("INTERNAL_SHARED_SECRET")
    return problems


_ALEMBIC_INI = os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")


@app.on_event("startup")
def on_startup():
    enforce_production_secrets(settings.environment, _secret_problems())

    # create_all() creates missing tables; Alembic then adds columns to tables
    # that already existed (create_all never alters one). Every migration is
    # guarded/idempotent, so this order is safe from any starting state.
    Base.metadata.create_all(bind=engine)
    alembic_command.upgrade(AlembicConfig(_ALEMBIC_INI), "head")
    ensure_bucket_exists()

    try:
        meili_client.index(MEILI_PRODUCTS_INDEX).update_filterable_attributes(["category", "group_id"])
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


# Called only by backup-service (internal secret; nginx routes nothing here).
@app.post("/internal/after-restore")
def after_restore(db: Session = Depends(get_db)):
    """After backup-service replaced the database: re-run the idempotent
    startup (create_all, bucket) and rebuild the search index."""
    on_startup()
    return {"indexed": rebuild_index(db.query(models.Product).all())}
