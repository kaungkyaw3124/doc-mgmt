"""initial schema: customers, documents, document_items

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255)),
        sa.Column("phone", sa.String(length=50)),
        sa.Column("billing_address", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("doc_type", sa.String(length=20), nullable=False),
        sa.Column("doc_number", sa.String(length=50), nullable=False, unique=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("currency", sa.String(length=3), server_default="USD"),
        sa.Column("subtotal", sa.Numeric(12, 2)),
        sa.Column("tax_total", sa.Numeric(12, 2)),
        sa.Column("total", sa.Numeric(12, 2)),
        sa.Column("issue_date", sa.Date()),
        sa.Column("due_date", sa.Date()),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("file_object_key", sa.String(length=500)),
        sa.Column("version", sa.Integer(), server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_documents_customer", "documents", ["customer_id"])
    op.create_index("idx_documents_type_status", "documents", ["doc_type", "status"])
    op.create_index(
        "idx_documents_metadata", "documents", ["metadata"], postgresql_using="gin"
    )

    op.create_table(
        "document_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
        ),
        sa.Column("product_id", postgresql.UUID(as_uuid=True)),
        sa.Column("description", sa.Text()),
        sa.Column("quantity", sa.Numeric(12, 2)),
        sa.Column("unit_price", sa.Numeric(12, 2)),
        sa.Column("tax_rate", sa.Numeric(5, 2)),
        sa.Column("line_total", sa.Numeric(12, 2)),
    )


def downgrade() -> None:
    op.drop_table("document_items")
    op.drop_index("idx_documents_metadata", table_name="documents")
    op.drop_index("idx_documents_type_status", table_name="documents")
    op.drop_index("idx_documents_customer", table_name="documents")
    op.drop_table("documents")
    op.drop_table("customers")
