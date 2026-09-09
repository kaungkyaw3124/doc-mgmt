"""move signer/contact details from companies to company_directors

Revision ID: 0002_move_signer_details_to_directors
Revises: 0001_initial
Create Date: 2026-09-09

Company no longer carries position/address/contact_no/support_email/
support_phone — that signer/contact information now belongs to the
individual Managing Director selected on a document (company_directors
gains address/contact_no/email).

NOTE on company_directors / documents.director_id: 0001_initial never
created these — they were added to models.py after that migration was
written, and have only ever existed via Base.metadata.create_all() (see
docs/known-issues.md #10 and docs/decisions.md #4 — Alembic is present
but not run at app startup in this project). This migration creates them
here too, guarded to be a no-op wherever create_all() already has, so
`alembic upgrade head` produces a consistent schema either way.

IMPORTANT — data loss on the dropped company columns: this only removes
the columns (any existing values in company.position/address/contact_no/
support_email/support_phone are discarded, they are NOT copied onto any
director). That copy is deliberately NOT automated: those columns never
recorded a person's *name* (only a job title), so there is no reliable,
non-invented "MD name" to attach the address/phone/email to. Before
running this migration against a database that has real values in those
columns, read them first (e.g. `SELECT id, name, position, address,
contact_no, support_email, support_phone FROM companies;`) and manually
create the corresponding director(s) via POST /companies/{id}/directors
with the correct real name.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_move_signer_details_to_directors"
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

    # The actual field move for this task.
    company_columns = _existing_columns("companies")
    for col in ("position", "address", "contact_no", "support_email", "support_phone"):
        if col in company_columns:
            op.drop_column("companies", col)


def downgrade() -> None:
    company_columns = _existing_columns("companies")
    if "position" not in company_columns:
        op.add_column("companies", sa.Column("position", sa.String(length=100)))
    if "address" not in company_columns:
        op.add_column("companies", sa.Column("address", sa.Text()))
    if "contact_no" not in company_columns:
        op.add_column("companies", sa.Column("contact_no", sa.String(length=50)))
    if "support_email" not in company_columns:
        op.add_column("companies", sa.Column("support_email", sa.String(length=255)))
    if "support_phone" not in company_columns:
        op.add_column("companies", sa.Column("support_phone", sa.String(length=50)))

    director_columns = _existing_columns("company_directors")
    if "email" in director_columns:
        op.drop_column("company_directors", "email")
    if "contact_no" in director_columns:
        op.drop_column("company_directors", "contact_no")
    if "address" in director_columns:
        op.drop_column("company_directors", "address")
