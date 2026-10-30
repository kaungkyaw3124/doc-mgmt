"""
Regression tests for the Task 7 gap fix: products.py's write endpoints did
not check X-Access-Level at all, so a Viewer role (view-only per the
Operation group's seeded RoleAccess) could bypass write restrictions on
Products via direct API calls. Every write endpoint now calls
_require_edit_access(x_access_level) before touching the DB.

No live Postgres/MinIO needed for most of these: _require_edit_access
raises before any DB/storage call, and FastAPI validates + runs
dependencies/body parsing before the route body executes, so a view-level
request against a write endpoint 403s without ever reaching the database —
these tests only need the gateway secret to clear test_gateway_auth's
middleware.
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.routers.products import _require_edit_access

client = TestClient(app)
_HEADERS_VIEW = {"X-Internal-Secret": settings.internal_shared_secret, "X-Access-Level": "view"}
_HEADERS_EDIT = {"X-Internal-Secret": settings.internal_shared_secret, "X-Access-Level": "edit"}
_HEADERS_NONE = {"X-Internal-Secret": settings.internal_shared_secret}


def test_require_edit_access_allows_edit():
    _require_edit_access("edit")  # must not raise


def test_require_edit_access_allows_missing_header_backward_compat():
    _require_edit_access(None)  # must not raise — opt-in restriction philosophy


def test_require_edit_access_rejects_view():
    with pytest.raises(HTTPException) as exc_info:
        _require_edit_access("view")
    assert exc_info.value.status_code == 403


_NIL_UUID = "00000000-0000-0000-0000-000000000000"

# (method, path, kwargs) — each body is a MINIMAL but schema-valid payload,
# because FastAPI validates the request body/dependencies before the route
# function (and thus _require_edit_access) ever runs: an invalid body would
# 422 first and never actually exercise the access check we're testing.
_WRITE_ENDPOINTS = [
    ("post", "/products", {"json": {"name": "Access Level Test Product"}}),
    ("post", "/products/bulk-import", {"files": {"excel_file": ("x.xlsx", b"not a real workbook", "application/octet-stream")}}),
    ("patch", f"/products/{_NIL_UUID}/trash", {}),
    ("patch", f"/products/{_NIL_UUID}/restore", {}),
    ("patch", f"/products/{_NIL_UUID}", {"json": {}}),
    ("delete", f"/products/{_NIL_UUID}", {}),
    ("post", f"/products/{_NIL_UUID}/sub-items", {"json": {"product_id": _NIL_UUID}}),
    ("delete", f"/products/{_NIL_UUID}/sub-items/{_NIL_UUID}", {}),
]


@pytest.mark.parametrize("method,path,kwargs", _WRITE_ENDPOINTS)
def test_view_access_level_is_rejected_before_reaching_the_db(method, path, kwargs):
    """
    A Viewer's X-Access-Level: view must be rejected by every write
    endpoint — proving there's no remaining bypass. (upload_product_file
    needs multipart form data to pass FastAPI's own validation before our
    handler runs, so it's covered separately below.)
    """
    resp = client.request(method.upper(), path, headers=_HEADERS_VIEW, **kwargs)
    assert resp.status_code == 403, f"{method.upper()} {path} did not enforce view-only access: {resp.status_code} {resp.text}"
    assert "view-only" in resp.json()["detail"]


def test_upload_product_file_rejects_view_access_level():
    resp = client.post(
        "/products/00000000-0000-0000-0000-000000000000/file",
        headers=_HEADERS_VIEW,
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 403
    assert "view-only" in resp.json()["detail"]
