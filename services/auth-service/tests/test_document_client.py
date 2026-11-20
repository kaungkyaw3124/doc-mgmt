"""Loop iteration 26: the project-ownership lookup used before granting
project access fails CLOSED — no answer from document-service, no grant."""
import urllib.error

import pytest
from fastapi import HTTPException

from app.core import document_client


def test_no_groups_means_no_projects_without_a_call(monkeypatch):
    monkeypatch.setattr(document_client.urllib.request, "urlopen", lambda *a, **k: pytest.fail("called"))
    assert document_client.project_ids_in_groups([]) == set()


def test_unreachable_document_service_refuses(monkeypatch):
    def down(*a, **k):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(document_client.urllib.request, "urlopen", down)
    with pytest.raises(HTTPException) as exc:
        document_client.project_ids_in_groups(["00000000-0000-0000-0000-000000000001"])
    assert exc.value.status_code == 503


def test_sends_the_groups_scope_and_secret(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, *a): return b'[{"id": "p1"}, {"id": "p2"}]'

    def fake(req, timeout):
        seen.update({k.lower(): v for k, v in req.header_items()})
        return Resp()
    monkeypatch.setattr(document_client.urllib.request, "urlopen", fake)
    assert document_client.project_ids_in_groups(["g1", "g2"]) == {"p1", "p2"}
    assert seen["x-group-ids"] == "g1,g2" and seen["x-is-superuser"] == "0" and seen["x-allowed-projects"] == "ALL"
    assert seen["x-internal-secret"]
