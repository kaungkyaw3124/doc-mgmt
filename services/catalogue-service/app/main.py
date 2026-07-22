from fastapi import FastAPI

from app.core.db import Base, engine
from app.core.storage import ensure_bucket_exists
from app.core.search_client import _client as meili_client, _INDEX_NAME as MEILI_PRODUCTS_INDEX
from app.routers import products, categories

app = FastAPI(title="Catalogue Service", version="0.1.0")

app.include_router(products.router)
app.include_router(categories.router)


@app.on_event("startup")
def on_startup():
    # Matches document-service's current approach: create_all() for now,
    # Alembic migrations to be introduced properly in a later session.
    Base.metadata.create_all(bind=engine)
    ensure_bucket_exists()

    try:
        meili_client.index(MEILI_PRODUCTS_INDEX).update_filterable_attributes(["category"])
    except Exception:
        pass


@app.get("/health")
def health():
    return {"status": "ok"}
