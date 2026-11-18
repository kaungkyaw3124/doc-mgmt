"""
Loop iteration 19 — search results are filtered by group server-side: a
superuser gets no group filter, anyone else a filter on the indexed group_id,
and a caller in no group (or with the headers missing) gets nothing without
Meilisearch even being asked.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.routers import search as search_router

OPS, CITY = str(uuid.uuid4()), str(uuid.uuid4())
SECRET = {"X-Internal-Secret": settings.internal_shared_secret}


class _Index:
    def __init__(self, calls, name):
        self.calls, self.name = calls, name

    def search(self, q, params):
        self.calls.append((self.name, params.get("filter")))
        return {"hits": []}


@pytest.fixture
def calls(monkeypatch):
    calls = []
    monkeypatch.setattr(search_router._client, "index", lambda name: _Index(calls, name))
    return calls


def test_group_filter_helper():
    assert search_router._group_filter(OPS, "1") is None
    assert search_router._group_filter(None, None) == ""
    assert search_router._group_filter("junk, ,", "0") == ""
    assert search_router._group_filter(f"{OPS},{CITY}", "0") == f'group_id IN ["{OPS}", "{CITY}"]'
    # a value that tries to break out of the filter is dropped, not quoted in
    assert search_router._group_filter(f'{OPS}"] OR group_id EXISTS', "0") == ""


def test_search_applies_the_group_filter(calls):
    client = TestClient(app)
    r = client.get("/search", params={"q": "chair"}, headers={**SECRET, "X-Group-Ids": OPS, "X-Is-Superuser": "0"})
    assert r.status_code == 200
    assert calls == [("documents", f'group_id IN ["{OPS}"]'), ("products", f'group_id IN ["{OPS}"]')]

    calls.clear()
    client.get("/search", params={"q": "chair", "doc_type": "quotation"}, headers={**SECRET, "X-Group-Ids": OPS, "X-Is-Superuser": "0"})
    assert calls[0] == ("documents", f'group_id IN ["{OPS}"] AND doc_type = "quotation"')

    calls.clear()
    client.get("/search", params={"q": "chair"}, headers={**SECRET, "X-Group-Ids": OPS, "X-Is-Superuser": "1"})
    assert calls == [("documents", None), ("products", None)]


@pytest.mark.parametrize("headers", [{}, {"X-Group-Ids": "", "X-Is-Superuser": "0"}])
def test_no_group_searches_nothing(calls, headers):
    r = TestClient(app).get("/search", params={"q": "chair"}, headers={**SECRET, **headers})
    assert r.json() == {"documents": [], "products": []} and calls == []
