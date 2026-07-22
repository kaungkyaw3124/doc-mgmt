from fastapi import FastAPI

from app.core.db import Base, engine
from app.core.storage import ensure_bucket_exists
from app.core.search_client import _client as meili_client, _INDEX_NAME as MEILI_DOCS_INDEX
from app.routers import documents, customers, companies, projects

app = FastAPI(title="Document Service", version="0.1.0")

app.include_router(documents.router)
app.include_router(customers.router)
app.include_router(companies.router)
app.include_router(projects.router)


@app.on_event("startup")
def on_startup():
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
