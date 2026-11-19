"""
Loop iteration 19 — department (group) data separation in catalogue-service:
products belong to one group; categories stay shared.

Real HTTP API + ORM on SQLite; search indexing and storage are stubbed. Each
request carries the gateway's X-Group-Ids / X-Is-Superuser headers.
"""
import importlib.util
import io
import pathlib
import uuid

import openpyxl
import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.core.config import settings
from app.core.db import Base, get_db
from app.main import app
from app.routers import products as products_router

OPS, CITY = uuid.uuid4(), uuid.uuid4()
SECRET = {"X-Internal-Secret": settings.internal_shared_secret}
AS_OPS = {**SECRET, "X-Group-Ids": str(OPS), "X-Is-Superuser": "0"}
AS_CITY = {**SECRET, "X-Group-Ids": str(CITY), "X-Is-Superuser": "0"}
AS_BOTH = {**SECRET, "X-Group-Ids": f"{OPS},{CITY}", "X-Is-Superuser": "0"}
AS_NOBODY = {**SECRET, "X-Group-Ids": "", "X-Is-Superuser": "0"}
AS_SUPER = {**SECRET, "X-Group-Ids": f"{OPS},{CITY}", "X-Is-Superuser": "1"}
INTERNAL = {**SECRET, "X-Is-Superuser": "1"}  # what document-service sends for its own export lookups


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def ctx(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    # pysqlite's own transaction handling breaks SAVEPOINT (the SKU / doc-number
    # retry uses one); SQLAlchemy's documented fix, so SQLite behaves like Postgres.
    event.listen(engine, "connect", lambda dbapi_conn, rec: setattr(dbapi_conn, "isolation_level", None))
    event.listen(engine, "begin", lambda conn: conn.exec_driver_sql("BEGIN"))
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def _get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    uploads = {}
    app.dependency_overrides[get_db] = _get_db
    monkeypatch.setattr(products_router, "index_product", lambda p: None)
    monkeypatch.setattr(products_router, "remove_product_from_index", lambda pid: None)
    monkeypatch.setattr(products_router, "rebuild_index", lambda rows: len(rows))
    monkeypatch.setattr(products_router, "upload_file", lambda f, key, content_type=None: uploads.__setitem__(key, f.read()))
    monkeypatch.setattr(products_router, "get_presigned_url", lambda key, force_download=False: f"https://minio.example/{key}")
    monkeypatch.setattr(products_router, "download_file_bytes", lambda key: uploads[key])
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def _product(client, headers, name="Chair", **body):
    r = client.post("/products", json={"name": name, **body}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_products_are_owned_and_invisible_to_other_groups(ctx):
    client, _ = ctx
    p = _product(client, AS_OPS)
    assert p["group_id"] == str(OPS)
    pid = p["id"]
    client.post(f"/products/{pid}/file", files={"file": ("a.pdf", b"%PDF-1.4 x", "application/pdf")}, headers=AS_OPS)

    def ids(headers, path="/products"):
        return {r["id"] for r in client.get(path, headers=headers).json()}

    assert ids(AS_OPS) == ids(AS_BOTH) == ids(AS_SUPER) == {pid}
    assert ids(AS_CITY) == ids(AS_NOBODY) == ids(SECRET) == set()   # SECRET only: scope headers missing -> nothing
    for method, url, kw in (
        ("get", f"/products/{pid}", {}),
        ("patch", f"/products/{pid}", {"json": {"name": "hijacked"}}),
        ("patch", f"/products/{pid}/trash", {}),
        ("patch", f"/products/{pid}/restore", {}),
        ("get", f"/products/{pid}/file-url", {}),
        ("get", f"/products/{pid}/file-content", {}),
        ("get", f"/products/{pid}/sub-items", {}),
        ("get", f"/products/{pid}/download-bundle", {}),
        ("post", f"/products/{pid}/file", {"files": {"file": ("b.pdf", b"%PDF-1.4 y", "application/pdf")}}),
        ("delete", f"/products/{pid}", {}),
    ):
        assert getattr(client, method)(url, headers=AS_CITY, **kw).status_code == 404, url
    assert client.get(f"/products/{pid}/file-url", headers=AS_OPS).status_code == 200
    assert client.get(f"/products/{pid}/file-content", headers=INTERNAL).status_code == 200  # export path

    client.patch(f"/products/{pid}/trash", headers=AS_OPS)
    assert ids(AS_OPS, "/products/trash") == {pid} and ids(AS_CITY, "/products/trash") == set()


def test_create_rules(ctx):
    client, _ = ctx
    assert client.post("/products", json={"name": "X"}, headers=AS_NOBODY).status_code == 403
    assert client.post("/products", json={"name": "X"}, headers=SECRET).status_code == 403
    assert client.post("/products", json={"name": "X"}, headers=AS_BOTH).status_code == 422
    assert client.post("/products", json={"name": "X"}, headers=AS_SUPER).status_code == 422
    assert client.post("/products", json={"name": "X", "group_id": str(CITY)}, headers=AS_OPS).status_code == 403
    assert _product(client, AS_SUPER, "Y", group_id=str(CITY))["group_id"] == str(CITY)


def test_duplicates_are_per_group(ctx):
    client, _ = ctx
    _product(client, AS_OPS, "Desk", description="Oak")
    # the same item in another department is not a duplicate
    _product(client, AS_CITY, "Desk", description="Oak")
    assert client.post("/products", json={"name": "Desk", "description": "Oak"}, headers=AS_OPS).status_code == 409
    check = client.get("/products/duplicate-check", params={"name": "Desk", "description": "Oak"}, headers=AS_CITY).json()
    assert len(check["exact"]) == 1
    assert client.get("/products/duplicates", headers=AS_SUPER).json()["exact_groups"] == []
    assert client.get("/products/duplicate-check", params={"name": "Desk", "description": "Oak"}, headers=AS_NOBODY).json()["exact"] == []


def test_sub_items_stay_in_one_group(ctx):
    client, _ = ctx
    parent = _product(client, AS_SUPER, "Kit", group_id=str(OPS))
    ops_part = _product(client, AS_SUPER, "Bolt", group_id=str(OPS))
    city_part = _product(client, AS_SUPER, "Nut", group_id=str(CITY))
    r = client.post(f"/products/{parent['id']}/sub-items", json={"product_id": city_part["id"]}, headers=AS_SUPER)
    assert r.status_code == 422
    assert client.post(f"/products/{parent['id']}/sub-items", json={"product_id": city_part["id"]}, headers=AS_OPS).status_code == 404
    assert client.post(f"/products/{parent['id']}/sub-items", json={"product_id": ops_part["id"]}, headers=AS_OPS).status_code == 201
    assert [s["name"] for s in client.get(f"/products/{parent['id']}/sub-items", headers=AS_OPS).json()] == ["Bolt"]


def _xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Name", "Category", "Price", "Description"])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_bulk_import_assigns_the_importers_group(ctx):
    client, _ = ctx
    _product(client, AS_CITY, "Lamp", description="Brass")
    sheet = ("p.xlsx", _xlsx([["Lamp", None, 5, "Brass"], ["Fan", None, 9, None]]), "application/octet-stream")
    r = client.post("/products/bulk-import", files={"excel_file": sheet}, headers=AS_OPS)
    assert r.status_code == 200, r.text
    assert len(r.json()["created"]) == 2 and r.json()["skipped"] == []   # CityMart's Lamp is not a duplicate for Ops
    assert {p["group_id"] for p in client.get("/products", headers=AS_OPS).json()} == {str(OPS)}
    assert client.post("/products/bulk-import", files={"excel_file": sheet}, headers=AS_BOTH).status_code == 422
    r = client.post("/products/bulk-import", files={"excel_file": sheet}, data={"group_id": str(CITY)}, headers=AS_SUPER)
    assert r.status_code == 200 and [s["name"] for s in r.json()["skipped"]] == ["Lamp"]


def test_delete_all_only_touches_the_callers_groups(ctx):
    client, _ = ctx
    _product(client, AS_OPS, "A")
    _product(client, AS_CITY, "B")
    assert client.delete("/products", headers=AS_OPS).json() == {"deleted": 1}
    assert len(client.get("/products", headers=AS_CITY).json()) == 1


def test_categories_stay_shared(ctx):
    client, _ = ctx
    hdr = {"X-Has-Category-Access": "true"}
    r = client.post("/categories", json={"name": "Furniture", "short_term": "FUR"}, headers={**AS_OPS, **hdr})
    assert r.status_code in (200, 201), r.text
    assert "Furniture" in {c["name"] for c in client.get("/categories", headers=AS_CITY).json()}
    # SKU numbering is shared too (sku is unique across groups)
    a = _product(client, AS_OPS, "Sofa", category="Furniture")
    b = _product(client, AS_CITY, "Stool", category="Furniture")
    assert (a["sku"], b["sku"]) == ("FUR-0001", "FUR-0002")


def test_assign_unassigned(ctx):
    client, Session = ctx
    db = Session()
    db.add(models.Product(sku="OLD-1", name="Old product"))
    db.commit()
    assert client.get("/products", headers=AS_OPS).json() == []
    assert len(client.get("/products", headers=AS_SUPER).json()) == 1
    assert client.post("/products/assign-unassigned", json={"group_id": str(OPS)}, headers=AS_OPS).status_code == 403
    r = client.post("/products/assign-unassigned", json={"group_id": str(OPS)}, headers=AS_SUPER)
    assert r.json() == {"products": 1, "reindexed": 1}
    assert len(client.get("/products", headers=AS_OPS).json()) == 1
    assert client.post("/products/assign-unassigned", json={"group_id": str(OPS)}, headers=AS_SUPER).json()["products"] == 0


_VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"


def test_alembic_single_head_and_0001_migration():
    # Since iteration 21 there is a second revision; the single head is checked
    # in tests/test_concurrency.py::test_single_alembic_head.
    files = [_VERSIONS / "0001_product_group_id.py"]
    spec = importlib.util.spec_from_file_location("c0001", files[0])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.down_revision is None
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, name VARCHAR)"))
        conn.execute(text("INSERT INTO products (name) VALUES ('old')"))
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
            mod.upgrade()
            assert "group_id" in {c["name"] for c in inspect(conn).get_columns("products")}
            assert conn.execute(text("SELECT group_id FROM products")).scalar() is None
            mod.downgrade()
            assert {c["name"] for c in inspect(conn).get_columns("products")} == {"id", "name"}
