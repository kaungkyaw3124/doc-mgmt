"""add companies.header_style and companies.letterhead_object_key

Revision ID: 0007_company_letterhead
Revises: 0006_customer_organization
Create Date: 2026-11-10

A company's quotation header is either its logo (+ seal) row, or one
full-width letterhead banner image. header_style picks which ("logo" or
"letterhead"); existing rows default to "logo", so nothing changes for them.
letterhead_object_key points at the uploaded banner and is kept when the
style is switched back. Guarded/idempotent like the earlier migrations — a
no-op where Base.metadata.create_all() already created the columns.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_company_letterhead"
down_revision: Union[str, None] = "0006_customer_organization"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(table_name):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    columns = _existing_columns("companies")
    if "header_style" not in columns:
        op.add_column("companies", sa.Column("header_style", sa.String(length=20), server_default="logo"))
    if "letterhead_object_key" not in columns:
        op.add_column("companies", sa.Column("letterhead_object_key", sa.String(length=500)))


def downgrade() -> None:
    columns = _existing_columns("companies")
    if "letterhead_object_key" in columns:
        op.drop_column("companies", "letterhead_object_key")
    if "header_style" in columns:
        op.drop_column("companies", "header_style")
