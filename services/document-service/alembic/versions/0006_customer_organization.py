"""add customers.organization

Revision ID: 0006_customer_organization
Revises: 0005_document_manual_end_user
Create Date: 2026-10-30

A saved customer can have an Organization separate from its Name. Nullable,
no default: existing customers keep NULL and their quotations keep printing
the name as the Organization, exactly as before. NULL (not a copy of the
name) is also what a blank Organization stores, so renaming a customer still
flows through. Guarded/idempotent like 0004/0005 — a no-op where the column
already exists (second `alembic upgrade head`, or a create_all() database).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_customer_organization"
down_revision: Union[str, None] = "0005_document_manual_end_user"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(table_name):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    if "organization" not in _existing_columns("customers"):
        op.add_column("customers", sa.Column("organization", sa.String(length=255)))


def downgrade() -> None:
    if "organization" in _existing_columns("customers"):
        op.drop_column("customers", "organization")
