"""
Loop iteration 20 — superuser moves products to another group (single and
bulk); bundle/sub-item links move together or the move is refused.
Fixtures from test_group_scope.py; search indexing is recorded.
"""
import logging
import uuid

import pytest

from app import models
from app.routers import products as products_router
from tests.test_group_scope import AS_CITY, AS_OPS, AS_SUPER, CITY, OPS, _product, ctx  # noqa: F401


@pytest.fixture
def mv(ctx, monkeypatch):  # noqa: F811
    client, Session = ctx
    indexed = []
    monkeypatch.setattr(products_router, "index_product", lambda p: indexed.append((str(p.id), str(p.group_id))))
    return client, Session, indexed


def _group(client, p):
    return client.get(f"/products/{p['id']}", headers=AS_SUPER).json()["group_id"]


def test_move_one_product_reindexes_and_audits(mv, caplog):
    client, _, indexed = mv
    p = _product(client, AS_OPS)
    indexed.clear()  # creating it indexed it once already
    r = client.post(f"/products/{p['id']}/move", json={"group_id": str(CITY), "dry_run": True}, headers=AS_SUPER)
    assert r.json() == {"moved": {"products": 1}, "dry_run": True} and _group(client, p) == str(OPS)
    with caplog.at_level(logging.INFO, logger="app.audit"):
        r = client.post(f"/products/{p['id']}/move", json={"group_id": str(CITY)}, headers={**AS_SUPER, "X-Username": "boss"})
    assert r.json()["moved"] == {"products": 1}
    assert client.get(f"/products/{p['id']}", headers=AS_OPS).status_code == 404
    assert client.get(f"/products/{p['id']}", headers=AS_CITY).status_code == 200
    assert indexed == [(p["id"], str(CITY))]
    assert f"group_move user=boss product={p['id']} from={OPS} to={CITY}" in caplog.messages


def test_bulk_move_is_atomic(mv):
    client, _, _ = mv
    a, b = _product(client, AS_OPS, "A"), _product(client, AS_OPS, "B")
    r = client.post("/products/move", json={"ids": [a["id"], str(uuid.uuid4())], "group_id": str(CITY)}, headers=AS_SUPER)
    assert r.status_code == 404 and _group(client, a) == str(OPS)
    r = client.post("/products/move", json={"ids": [a["id"], b["id"]], "group_id": str(CITY)}, headers=AS_SUPER)
    assert r.json()["moved"] == {"products": 2}


def test_bundle_moves_with_its_sub_items_or_is_refused(mv):
    client, _, _ = mv
    bundle, part = _product(client, AS_OPS, "PC"), _product(client, AS_OPS, "CPU")
    assert client.post(f"/products/{bundle['id']}/sub-items", json={"product_id": part["id"]}, headers=AS_OPS).status_code in (200, 201)
    r = client.post(f"/products/{part['id']}/move", json={"group_id": str(CITY), "with_links": False}, headers=AS_SUPER)
    assert r.status_code == 409 and "1 product" in r.json()["detail"] and _group(client, part) == str(OPS)
    r = client.post(f"/products/{part['id']}/move", json={"group_id": str(CITY)}, headers=AS_SUPER)
    assert r.json()["moved"] == {"products": 2} and _group(client, bundle) == str(CITY)


def test_only_superusers_active_groups_and_unassigned_counts(mv):
    client, Session, _ = mv
    p = _product(client, AS_OPS)
    both = {**AS_OPS, "X-Group-Ids": f"{OPS},{CITY}"}
    assert client.post(f"/products/{p['id']}/move", json={"group_id": str(CITY)}, headers=both).status_code == 403
    assert client.post("/products/move", json={"ids": [p["id"]], "group_id": str(CITY)}, headers=both).status_code == 403
    assert client.get("/products/unassigned-counts", headers=AS_OPS).status_code == 403
    assert client.post(f"/products/{p['id']}/move", json={"group_id": str(uuid.uuid4())}, headers=AS_SUPER).status_code == 422
    with Session() as db:
        db.add(models.Product(sku="OLD-1", name="Old", group_id=None))
        db.commit()
    assert client.get("/products/unassigned-counts", headers=AS_SUPER).json() == {"products": 1}
