"""
Unit tests for _require_delete_access — the new, separate Delete
permission gate for trash_document/delete_document. See
docs/SECURITY_HARDENING_LOG.md's "Delete permission" entry: unlike
_require_edit_access (missing header = allowed, a backward-compat
default), this is deliberately fail-closed — only an explicit "true"
(computed server-side by auth-service's /verify from a
"documents-delete" RoleAccess grant, forwarded by Nginx as
X-Has-Document-Delete) is accepted. No live Postgres needed — pure
function test, same pattern as test_recycle_bin.py.
"""
import pytest
from fastapi import HTTPException

from app.routers.documents import _require_delete_access


def test_explicit_true_is_allowed():
    _require_delete_access("true")  # must not raise


def test_missing_header_is_denied():
    with pytest.raises(HTTPException) as exc_info:
        _require_delete_access(None)
    assert exc_info.value.status_code == 403


def test_explicit_false_is_denied():
    with pytest.raises(HTTPException) as exc_info:
        _require_delete_access("false")
    assert exc_info.value.status_code == 403


def test_unexpected_value_is_denied():
    """A forged/garbage header value must fail closed, not be treated as
    truthy by accident (e.g. any non-empty string)."""
    with pytest.raises(HTTPException) as exc_info:
        _require_delete_access("True")  # wrong case — must not be treated as "true"
    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException):
        _require_delete_access("1")

    with pytest.raises(HTTPException):
        _require_delete_access("yes")
