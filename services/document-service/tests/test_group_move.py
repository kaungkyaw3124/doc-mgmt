"""
Loop iteration 20 — superuser moves documents/customers/projects to another
group. Real API + ORM on SQLite (fixtures from test_group_scope.py); search
indexing and the audit log are recorded instead of called.
"""
import logging
import uuid

import pytest

from app.routers import moves as moves_router
from tests.test_group_scope import AS_CITY, AS_OPS, AS_SUPER, CITY, OPS, _create, ctx  # noqa: F401


@pytest.fixture
def mv(ctx, monkeypatch):  # noqa: F811
    client, Session = ctx
    calls = {"indexed": [], "audit": []}
    monkeypatch.setattr(moves_router, "index_document", lambda doc: calls["indexed"].append((str(doc.id), str(doc.group_id))))
    monkeypatch.setattr(moves_router, "log_action", lambda db, doc_id, user, action: calls["audit"].append((str(doc_id), user, action)))
    return client, calls


def _doc_with_links(client, headers=AS_OPS):
    cust = _create(client, "/customers", headers)
    proj = _create(client, "/projects", headers)
    doc = _create(client, "/documents", headers, customer_id=cust["id"], project_id=proj["id"])
    return doc, cust, proj


def _group(client, path, row):
    return client.get(f"{path}/{row['id']}", headers=AS_SUPER).json()["group_id"]


def test_moving_a_document_takes_its_customer_and_project_and_reindexes(mv, caplog):
    client, calls = mv
    doc, cust, proj = _doc_with_links(client)
    body = {"group_id": str(CITY), "dry_run": True}
    r = client.post(f"/documents/{doc['id']}/move", json=body, headers=AS_SUPER)
    assert r.json() == {"moved": {"documents": 1, "customers": 1, "projects": 1}, "dry_run": True}
    assert _group(client, "/documents", doc) == str(OPS)  # dry run changed nothing

    with caplog.at_level(logging.INFO, logger="app.audit"):
        r = client.post(f"/documents/{doc['id']}/move", json={"group_id": str(CITY)},
                        headers={**AS_SUPER, "X-Username": "boss"})
    assert r.status_code == 200 and r.json()["moved"] == {"documents": 1, "customers": 1, "projects": 1}
    for path, row in (("/documents", doc), ("/customers", cust), ("/projects", proj)):
        assert _group(client, path, row) == str(CITY)
    assert client.get(f"/documents/{doc['id']}", headers=AS_OPS).status_code == 404
    assert client.get(f"/documents/{doc['id']}", headers=AS_CITY).status_code == 200
    assert calls["indexed"] == [(doc["id"], str(CITY))]
    assert calls["audit"] == [(doc["id"], "boss", "moved_group")]
    lines = [m for m in caplog.messages if m.startswith("group_move")]
    assert f"group_move user=boss document={doc['id']} from={OPS} to={CITY}" in lines and len(lines) == 3


def test_document_without_links_is_refused_when_its_customer_stays_behind(mv):
    client, calls = mv
    doc, cust, _ = _doc_with_links(client)
    r = client.post(f"/documents/{doc['id']}/move", json={"group_id": str(CITY), "with_links": False}, headers=AS_SUPER)
    assert r.status_code == 409 and "1 customer, 1 project" in r.json()["detail"]
    assert _group(client, "/documents", doc) == str(OPS) and _group(client, "/customers", cust) == str(OPS)
    assert calls["indexed"] == []
    # a document with no customer/project moves alone without the box
    lone = _create(client, "/documents", AS_OPS)
    r = client.post(f"/documents/{lone['id']}/move", json={"group_id": str(CITY), "with_links": False}, headers=AS_SUPER)
    assert r.json()["moved"] == {"documents": 1, "customers": 0, "projects": 0}


@pytest.mark.parametrize("path", ["/customers", "/projects"])
def test_customer_or_project_used_by_documents_needs_the_box(mv, path):
    client, _ = mv
    doc, cust, proj = _doc_with_links(client)
    other = _create(client, "/documents", AS_OPS, customer_id=cust["id"])  # a second document on the customer
    row = cust if path == "/customers" else proj
    r = client.post(f"{path}/{row['id']}/move", json={"group_id": str(CITY), "with_links": False}, headers=AS_SUPER)
    assert r.status_code == 409 and "also move its documents" in r.json()["detail"]
    r = client.post(f"{path}/{row['id']}/move", json={"group_id": str(CITY)}, headers=AS_SUPER)
    # the whole connected set moves: both documents, the customer and the project
    assert r.json()["moved"] == {"documents": 2, "customers": 1, "projects": 1}
    assert {_group(client, "/documents", d) for d in (doc, other)} == {str(CITY)}


def test_unused_customer_moves_alone_and_bulk_is_atomic(mv):
    client, _ = mv
    a, b = _create(client, "/customers", AS_OPS), _create(client, "/customers", AS_OPS)
    r = client.post("/customers/move", json={"ids": [a["id"], str(uuid.uuid4())], "group_id": str(CITY)}, headers=AS_SUPER)
    assert r.status_code == 404 and _group(client, "/customers", a) == str(OPS)  # nothing moved
    r = client.post("/customers/move", json={"ids": [a["id"], b["id"]], "group_id": str(CITY), "with_links": False}, headers=AS_SUPER)
    assert r.json()["moved"]["customers"] == 2
    # already there: a no-op, not an error
    assert client.post(f"/customers/{a['id']}/move", json={"group_id": str(CITY)}, headers=AS_SUPER).json()["moved"]["customers"] == 0


def test_bulk_documents_and_unassigned_rows(mv, ctx):  # noqa: F811
    client, _ = mv
    _, Session = ctx
    d1, d2 = _create(client, "/documents", AS_OPS), _create(client, "/documents", AS_OPS)
    r = client.post("/documents/move", json={"ids": [d1["id"], d2["id"]], "group_id": str(CITY)}, headers=AS_SUPER)
    assert r.json()["moved"]["documents"] == 2
    # unassigned (NULL) rows are movable and counted for the admin card
    from app import models
    with Session() as db:
        db.add(models.Customer(name="Old", group_id=None))
        db.commit()
    assert client.get("/documents/unassigned-counts", headers=AS_SUPER).json() == {"documents": 0, "customers": 1, "projects": 0}
    old = next(c for c in client.get("/customers", headers=AS_SUPER).json() if c["group_id"] is None)
    assert client.post(f"/customers/{old['id']}/move", json={"group_id": str(OPS)}, headers=AS_SUPER).status_code == 200
    assert client.get("/documents/unassigned-counts", headers=AS_SUPER).json()["customers"] == 0


def test_only_superusers_and_only_active_groups(mv):
    client, _ = mv
    doc = _create(client, "/documents", AS_OPS)
    both_admin = {**AS_OPS, "X-Group-Ids": f"{OPS},{CITY}"}  # a member (even group admin) of both groups
    for path, body in ((f"/documents/{doc['id']}/move", {"group_id": str(CITY)}),
                       ("/documents/move", {"ids": [doc["id"]], "group_id": str(CITY)}),
                       ("/customers/move", {"ids": [doc["id"]], "group_id": str(CITY)})):
        assert client.post(path, json=body, headers=both_admin).status_code == 403
    assert client.get("/documents/unassigned-counts", headers=AS_OPS).status_code == 403
    r = client.post(f"/documents/{doc['id']}/move", json={"group_id": str(uuid.uuid4())}, headers=AS_SUPER)
    assert r.status_code == 422  # unknown or disabled group
    assert client.post(f"/documents/{uuid.uuid4()}/move", json={"group_id": str(CITY)}, headers=AS_SUPER).status_code == 404
