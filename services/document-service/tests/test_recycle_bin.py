"""
Schema-level regression tests for the Recycle Bin deletion-audit fix:
DocumentOut must expose deleted_by/deleted_at so the recycle bin can show
who deleted a document and when. No live Postgres needed — these test the
Pydantic schema directly, the same way test_companies.py does. Full
create/delete/restore/persistence behavior against a real database and
through real Nginx (Editor vs Viewer vs superuser, X-Username never
client-spoofable) is covered by the infra-integration CI job.
"""

from app import schemas


def test_document_out_has_deleted_by_and_deleted_at():
    assert "deleted_by" in schemas.DocumentOut.model_fields
    assert "deleted_at" in schemas.DocumentOut.model_fields


def test_document_out_deleted_fields_are_optional():
    # A never-deleted document (or one trashed before this column existed)
    # must still validate with these fields absent/None — never required.
    doc = schemas.DocumentOut.model_validate({
        "id": "00000000-0000-0000-0000-000000000000",
        "doc_type": "quotation",
        "doc_number": "Q-0001",
        "customer_id": None,
        "project_id": None,
        "company_id": None,
        "director_id": None,
        "status": "draft",
        "currency": "USD",
        "subtotal": None,
        "tax_total": None,
        "total": None,
        "issue_date": None,
        "due_date": None,
        "doc_metadata": None,
        "terms_and_conditions": None,
        "file_object_key": None,
        "version": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "items": [],
    })
    assert doc.deleted_by is None
    assert doc.deleted_at is None
