"""
Loop iteration 21 — safe simultaneous use in document-service: version check
on edit (409), Idempotency-Key replay, the locked doc-number counter and the
all-or-nothing document save. Real API + ORM on SQLite (fixtures from
test_group_scope.py). Parallel races run against real Postgres in the gateway
suite (tests/test_concurrency.py at the repo root).
"""
import pathlib
from datetime import date, datetime, timedelta

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app import models
from app.core.concurrency import CONFLICT_DETAIL
from app.main import app
from tests.test_group_scope import AS_OPS, _create, ctx  # noqa: F401

TODAY = f"{date.today():%Y%m%d}"
ENTITIES = [  # path, create body, edit field
    ("/customers", {"name": "Buyer"}, "name"),
    ("/projects", {"name": "Project"}, "name"),
    ("/companies", {"name": "Supplier"}, "name"),
    ("/documents", {"doc_type": "quotation", "items": [{"description": "Widget", "quantity": "1", "unit_price": "5"}]}, "terms_and_conditions"),
]


def _edit(client, path, row, field, value, **extra):
    body = {field: value, **extra}
    if path == "/documents":  # a document edit is a full edit: items are re-sent
        body["items"] = [{"description": "Widget", "quantity": "1", "unit_price": "5"}]
    return client.patch(f"{path}/{row['id']}", json=body, headers=AS_OPS)


@pytest.mark.parametrize("path,body,field", ENTITIES)
def test_stale_version_gets_409_and_keeps_the_first_save(ctx, path, body, field):
    client, _ = ctx
    row = client.post(path, json=body, headers=AS_OPS).json()
    assert row["version"] == 1

    a = _edit(client, path, row, field, "from A", version=1)  # A and B both loaded version 1
    assert a.status_code == 200, a.text
    assert a.json()["version"] == 2

    b = _edit(client, path, row, field, "from B", version=1)
    assert b.status_code == 409
    assert b.json()["detail"] == CONFLICT_DETAIL
    assert client.get(f"{path}/{row['id']}", headers=AS_OPS).json()[field] == "from A"

    b2 = _edit(client, path, row, field, "from B", version=2)  # B reloaded
    assert b2.status_code == 200 and b2.json()[field] == "from B" and b2.json()["version"] == 3

    no_version = _edit(client, path, row, field, "script")  # scripts / old clients: no check
    assert no_version.status_code == 200 and no_version.json()["version"] == 4


def test_items_only_document_edit_still_bumps_the_version(ctx):
    client, _ = ctx
    doc = _create(client, "/documents", AS_OPS)
    body = {"items": [{"description": "Widget", "remark": "new remark", "quantity": "1", "unit_price": "5"}], "version": 1}
    assert client.patch(f"/documents/{doc['id']}", json=body, headers=AS_OPS).json()["version"] == 2
    assert client.patch(f"/documents/{doc['id']}", json=body, headers=AS_OPS).status_code == 409


@pytest.mark.parametrize("path,body,field", ENTITIES)
def test_idempotency_key_replays_the_first_create(ctx, path, body, field):
    client, Session = ctx
    model = {"/customers": models.Customer, "/projects": models.Project, "/companies": models.Company, "/documents": models.Document}[path]
    key = {**AS_OPS, "Idempotency-Key": "k-1"}
    first = client.post(path, json=body, headers=key)
    again = client.post(path, json=body, headers=key)
    assert first.status_code == again.status_code == 201
    assert again.json()["id"] == first.json()["id"]
    with Session() as db:
        assert db.query(model).count() == 1

    other = client.post(path, json=body, headers={**AS_OPS, "Idempotency-Key": "k-2"})
    assert other.json()["id"] != first.json()["id"]

    with Session() as db:  # the first key expires
        db.query(models.IdempotencyKey).filter(models.IdempotencyKey.key.like("%:k-1")).update(
            {"created_at": datetime.utcnow() - timedelta(days=2)}, synchronize_session=False)
        db.commit()
    expired = client.post(path, json=body, headers=key)
    assert expired.json()["id"] not in (first.json()["id"], other.json()["id"])
    with Session() as db:
        assert db.query(model).count() == 3


def test_doc_numbers_come_from_the_counter_seeded_from_existing_max(ctx):
    client, Session = ctx
    _create(client, "/documents", AS_OPS, doc_number=f"DOC-{TODAY}/007")  # typed by hand earlier
    assert client.get("/documents/next-number", headers=AS_OPS).json()["doc_number"] == f"DOC-{TODAY}/008"
    assert client.get("/documents/next-number", headers=AS_OPS).json()["doc_number"] == f"DOC-{TODAY}/008"  # preview takes nothing
    assert _create(client, "/documents", AS_OPS)["doc_number"] == f"DOC-{TODAY}/008"
    assert _create(client, "/documents", AS_OPS)["doc_number"] == f"DOC-{TODAY}/009"
    with Session() as db:
        assert db.execute(text("SELECT value FROM counters WHERE name = :n"), {"n": f"doc:DOC-{TODAY}/"}).scalar() == 9


def test_generated_number_taken_by_hand_is_retried_once(ctx):
    client, _ = ctx
    _create(client, "/documents", AS_OPS)  # 001, counter now 1
    _create(client, "/documents", AS_OPS, doc_number=f"DOC-{TODAY}/002")  # someone types the next one
    assert _create(client, "/documents", AS_OPS)["doc_number"] == f"DOC-{TODAY}/003"


def test_failure_after_the_header_insert_stores_nothing(ctx):
    client, Session = ctx
    _create(client, "/documents", AS_OPS)  # 001

    def boom(mapper, connection, target):
        raise RuntimeError("injected line-item failure")

    event.listen(models.LineItem, "before_insert", boom)
    try:
        r = TestClient(app, raise_server_exceptions=False).post(
            "/documents", json=ENTITIES[3][1], headers=AS_OPS)
    finally:
        event.remove(models.LineItem, "before_insert", boom)
    assert r.status_code == 500
    with Session() as db:
        assert db.query(models.Document).count() == 1
        assert db.query(models.LineItem).count() == 1
        assert db.query(models.AuditLogEntry).filter_by(action="created").count() == 1  # only the first doc's
    assert _create(client, "/documents", AS_OPS)["doc_number"] == f"DOC-{TODAY}/002"  # the number came back: no gap


def test_create_and_edit_write_their_audit_entry_in_the_same_transaction(ctx):
    client, Session = ctx
    doc = _create(client, "/documents", AS_OPS)
    _edit(client, "/documents", doc, "terms_and_conditions", "x")
    with Session() as db:
        assert sorted(e.action for e in db.query(models.AuditLogEntry)) == ["created", "edited"]


def test_single_alembic_head():
    ini = pathlib.Path(__file__).resolve().parents[1] / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "alembic"))
    assert ScriptDirectory.from_config(cfg).get_heads() == ["0011_labour"]
