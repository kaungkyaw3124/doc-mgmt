"""
Loop iteration 15 — DEV-ONLY "Delete ALL products" (DELETE /products).
Delete this file together with the DEV-ONLY blocks when the feature goes.

Same harness as test_duplicates.py: real HTTP API + ORM on SQLite, search
index and object storage stubbed.
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.core import storage
from app.core.config import settings
from app.core.db import Base, get_db
from app.main import app
from app.routers import products as products_router


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def ctx(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def _get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    removed, deleted_keys = [], []
    monkeypatch.setattr(products_router, "index_product", lambda p: None)
    monkeypatch.setattr(products_router, "remove_product_from_index", lambda pid: removed.append(str(pid)))
    monkeypatch.setattr(storage.s3_client, "delete_object", lambda Bucket, Key: deleted_keys.append(Key))
    monkeypatch.setattr(settings, "environment", "development")
    client = TestClient(app, headers={"X-Internal-Secret": settings.internal_shared_secret})
    yield client, Session, removed, deleted_keys
    app.dependency_overrides.clear()


def _seed(client, Session):
    """3 products: one in the recycle bin, one with a sub-item (the third),
    one with a stored file."""
    ids = [client.post("/products", json={"name": f"Wipe me {i}"}).json()["id"] for i in range(3)]
    assert client.patch(f"/products/{ids[0]}/trash").status_code == 200
    assert client.post(f"/products/{ids[1]}/sub-items", json={"product_id": ids[2]}).status_code in (200, 201)
    with Session() as db:
        db.get(models.Product, uuid.UUID(ids[2])).image_object_key = "products/x/file.pdf"
        db.add(models.Category(name="Keep me"))
        db.commit()
    return ids


def _counts(Session):
    with Session() as db:
        return db.query(models.Product).count(), db.query(models.ProductSubItem).count(), db.query(models.Category).count()


def test_deletes_everything_but_categories(ctx):
    client, Session, removed, deleted_keys = ctx
    ids = _seed(client, Session)
    assert _counts(Session) == (3, 1, 1)

    resp = client.delete("/products")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": 3}
    assert client.get("/products").json() == []
    assert client.get("/products/trash").json() == []
    assert _counts(Session) == (0, 0, 1)
    assert sorted(removed) == sorted(ids)
    assert deleted_keys == ["products/x/file.pdf"]


def test_storage_error_does_not_fail_the_request(ctx, monkeypatch):
    client, Session, _, _ = ctx
    _seed(client, Session)

    def boom(Bucket, Key):
        raise RuntimeError("minio down")
    monkeypatch.setattr(storage.s3_client, "delete_object", boom)
    resp = client.delete("/products")
    assert resp.status_code == 200 and resp.json() == {"deleted": 3}
    assert _counts(Session)[0] == 0


def test_empty_catalogue(ctx):
    client, *_ = ctx
    assert client.delete("/products").json() == {"deleted": 0}


def test_refused_in_production(ctx, monkeypatch):
    client, Session, removed, _ = ctx
    _seed(client, Session)
    monkeypatch.setattr(settings, "environment", "production")
    resp = client.delete("/products")
    assert resp.status_code == 403
    assert _counts(Session) == (3, 1, 1) and removed == []
    assert client.get("/products/dev-config").json() == {"delete_all_enabled": False}


def test_refused_for_view_only(ctx):
    client, Session, removed, _ = ctx
    _seed(client, Session)
    resp = client.delete("/products", headers={"X-Access-Level": "view"})
    assert resp.status_code == 403
    assert _counts(Session) == (3, 1, 1) and removed == []


def test_dev_config_enabled_outside_production(ctx):
    client, *_ = ctx
    assert client.get("/products/dev-config").json() == {"delete_all_enabled": True}
