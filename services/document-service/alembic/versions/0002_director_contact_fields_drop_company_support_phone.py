"""add director contact fields, drop unused company.support_phone

Revision ID: 0002_director_contact_fields_drop_company_support_phone
Revises: 0001_initial
Create Date: 2026-09-09

Company keeps position/address/contact_no/support_email — the quotation
Supplier block, and any future signer/signature use, still reads those
directly off the selected Company. Only support_phone is removed (it was
never used anywhere in the app; contact_no already covers the company's
phone number — see docs/SECURITY_HARDENING_LOG.md for this task).

company_directors separately gains address/contact_no/email — a company
can have several Managing Directors, each shown with their own contact
info and seal when selected on a document (kept separate from the
company's own Supplier info; see documents.director_id below).

NOTE on company_directors / documents.director_id: 0001_initial never
created these — they were added to models.py after that migration was
written, and have only ever existed via Base.metadata.create_all() (see
docs/known-issues.md #10 and docs/decisions.md #4 — Alembic is present
but not run at app startup in this project). This migration creates them
here too, guarded to be a no-op wherever create_all() already has, so
`alembic upgrade head` produces a consistent schema either way.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_director_contact_fields_drop_company_support_phone"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_tables():
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def _existing_columns(table_name):
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table_name)}


def upgrade() -> None:
    tables = _existing_tables()

    # Catch up the pre-existing company_directors/director_id gap (see
    # module docstring) — no-op if create_all() already put these in place.
    if "company_directors" not in tables:
        op.create_table(
            "company_directors",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "company_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("companies.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("seal_object_key", sa.String(length=500)),
            sa.Column("sort_order", sa.Integer(), server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True)),
        )
        op.create_index("ix_company_directors_company_id", "company_directors", ["company_id"])

    director_columns = _existing_columns("company_directors")
    if "address" not in director_columns:
        op.add_column("company_directors", sa.Column("address", sa.Text()))
    if "contact_no" not in director_columns:
        op.add_column("company_directors", sa.Column("contact_no", sa.String(length=50)))
    if "email" not in director_columns:
        op.add_column("company_directors", sa.Column("email", sa.String(length=255)))

    document_columns = _existing_columns("documents")
    if "director_id" not in document_columns:
        op.add_column(
            "documents",
            sa.Column(
                "director_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("company_directors.id"),
            ),
        )
        op.create_index("idx_documents_director", "documents", ["director_id"])

    # The actual field change for this task: only support_phone is removed
    # from Company. position/address/contact_no/support_email stay — the
    # quotation Supplier block reads them directly off the company.
    company_columns = _existing_columns("companies")
    if "support_phone" in company_columns:
        op.drop_column("companies", "support_phone")


def downgrade() -> None:
    company_columns = _existing_columns("companies")
    if "support_phone" not in company_columns:
        op.add_column("companies", sa.Column("support_phone", sa.String(length=50)))

    director_columns = _existing_columns("company_directors")
    if "email" in director_columns:
        op.drop_column("company_directors", "email")
    if "contact_no" in director_columns:
        op.drop_column("company_directors", "contact_no")
    if "address" in director_columns:
        op.drop_column("company_directors", "address")
