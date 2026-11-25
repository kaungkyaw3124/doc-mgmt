"""
Loop iteration 21 — safe simultaneous use in catalogue-service: product
version check (409), Idempotency-Key replay, the locked SKU counter, and no
half-saved product / orphan file when storage or the database fails. Real
API + ORM on SQLite (fixtures from test_group_scope.py). Parallel races run
against real Postgres in the gateway suite (tests/test_concurrency.py).
"""
import io
import pathlib
from datetime import datetime, timedelta

import openpyxl
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session as OrmSession

from app import models
from app.core import search_client
from app.core.concurrency import CONFLICT_DETAIL
from app.main import app
from app.routers import products as products_router
from tests.test_group_scope import AS_OPS, _product, ctx  # noqa: F401


def test_stale_product_version_gets_409(ctx):
    client, _ = ctx
    p = _product(client, AS_OPS)
    assert p["version"] == 1
    assert client.patch(f"/products/{p['id']}", json={"name": "A", "version": 1}, headers=AS_OPS).json()["version"] == 2
    b = client.patch(f"/products/{p['id']}", json={"name": "B", "version": 1}, headers=AS_OPS)
    assert b.status_code == 409 and b.json()["detail"] == CONFLICT_DETAIL
    assert client.get(f"/products/{p['id']}", headers=AS_OPS).json()["name"] == "A"
    assert client.patch(f"/products/{p['id']}", json={"name": "B", "version": 2}, headers=AS_OPS).status_code == 200
    assert client.patch(f"/products/{p['id']}", json={"name": "C"}, headers=AS_OPS).status_code == 200  # no version: no check


def test_idempotency_key_replays_and_expires(ctx):
    client, Session = ctx
    key = {**AS_OPS, "Idempotency-Key": "p-1"}
    first = client.post("/products", json={"name": "Desk"}, headers=key)
    again = client.post("/products", json={"name": "Desk"}, headers=key)  # would be an exact duplicate if it created one
    assert first.status_code == again.status_code == 201 and again.json()["id"] == first.json()["id"]
    assert client.post("/products", json={"name": "Lamp"}, headers={**AS_OPS, "Idempotency-Key": "p-2"}).json()["id"] != first.json()["id"]
    with Session() as db:
        assert db.query(models.Product).count() == 2
        db.query(models.IdempotencyKey).update({"created_at": datetime.utcnow() - timedelta(days=2)}, synchronize_session=False)
        db.commit()
    assert client.post("/products", json={"name": "Chair"}, headers=key).json()["id"] != first.json()["id"]


def test_skus_come_from_the_counter_and_skip_a_hand_typed_one(ctx):
    client, _ = ctx
    _product(client, AS_OPS, name="old", sku="PRD-0041")  # existing data: the counter seeds from it
    assert _product(client, AS_OPS, name="a")["sku"] == "PRD-0042"
    _product(client, AS_OPS, name="typed", sku="PRD-0043")  # someone types the next one
    assert _product(client, AS_OPS, name="b")["sku"] == "PRD-0044"  # clash retried once, past the highest
    assert _product(client, AS_OPS, name="c")["sku"] == "PRD-0045"


def _sheet(*names) -> bytes:
    wb = openpyxl.Workbook()
    wb.active.append(["Name"])
    for n in names:
        wb.active.append([n])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _zip(*names) -> bytes:
    import zipfile
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n in names:
            z.writestr(n, b"%PDF-1.4 test")
    return out.getvalue()


def _import(client, *names, files=("1.pdf",)):
    return client.post("/products/bulk-import", headers=AS_OPS, files={
        "excel_file": ("p.xlsx", _sheet(*names)), "catalogue_zip": ("c.zip", _zip(*files))})


def test_bulk_import_storage_failure_creates_no_row(ctx, monkeypatch):
    client, Session = ctx

    def down(*a, **k):
        raise ConnectionError("minio down")

    monkeypatch.setattr(products_router, "upload_file", down)
    r = _import(client, "with file", "no file")
    assert r.status_code == 200, r.text
    assert [s["row"] for s in r.json()["skipped"]] == [1]
    assert [c["name"] for c in r.json()["created"]] == ["no file"]
    assert r.json()["created"][0]["sku"] == "PRD-0001"  # the failed row gave its number back
    with Session() as db:
        assert [p.name for p in db.query(models.Product)] == ["no file"]


def test_bulk_import_commit_failure_removes_the_uploaded_file(ctx, monkeypatch):
    client, Session = ctx
    uploaded, deleted = [], []
    monkeypatch.setattr(products_router, "upload_file", lambda f, key, content_type=None: uploaded.append(key))
    monkeypatch.setattr(products_router, "delete_file", deleted.append)

    def fail(session):
        if any(getattr(o, "image_object_key", None) for o in session.dirty | session.new):
            raise RuntimeError("database went away")

    event.listen(OrmSession, "before_commit", fail)
    try:
        r = TestClient(app, raise_server_exceptions=False).post("/products/bulk-import", headers=AS_OPS, files={
            "excel_file": ("p.xlsx", _sheet("with file")), "catalogue_zip": ("c.zip", _zip("1.pdf"))})
    finally:
        event.remove(OrmSession, "before_commit", fail)
    assert r.status_code == 500
    assert uploaded and deleted == uploaded
    with Session() as db:
        assert db.query(models.Product).count() == 0


def test_file_upload_storage_failure_is_a_clear_error_and_changes_nothing(ctx, monkeypatch):
    client, Session = ctx
    p = _product(client, AS_OPS)

    def down(*a, **k):
        raise ConnectionError("minio down")

    monkeypatch.setattr(products_router, "upload_file", down)
    r = client.post(f"/products/{p['id']}/file", headers=AS_OPS, files={"file": ("c.pdf", b"%PDF-1.4 x", "application/pdf")})
    assert r.status_code == 502 and "Nothing was changed" in r.json()["detail"]
    assert client.get(f"/products/{p['id']}", headers=AS_OPS).json()["image_object_key"] is None


def test_search_index_failure_does_not_fail_the_save(ctx, monkeypatch):
    client, _ = ctx
    monkeypatch.setattr(products_router, "index_product", search_client.index_product)  # the real one…

    class Down:
        def index(self, name):
            raise ConnectionError("meilisearch down")

    monkeypatch.setattr(search_client, "_client", Down())  # …with search unreachable
    assert client.post("/products", json={"name": "Saved anyway"}, headers=AS_OPS).status_code == 201


def test_single_alembic_head():
    ini = pathlib.Path(__file__).resolve().parents[1] / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "alembic"))
    assert ScriptDirectory.from_config(cfg).get_heads() == ["0003_duplicate_dismissals"]
