"""
Loop iteration 15 — DEV-ONLY "Delete ALL" for documents, customers, projects
and companies. Delete this file (and app/core/dev_delete_all.py) together with
the DEV-ONLY blocks when the feature goes.

Same harness as test_supplier_position_e2e.py: real HTTP API + ORM on SQLite,
search index and object storage stubbed.
"""
from datetime import date

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
from app.routers import documents as documents_router

EDIT = {"X-Has-Document-Delete": "true"}


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
    unindexed, deleted_keys = [], []
    monkeypatch.setattr(documents_router, "index_document", lambda doc: None)
    monkeypatch.setattr(documents_router, "log_action", lambda *a, **k: None)
    monkeypatch.setattr(documents_router, "remove_document_from_index", lambda d: unindexed.append(str(d)))
    monkeypatch.setattr(storage.s3_client, "delete_object", lambda Bucket, Key: deleted_keys.append(Key))
    monkeypatch.setattr(settings, "environment", "development")
    client = TestClient(app, headers={"X-Internal-Secret": settings.internal_shared_secret})
    yield client, Session, unindexed, deleted_keys
    app.dependency_overrides.clear()


def _seed(client, Session):
    """2 companies (one trashed, one with an MD + files), a customer, a project,
    3 documents linked to them (one trashed, one with line items, one with a file)."""
    co = client.post("/companies", json={"name": "Wipe Co", "short_name": "WC"}).json()["id"]
    co2 = client.post("/companies", json={"name": "Trashed Co", "short_name": "TC"}).json()["id"]
    assert client.patch(f"/companies/{co2}/trash").status_code == 200
    md = client.post(f"/companies/{co}/directors", json={"name": "MD", "position": "Managing Director"}).json()["id"]
    cust = client.post("/customers", json={"name": "Wipe Cust"}).json()["id"]
    proj = client.post("/projects", json={"name": "Wipe Proj"}).json()["id"]
    docs = []
    for i in range(3):
        r = client.post("/documents", json={
            "doc_type": "quotation", "company_id": co, "director_id": md, "customer_id": cust, "project_id": proj,
            "issue_date": str(date(2026, 11, 14)),
            "items": [{"description": f"item {i}", "quantity": 1, "unit_price": 10}],
        })
        assert r.status_code == 201, r.text
        docs.append(r.json()["id"])
    with Session() as db:
        db.query(models.Company).filter_by(name="Wipe Co").update({"logo_object_key": "logo.png", "seal_object_key": "seal.png"})
        db.query(models.CompanyDirector).update({"seal_object_key": "md-seal.png"})
        first = db.query(models.Document).first()
        first.file_object_key = "quotation/x.pdf"
        db.add(models.AuditLogEntry(document_id=first.id, username="admin", action="viewed"))
        db.commit()
    assert client.patch(f"/documents/{docs[0]}/trash", headers=EDIT).status_code == 200
    return docs


def _count(Session, model):
    with Session() as db:
        return db.query(model).count()


def test_delete_all_documents_removes_items_audit_index_and_files(ctx):
    client, Session, unindexed, deleted_keys = ctx
    docs = _seed(client, Session)
    assert _count(Session, models.LineItem) == 3 and _count(Session, models.AuditLogEntry) >= 1

    r = client.delete("/documents", headers=EDIT)
    assert r.status_code == 200 and r.json() == {"deleted": 3}
    assert client.get("/documents").json() == [] and client.get("/documents/trash").json() == []
    assert _count(Session, models.LineItem) == 0 and _count(Session, models.AuditLogEntry) == 0
    assert sorted(unindexed) == sorted(docs)
    assert deleted_keys == ["quotation/x.pdf"]
    # no cross-delete
    assert _count(Session, models.Customer) == 1 and _count(Session, models.Company) == 2


def test_delete_all_documents_needs_delete_permission(ctx):
    client, Session, _, _ = ctx
    _seed(client, Session)
    assert client.delete("/documents").status_code == 403
    assert _count(Session, models.Document) == 3


@pytest.mark.parametrize("path,model,col", [
    ("/customers", models.Customer, "customer_id"),
    ("/projects", models.Project, "project_id"),
])
def test_delete_all_customers_projects_keeps_documents(ctx, path, model, col):
    client, Session, _, _ = ctx
    _seed(client, Session)
    r = client.delete(path)
    assert r.status_code == 200 and r.json() == {"deleted": 1}
    assert client.get(path).json() == [] and client.get(f"{path}/trash").json() == []
    with Session() as db:
        assert db.query(models.Document).count() == 3
        assert all(getattr(d, col) is None for d in db.query(models.Document))


def test_delete_all_companies_clears_refs_directors_and_files(ctx):
    client, Session, _, deleted_keys = ctx
    _seed(client, Session)
    r = client.delete("/companies")
    assert r.status_code == 200 and r.json() == {"deleted": 2}
    assert client.get("/companies").json() == [] and client.get("/companies/trash").json() == []
    assert _count(Session, models.CompanyDirector) == 0
    with Session() as db:
        assert db.query(models.Document).count() == 3
        assert all(d.company_id is None and d.director_id is None for d in db.query(models.Document))
    assert sorted(deleted_keys) == ["logo.png", "md-seal.png", "seal.png"]


ALL = ["/documents", "/customers", "/projects", "/companies"]
MODELS = [models.Document, models.Customer, models.Project, models.Company]


@pytest.mark.parametrize("path", ALL)
def test_refused_in_production(ctx, monkeypatch, path):
    client, Session, _, _ = ctx
    _seed(client, Session)
    monkeypatch.setattr(settings, "environment", "production")
    assert client.delete(path, headers=EDIT).status_code == 403
    assert [_count(Session, m) for m in MODELS] == [3, 1, 1, 2]


@pytest.mark.parametrize("path", ALL)
def test_refused_for_view_only(ctx, path):
    client, Session, _, _ = ctx
    _seed(client, Session)
    assert client.delete(path, headers={**EDIT, "X-Access-Level": "view"}).status_code == 403
    assert [_count(Session, m) for m in MODELS] == [3, 1, 1, 2]


@pytest.mark.parametrize("path", ALL)
def test_empty_table(ctx, path):
    client, _, _, _ = ctx
    assert client.delete(path, headers=EDIT).json() == {"deleted": 0}


def test_storage_error_does_not_fail(ctx, monkeypatch):
    client, Session, _, _ = ctx
    _seed(client, Session)

    def boom(Bucket, Key):
        raise RuntimeError("minio down")

    monkeypatch.setattr(storage.s3_client, "delete_object", boom)
    assert client.delete("/documents", headers=EDIT).json() == {"deleted": 3}
    assert client.delete("/companies").json() == {"deleted": 2}
