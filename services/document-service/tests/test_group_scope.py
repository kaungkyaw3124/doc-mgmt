"""
Loop iteration 19 — department (group) data separation in document-service.

Real HTTP API + ORM on SQLite. Each request carries the gateway's scope
headers (X-Group-Ids / X-Is-Superuser) the way Nginx would forward them from
auth-service's /verify; search indexing, the audit log, storage and the
catalogue are stubbed.
"""
import importlib.util
import io
import pathlib
import uuid

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.core import group_scope
from app.core.config import settings
from app.core.db import Base, get_db
from app.main import app
from app.routers import documents as documents_router

OPS, CITY = uuid.uuid4(), uuid.uuid4()
SECRET = {"X-Internal-Secret": settings.internal_shared_secret}
AS_OPS = {**SECRET, "X-Group-Ids": str(OPS), "X-Is-Superuser": "0"}
AS_CITY = {**SECRET, "X-Group-Ids": str(CITY), "X-Is-Superuser": "0"}
AS_BOTH = {**SECRET, "X-Group-Ids": f"{OPS},{CITY}", "X-Is-Superuser": "0"}
AS_NOBODY = {**SECRET, "X-Group-Ids": "", "X-Is-Superuser": "0"}
AS_SUPER = {**SECRET, "X-Group-Ids": f"{OPS},{CITY}", "X-Is-Superuser": "1"}
NO_SCOPE = dict(SECRET)  # headers missing altogether
DELETE_OK = {"X-Has-Document-Delete": "true", "X-Has-Audit-Log": "true"}


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

    files = {}
    app.dependency_overrides[get_db] = _get_db
    monkeypatch.setattr(documents_router, "index_document", lambda doc: None)
    monkeypatch.setattr(documents_router, "remove_document_from_index", lambda doc_id: None)
    monkeypatch.setattr(documents_router, "rebuild_index", lambda rows: len(rows))
    monkeypatch.setattr(documents_router, "log_action", lambda *a, **k: None)
    monkeypatch.setattr(documents_router, "upload_file", lambda f, key, content_type=None: files.__setitem__(key, f.read()))
    monkeypatch.setattr(documents_router, "get_presigned_url", lambda key, force_download=False: f"https://minio.example/{key}")
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def _create(client, path, headers, **body):
    defaults = {"/customers": {"name": "Buyer"}, "/projects": {"name": "Project"},
                "/documents": {"doc_type": "quotation", "items": [{"description": "Widget", "quantity": "1", "unit_price": "5"}]}}
    r = client.post(path, json={**defaults[path], **body}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# ---- the helper ------------------------------------------------------------

def test_scope_parsing_fails_closed():
    assert group_scope.get_scope(None, None) == group_scope.Scope(False, frozenset())
    assert group_scope.get_scope("not-a-uuid, ,", "0").group_ids == frozenset()
    assert group_scope.get_scope(f"{OPS}", "yes").superuser is False  # only an exact "1" counts
    assert group_scope.get_scope(f"{OPS},{CITY}", "1") == group_scope.Scope(True, frozenset({OPS, CITY}))


@pytest.mark.parametrize("scope,requested,expected", [
    (group_scope.Scope(False, frozenset({OPS})), None, OPS),          # one group -> that one
    (group_scope.Scope(False, frozenset({OPS, CITY})), CITY, CITY),   # picked one of theirs
    (group_scope.Scope(True, frozenset({OPS, CITY})), OPS, OPS),
])
def test_group_for_create_picks_the_owner(scope, requested, expected):
    assert group_scope.group_for_create(scope, requested) == expected


@pytest.mark.parametrize("scope,requested,code", [
    (group_scope.Scope(False, frozenset()), None, 403),               # in no group
    (group_scope.Scope(False, frozenset({OPS, CITY})), None, 422),    # several groups, none chosen
    (group_scope.Scope(True, frozenset({OPS})), None, 422),           # superuser must choose
    (group_scope.Scope(False, frozenset({OPS})), CITY, 403),          # not their group
    (group_scope.Scope(True, frozenset({OPS})), uuid.uuid4(), 422),   # unknown/disabled group
])
def test_group_for_create_refuses(scope, requested, code):
    with pytest.raises(HTTPException) as e:
        group_scope.group_for_create(scope, requested)
    assert e.value.status_code == code


# ---- isolation through the API ---------------------------------------------

@pytest.mark.parametrize("path", ["/customers", "/projects", "/documents"])
def test_records_are_owned_by_the_creators_group_and_invisible_to_others(ctx, path):
    client, _ = ctx
    row = _create(client, path, AS_OPS)
    assert row["group_id"] == str(OPS)
    rid = row["id"]

    def ids(headers, suffix=""):
        return {r["id"] for r in client.get(path + suffix, headers=headers).json()}

    assert ids(AS_OPS) == {rid} and ids(AS_BOTH) == {rid} and ids(AS_SUPER) == {rid}
    assert ids(AS_CITY) == set() and ids(AS_NOBODY) == set() and ids(NO_SCOPE) == set()

    # direct id access from another group: 404, never 403 (existence not revealed)
    hdr = {**AS_CITY, **DELETE_OK}
    assert client.get(f"{path}/{rid}", headers=hdr).status_code == 404
    patch_body = {"items": []} if path == "/documents" else {"name": "hijacked"}
    assert client.patch(f"{path}/{rid}", json=patch_body, headers=hdr).status_code == 404
    assert client.patch(f"{path}/{rid}/trash", headers=hdr).status_code == 404
    assert client.patch(f"{path}/{rid}/restore", headers=hdr).status_code == 404
    assert client.delete(f"{path}/{rid}", headers=hdr).status_code == 404
    assert client.get(f"{path}/{rid}", headers={**NO_SCOPE, **DELETE_OK}).status_code == 404

    # recycle bin: also per group
    assert client.patch(f"{path}/{rid}/trash", headers={**AS_OPS, **DELETE_OK}).status_code == 200
    assert ids(AS_OPS, "/trash") == {rid} and ids(AS_CITY, "/trash") == set() and ids(AS_SUPER, "/trash") == {rid}
    assert client.get(f"{path}/{rid}", headers=AS_OPS).json()["name" if path != "/documents" else "doc_type"] != "hijacked"


def test_document_extras_are_scoped(ctx):
    client, _ = ctx
    customer = _create(client, "/customers", AS_OPS)
    project = _create(client, "/projects", AS_OPS)
    doc = _create(client, "/documents", AS_OPS, customer_id=customer["id"], project_id=project["id"])
    did = doc["id"]
    client.post(f"/documents/{did}/file", files={"file": ("a.txt", b"hello", "text/plain")}, headers=AS_OPS)
    city = {**AS_CITY, **DELETE_OK}

    for url in (f"/documents/{did}/export/quotation-pdf", f"/documents/{did}/export/quotation",
                f"/documents/{did}/file-url", f"/documents/{did}/audit-log", f"/documents/{did}/export/catalogue"):
        assert client.get(url, headers=city).status_code == 404, url
    assert client.get(f"/documents/{did}/export/quotation-pdf", headers=AS_OPS).content[:4] == b"%PDF"
    assert client.get(f"/documents/{did}/file-url", headers=AS_OPS).status_code == 200
    assert client.get(f"/documents/{did}/audit-log", headers={**AS_OPS, **DELETE_OK}).status_code == 200
    assert client.patch(f"/documents/{did}/status", params={"new_status": "sent"}, headers=city).status_code == 404
    assert client.post(f"/documents/{did}/file", files={"file": ("b.txt", b"x", "text/plain")}, headers=city).status_code == 404

    assert client.get(f"/documents/by-customer/{customer['id']}", headers=AS_CITY).json() == []
    assert len(client.get(f"/documents/by-customer/{customer['id']}", headers=AS_OPS).json()) == 1
    assert client.get(f"/projects/{project['id']}/documents", headers=AS_CITY).status_code == 404
    assert len(client.get(f"/projects/{project['id']}/documents", headers=AS_OPS).json()) == 1


def test_by_product_lists_only_the_callers_documents(ctx, monkeypatch):
    client, _ = ctx
    pid = uuid.uuid4()
    monkeypatch.setattr(documents_router, "get_product", lambda product_id, scope=None: {"name": "P", "unit_price": 1})
    _create(client, "/documents", AS_OPS, items=[{"product_id": str(pid), "quantity": "1"}])
    _create(client, "/documents", AS_CITY, items=[{"product_id": str(pid), "quantity": "1"}])
    assert len(client.get(f"/documents/by-product/{pid}", headers=AS_OPS).json()) == 1
    assert len(client.get(f"/documents/by-product/{pid}", headers=AS_SUPER).json()) == 2
    assert client.get(f"/documents/by-product/{pid}", headers=AS_NOBODY).json() == []


# ---- create rules ------------------------------------------------------------

def test_create_rules(ctx):
    client, _ = ctx
    for path in ("/customers", "/projects", "/documents"):
        body = {"/customers": {"name": "X"}, "/projects": {"name": "X"}, "/documents": {"doc_type": "quotation"}}[path]
        assert client.post(path, json=body, headers=AS_NOBODY).status_code == 403
        assert "not in any group" in client.post(path, json=body, headers=NO_SCOPE).json()["detail"]
        assert client.post(path, json=body, headers=AS_BOTH).status_code == 422                   # must choose
        assert client.post(path, json={**body, "group_id": str(CITY)}, headers=AS_OPS).status_code == 403
        assert client.post(path, json=body, headers=AS_SUPER).status_code == 422                  # superuser too
        r = client.post(path, json={**body, "group_id": str(CITY)}, headers=AS_BOTH)
        assert r.status_code == 201 and r.json()["group_id"] == str(CITY)
        r = client.post(path, json={**body, "group_id": str(OPS)}, headers=AS_SUPER)
        assert r.status_code == 201 and r.json()["group_id"] == str(OPS)


def test_no_cross_group_links(ctx):
    client, _ = ctx
    city_customer = _create(client, "/customers", AS_CITY)
    city_project = _create(client, "/projects", AS_CITY)
    ops_customer = _create(client, "/customers", AS_OPS)
    # not visible to an Operations user at all
    r = client.post("/documents", json={"doc_type": "quotation", "customer_id": city_customer["id"]}, headers=AS_OPS)
    assert r.status_code == 400
    # visible (superuser / both groups) but in another group than the document
    for headers in (AS_SUPER, AS_BOTH):
        for field, row in (("customer_id", city_customer), ("project_id", city_project)):
            r = client.post("/documents", json={"doc_type": "quotation", "group_id": str(OPS), field: row["id"]}, headers=headers)
            assert r.status_code == 422 and "another group" in r.json()["detail"]
    doc = _create(client, "/documents", AS_OPS, customer_id=ops_customer["id"])
    r = client.patch(f"/documents/{doc['id']}", json={"customer_id": city_customer["id"], "items": []}, headers=AS_SUPER)
    assert r.status_code == 422
    assert client.patch(f"/documents/{doc['id']}", json={"customer_id": ops_customer["id"], "items": []}, headers=AS_OPS).status_code == 200


def test_document_with_a_deleted_or_foreign_product_still_saves_and_exports(ctx, monkeypatch):
    """Line items keep their own description/price snapshot; the product
    lookup is only needed when the item has none."""
    from app.core.catalogue_client import ProductNotFoundError

    client, _ = ctx
    calls = []

    def missing(product_id, scope=None):
        calls.append(scope)
        raise ProductNotFoundError(f"product {product_id} not found in catalogue")

    monkeypatch.setattr(documents_router, "get_product", missing)
    pid = str(uuid.uuid4())
    doc = _create(client, "/documents", AS_OPS, items=[{"product_id": pid, "description": "Snapshot name", "quantity": "2", "unit_price": "7"}])
    assert calls == []  # snapshot given: no lookup at all
    assert client.patch(f"/documents/{doc['id']}", json={"items": doc["items"]}, headers=AS_OPS).status_code == 200
    assert b"%PDF" == client.get(f"/documents/{doc['id']}/export/quotation-pdf", headers=AS_OPS).content[:4]
    assert client.get(f"/documents/{doc['id']}/export/quotation", headers=AS_OPS).status_code == 200
    # without a snapshot the lookup runs AS THE CALLER, and a product they can't see is refused
    r = client.post("/documents", json={"doc_type": "quotation", "items": [{"product_id": pid, "quantity": "1"}]}, headers=AS_OPS)
    assert r.status_code == 400 and calls[-1] == group_scope.Scope(False, frozenset({OPS}))


def test_numbering_stays_per_company_across_groups(ctx):
    client, _ = ctx
    company = client.post("/companies", json={"name": "Shared Co", "short_name": "SC"}, headers=AS_OPS).json()
    # companies are shared: both groups see it
    assert company["id"] in {c["id"] for c in client.get("/companies", headers=AS_CITY).json()}
    a = _create(client, "/documents", AS_OPS, company_id=company["id"])
    b = _create(client, "/documents", AS_CITY, company_id=company["id"])
    assert a["doc_number"].endswith("/001") and b["doc_number"].endswith("/002")


def test_delete_all_only_touches_the_callers_groups(ctx, monkeypatch):
    client, _ = ctx
    monkeypatch.setattr("app.core.dev_delete_all.delete_files", lambda keys: list(keys))
    for path in ("/customers", "/projects", "/documents"):
        _create(client, path, AS_OPS)
        _create(client, path, AS_CITY)
    for path in ("/documents", "/customers", "/projects"):
        assert client.delete(path, headers={**AS_OPS, **DELETE_OK}).json() == {"deleted": 1}
        assert len(client.get(path, headers=AS_CITY).json()) == 1
        assert client.delete(path, headers={**AS_NOBODY, **DELETE_OK}).json() == {"deleted": 0}


# ---- existing data: unassigned rows + assign-unassigned ---------------------

def test_unassigned_rows_are_superuser_only_until_assigned(ctx):
    client, Session = ctx
    db = Session()
    db.add_all([models.Customer(name="Old customer"), models.Project(name="Old project"),
                models.Document(doc_type="quotation", doc_number="OLD-1", status="draft", currency="USD")])
    db.commit()
    for path in ("/customers", "/projects", "/documents"):
        assert client.get(path, headers=AS_OPS).json() == []
        assert len(client.get(path, headers=AS_SUPER).json()) == 1

    assert client.post("/documents/assign-unassigned", json={"group_id": str(OPS)}, headers=AS_OPS).status_code == 403
    assert client.post("/documents/assign-unassigned", json={"group_id": str(uuid.uuid4())}, headers=AS_SUPER).status_code == 422
    r = client.post("/documents/assign-unassigned", json={"group_id": str(OPS)}, headers=AS_SUPER)
    assert r.json() == {"documents": 1, "customers": 1, "projects": 1, "reindexed": 1}
    for path in ("/customers", "/projects", "/documents"):
        assert len(client.get(path, headers=AS_OPS).json()) == 1 and client.get(path, headers=AS_CITY).json() == []
    # a second run is harmless and leaves owned rows alone
    _create(client, "/customers", AS_CITY)
    r = client.post("/documents/assign-unassigned", json={"group_id": str(OPS)}, headers=AS_SUPER)
    assert r.json() == {"documents": 0, "customers": 0, "projects": 0, "reindexed": 1}
    assert len(client.get("/customers", headers=AS_CITY).json()) == 1


_MIGRATION = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0009_group_id.py"


def test_0009_migration_keeps_rows_null_and_runs_twice_and_down():
    spec = importlib.util.spec_from_file_location("m0009", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.down_revision == "0008_company_letterhead"
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for table in mod.TABLES:
            conn.execute(text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, name VARCHAR)"))
            conn.execute(text(f"INSERT INTO {table} (name) VALUES ('old')"))
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
            mod.upgrade()  # guarded: a second run is a no-op
            for table in mod.TABLES:
                assert "group_id" in {c["name"] for c in inspect(conn).get_columns(table)}
                assert f"ix_{table}_group_id" in {i["name"] for i in inspect(conn).get_indexes(table)}
                assert conn.execute(text(f"SELECT group_id FROM {table}")).scalar() is None  # nothing backfilled
            mod.downgrade()
            for table in mod.TABLES:
                assert {c["name"] for c in inspect(conn).get_columns(table)} == {"id", "name"}
