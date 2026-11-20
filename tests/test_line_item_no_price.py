"""Iteration 28: a catalogue product with no price (bulk import) must never
make document create/update 500 — the line's price comes from the request,
and if neither has one the API answers 422 naming the line."""
import pytest

from conftest import unique


@pytest.fixture
def priceless_product(admin, category, group_id):
    resp = admin.post("/products", json={"group_id": group_id, "name": unique("no-price"), "category": category["name"]})
    assert resp.status_code == 201, resp.text
    assert resp.json()["unit_price"] is None
    yield resp.json()
    admin.delete(f"/products/{resp.json()['id']}")


def _doc(customer, group_id, items):
    return {"group_id": group_id, "doc_type": "quotation", "customer_id": customer["id"], "currency": "USD", "items": items}


def test_no_price_anywhere_is_422_naming_the_line(admin, customer, group_id, priceless_product):
    items = [{"description": "a", "quantity": "1", "unit_price": "1"},
             {"description": "b", "quantity": "1", "unit_price": "1"},
             {"product_id": priceless_product["id"], "quantity": "1"}]
    resp = admin.post("/documents", json=_doc(customer, group_id, items))
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "Line 3: enter a unit price"


def test_request_price_is_used_when_product_has_none(admin, customer, group_id, priceless_product):
    items = [{"product_id": priceless_product["id"], "quantity": "2", "unit_price": "7.50"}]
    resp = admin.post("/documents", json=_doc(customer, group_id, items))
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    try:
        assert float(doc["items"][0]["unit_price"]) == 7.5
        assert doc["items"][0]["description"] == priceless_product["name"]
        # update without a price → 422, never 500
        upd = admin.patch(f"/documents/{doc['id']}", json={"customer_id": customer["id"], "version": doc["version"],
                                                            "items": [{"product_id": priceless_product["id"], "quantity": "1"}]})
        assert upd.status_code == 422, upd.text
        assert upd.json()["detail"] == "Line 1: enter a unit price"
    finally:
        admin.delete(f"/documents/{doc['id']}")


def test_product_price_still_used_when_request_has_none(admin, customer, group_id, product):
    resp = admin.post("/documents", json=_doc(customer, group_id, [{"product_id": product["id"], "quantity": "2"}]))
    assert resp.status_code == 201, resp.text
    assert float(resp.json()["items"][0]["unit_price"]) == 12.5
    admin.delete(f"/documents/{resp.json()['id']}")
