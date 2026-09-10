"""add documents.deleted_by / documents.deleted_at (recycle bin deletion audit)

Revision ID: 0003_document_deleted_by_at
Revises: 0002_director_contact_fields
Create Date: 2026-09-10

Records who soft-deleted a document (trash_document) and when, so the
recycle bin can show "Deleted by"/"Deleted at" without relying on the
separate audit-log permission (audit_log_entries stays the full event
timeline; these two columns are just a denormalized snapshot of the
document's current trashed state, cleared on restore — same pattern as
is_deleted itself). See docs/SECURITY_HARDENING_LOG.md's Recycle Bin
entry for the full design rationale.

Both columns are nullable and back-dated for nothing: a document already
sitting in the recycle bin before this migration runs will have
deleted_by/deleted_at = NULL — there is no reliable source to backfill
that from (audit_log_entries never recorded a "deleted" action before
this same change added it), so the app displays those as "Unknown"
rather than guessing. Nothing existing is modified, dropped, or lost by
this migration — purely additive.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_document_deleted_by_at"
down_revision: Union[str, None] = "0002_director_contact_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(table_name):
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table_name)}


def upgrade() -> None:
    columns = _existing_columns("documents")
    if "deleted_by" not in columns:
        op.add_column("documents", sa.Column("deleted_by", sa.String(length=100)))
    if "deleted_at" not in columns:
        op.add_column("documents", sa.Column("deleted_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    columns = _existing_columns("documents")
    if "deleted_at" in columns:
        op.drop_column("documents", "deleted_at")
    if "deleted_by" in columns:
        op.drop_column("documents", "deleted_by")
