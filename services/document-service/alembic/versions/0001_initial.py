"""initial schema: customers, projects, companies, documents, line_items, audit_log_entries

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
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_customers_is_deleted", "customers", ["is_deleted"])

    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("budget_year", sa.String(length=20)),
        sa.Column("description", sa.Text()),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_projects_is_deleted", "projects", ["is_deleted"])

    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("short_name", sa.String(length=20)),
        sa.Column("position", sa.String(length=100)),
        sa.Column("address", sa.Text()),
        sa.Column("contact_no", sa.String(length=50)),
        sa.Column("support_email", sa.String(length=255)),
        sa.Column("support_phone", sa.String(length=50)),
        sa.Column("logo_object_key", sa.String(length=500)),
        sa.Column("seal_object_key", sa.String(length=500)),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_companies_is_deleted", "companies", ["is_deleted"])

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("doc_type", sa.String(length=20), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("doc_number", sa.String(length=50), nullable=False, unique=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("currency", sa.String(length=3), server_default="USD"),
        sa.Column("subtotal", sa.Numeric(12, 2)),
        sa.Column("tax_total", sa.Numeric(12, 2)),
        sa.Column("total", sa.Numeric(12, 2)),
        sa.Column("issue_date", sa.Date()),
        sa.Column("due_date", sa.Date()),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("terms_and_conditions", sa.Text()),
        sa.Column("file_object_key", sa.String(length=500)),
        sa.Column("version", sa.Integer(), server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_documents_customer", "documents", ["customer_id"])
    op.create_index("idx_documents_project", "documents", ["project_id"])
    op.create_index("idx_documents_company", "documents", ["company_id"])
    op.create_index("idx_documents_type_status", "documents", ["doc_type", "status"])
    op.create_index("ix_documents_is_deleted", "documents", ["is_deleted"])
    op.create_index(
        "idx_documents_metadata", "documents", ["metadata"], postgresql_using="gin"
    )

    op.create_table(
        "line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
        ),
        sa.Column("product_id", postgresql.UUID(as_uuid=True)),
        sa.Column("sort_order", sa.Integer(), server_default="0"),
        sa.Column("description", sa.Text()),
        sa.Column("remark", sa.String(length=500)),
        sa.Column("unit", sa.String(length=20), server_default="Nos"),
        sa.Column("quantity", sa.Numeric(12, 2)),
        sa.Column("unit_price", sa.Numeric(12, 2)),
        sa.Column("tax_rate", sa.Numeric(5, 2)),
        sa.Column("line_total", sa.Numeric(12, 2)),
    )
    op.create_index("idx_line_items_document", "line_items", ["document_id"])
    op.create_index("idx_line_items_product", "line_items", ["product_id"])

    op.create_table(
        "audit_log_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_audit_log_document", "audit_log_entries", ["document_id"])


def downgrade() -> None:
    op.drop_table("audit_log_entries")
    op.drop_table("line_items")
    op.drop_index("idx_documents_metadata", table_name="documents")
    op.drop_index("idx_documents_type_status", table_name="documents")
    op.drop_index("idx_documents_company", table_name="documents")
    op.drop_index("idx_documents_project", table_name="documents")
    op.drop_index("idx_documents_customer", table_name="documents")
    op.drop_table("documents")
    op.drop_table("companies")
    op.drop_table("projects")
    op.drop_table("customers")
