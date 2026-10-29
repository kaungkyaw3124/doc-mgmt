"""Newly created products/documents become searchable (Meilisearch indexes
asynchronously, so poll for up to ~10s rather than asserting immediately)."""
import pytest

from conftest import create_quotation, poll, unique


def _hits(admin, q, index):
    resp = admin.get("/search", params={"q": q, "index": index})
    assert resp.status_code == 200, resp.text
    return resp.json().get(index, [])


def test_product_becomes_searchable(admin, product):
    hits = poll(lambda: [h for h in _hits(admin, product["name"], "products") if h.get("id") == product["id"]])
    assert hits, f"product {product['name']} not searchable within 10s"


@pytest.fixture
def searchable_quotation(admin, customer):
    token = unique("searchtok")
    doc = create_quotation(admin, customer["id"], f"widget {token}")
    yield doc, token
    admin.delete(f"/documents/{doc['id']}")


def test_document_becomes_searchable(admin, searchable_quotation):
    doc, token = searchable_quotation
    hits = poll(lambda: [h for h in _hits(admin, token, "documents") if h.get("id") == doc["id"]])
    assert hits, f"document {doc['doc_number']} not searchable by line-item text within 10s"
