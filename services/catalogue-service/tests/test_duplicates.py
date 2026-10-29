"""
Loop iteration 8 — duplicate products: exact duplicates (same normalized
name + description) are blocked on create / edit / restore and skipped in
bulk import; same-name and similar names are reported, not blocked.

Real HTTP API + ORM on SQLite (JSONB mapped to JSON); search indexing and
object storage are stubbed. No X-Allowed-Projects header -> unrestricted,
so get_visible_product_ids makes no network call.
"""
import io
import zipfile

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.core.config import settings
from app.core.db import Base, get_db
from app.core.duplicates import norm
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

    uploads = {}
    app.dependency_overrides[get_db] = _get_db
    monkeypatch.setattr(products_router, "index_product", lambda p: None)
    monkeypatch.setattr(products_router, "upload_file", lambda f, key, content_type=None: uploads.__setitem__(key, f.read()))
    # no `with`: skips startup (create_all on Postgres, Meilisearch settings)
    client = TestClient(app, headers={"X-Internal-Secret": settings.internal_shared_secret})
    yield client, Session, uploads
    app.dependency_overrides.clear()


def _create(client, name, description=None, **extra):
    return client.post("/products", json={"name": name, "description": description, **extra})


def _ok(resp, code=201):
    assert resp.status_code == code, resp.text
    return resp.json()


# ---- norm ------------------------------------------------------------------

def test_norm():
    assert norm("  Cantilever   Shelf\t750MM ") == "cantilever shelf 750mm"
    assert norm("STRASSE") == norm("strasse")
    assert norm(None) == "" and norm("") == "" and norm("   ") == ""
    assert norm("a b") != norm("ab")  # only whitespace runs collapse; nothing else is removed


# ---- create / edit / restore --------------------------------------------------

def test_create_exact_duplicate_is_409_with_the_existing_sku(ctx):
    client, _, _ = ctx
    first = _ok(_create(client, "Office Desk", "Oak, 120cm"))
    resp = _create(client, "  office   DESK ", "oak,  120CM")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["message"] == "This item already exists"
    assert detail["existing"] == {"id": first["id"], "sku": first["sku"], "name": "Office Desk"}


def test_same_name_with_a_different_description_is_allowed(ctx):
    client, _, _ = ctx
    _ok(_create(client, "Office Desk", "Oak"))
    _ok(_create(client, "Office Desk", "Walnut"))
    _ok(_create(client, "Office Desk", None))  # blank description is its own description


def test_edit_price_on_an_existing_duplicate_pair_but_not_rename_into_one(ctx):
    client, Session, _ = ctx
    db = Session()  # a pair that predates the check, written straight to the DB
    db.add_all([models.Product(sku="PRD-0001", name="Chair", description="Red"),
                models.Product(sku="PRD-0002", name="Chair", description="Red")])
    db.commit()
    pid = db.query(models.Product).filter_by(sku="PRD-0002").one().id
    assert _ok(client.patch(f"/products/{pid}", json={"unit_price": "12.50"}), 200)["unit_price"] == "12.50"
    assert client.patch(f"/products/{pid}", json={"name": "Chair", "description": "Red"}).status_code == 200  # unchanged text

    other = _ok(_create(client, "Stool", "Red"))
    resp = client.patch(f"/products/{other['id']}", json={"name": "chair"})
    assert resp.status_code == 409 and resp.json()["detail"]["existing"]["sku"] == "PRD-0001"
    assert _ok(client.patch(f"/products/{other['id']}", json={"name": "Stool", "description": "Blue"}), 200)


def test_restore_into_an_exact_duplicate_is_409(ctx):
    client, _, _ = ctx
    old = _ok(_create(client, "Lamp", "Brass"))
    _ok(client.patch(f"/products/{old['id']}/trash"), 200)
    new = _ok(_create(client, "Lamp", "Brass"))  # allowed: the old one is trashed
    resp = client.patch(f"/products/{old['id']}/restore")
    assert resp.status_code == 409 and resp.json()["detail"]["existing"]["sku"] == new["sku"]
    _ok(client.patch(f"/products/{new['id']}/trash"), 200)
    _ok(client.patch(f"/products/{old['id']}/restore"), 200)  # no active twin left -> fine


# ---- duplicate-check / duplicates --------------------------------------------

def test_duplicate_check(ctx):
    client, _, _ = ctx
    shelf = _ok(_create(client, "Cantilever Shelf 750 mm", "Steel, grey", category="Storage"))
    other = _ok(_create(client, "Cantilever Shelf 750 mm", "Steel, white"))
    gone = _ok(_create(client, "Cantilever Shelf 750 mm", "Steel, grey (old)"))
    _ok(client.patch(f"/products/{gone['id']}/trash"), 200)
    _ok(_create(client, "Coffee Table", "Glass"))

    r = _ok(client.get("/products/duplicate-check", params={"name": "cantilever shelf  750 MM", "description": "steel, GREY"}), 200)
    assert [p["sku"] for p in r["exact"]] == [shelf["sku"]]
    assert r["exact"][0] == {"id": shelf["id"], "sku": shelf["sku"], "name": "Cantilever Shelf 750 mm",
                             "description": "Steel, grey", "category": "Storage"}
    assert [p["sku"] for p in r["same_name"]] == [other["sku"]]  # trashed one not returned
    assert r["similar"] == []

    r = _ok(client.get("/products/duplicate-check",
                       params={"name": "Cantilever Shelf 750 mm", "description": "Steel, grey", "exclude_id": shelf["id"]}), 200)
    assert r["exact"] == [] and [p["sku"] for p in r["same_name"]] == [other["sku"]]

    r = _ok(client.get("/products/duplicate-check", params={"name": "Cantilver Shelf 750mm"}), 200)  # typo
    assert r["exact"] == [] and r["same_name"] == []
    assert {p["sku"] for p in r["similar"]} == {shelf["sku"], other["sku"]}
    assert "Coffee Table" not in {p["name"] for p in r["similar"]}


def test_duplicates_groups(ctx):
    client, Session, _ = ctx
    db = Session()
    db.add_all([
        models.Product(sku="A-1", name="Chair", description="Red"),
        models.Product(sku="A-2", name="chair ", description="red"),
        models.Product(sku="A-3", name="Chair", description="Blue"),
        models.Product(sku="B-1", name="Bench", description="Oak"),
        models.Product(sku="B-2", name="Bench", description="Oak", is_deleted=True),  # trashed: ignored
        models.Product(sku="C-1", name="Cabinet", description="Tall"),               # singleton
    ])
    db.commit()
    r = _ok(client.get("/products/duplicates"), 200)
    assert [[p["sku"] for p in g] for g in r["exact_groups"]] == [["A-1", "A-2"]]
    assert [[p["sku"] for p in g] for g in r["same_name_groups"]] == [["A-1", "A-2", "A-3"]]


# ---- bulk import --------------------------------------------------------------

def _xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Name", "Description", "Price"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _zip(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, f"file {n}")
    return buf.getvalue()


def test_bulk_import_skips_duplicates_and_warns_on_same_name(ctx):
    client, Session, uploads = ctx
    desk = _ok(_create(client, "Office Desk", "Oak"))
    table = _ok(_create(client, "Coffee Table", "Glass"))
    rows = [
        ["office desk", "OAK", 100],       # 1: exact duplicate of an existing product
        ["Chair", "Red", 10],              # 2: created
        [" chair", "red ", 11],            # 3: duplicate of row 2
        ["Coffee Table", "Walnut", 50],    # 4: created, same name as an existing product
        ["Lamp", "Brass", 5],              # 5: created, gets file 5
    ]
    resp = client.post("/products/bulk-import", files={
        "excel_file": ("p.xlsx", _xlsx(rows), "application/octet-stream"),
        "catalogue_zip": ("c.zip", _zip(["1.pdf", "5.pdf"]), "application/zip"),
    })
    body = _ok(resp, 200)

    assert [(c["row"], c["name"]) for c in body["created"]] == [(2, "Chair"), (4, "Coffee Table"), (5, "Lamp")]
    chair_sku = body["created"][0]["sku"]
    assert body["skipped"] == [
        {"row": 1, "name": "office desk", "reason": "already exists", "existing_sku": desk["sku"]},
        {"row": 3, "name": "chair", "reason": "duplicate of row 2", "existing_sku": chair_sku},
    ]
    assert {"row": 4, "name": "Coffee Table", "reason": f"same name as {table['sku']}"} in body["warnings"]

    # numbering unchanged: file 5 still lands on row 5's product, file 1 (skipped row) is left over
    lamp = body["created"][2]
    assert lamp["has_file"] is True
    assert Session().query(models.Product).filter_by(sku=lamp["sku"]).one().image_object_key == f"{lamp['sku']}/5.pdf"
    assert list(uploads) == [f"{lamp['sku']}/5.pdf"]
    assert any(isinstance(w, str) and "numbers: 1" in w for w in body["warnings"])
    assert Session().query(models.Product).filter_by(is_deleted=False).count() == 5  # 2 before + 3 created
