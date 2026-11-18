"""add products.group_id (owning department)

Revision ID: 0001_product_group_id
Revises:
Create Date: 2026-11-18

First Alembic revision of catalogue-service (until now it relied on
Base.metadata.create_all(), which never adds a column to an existing table).
startup runs create_all() first, so on a brand-new database this is a no-op;
on an existing one it adds the nullable, indexed column. Nothing is
backfilled: old products stay NULL ("unassigned", superusers only) until
POST /products/assign-unassigned. No FK — the id is an auth-service group.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0001_product_group_id"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "group_id" not in {c["name"] for c in insp.get_columns("products")}:
        op.add_column("products", sa.Column("group_id", UUID(as_uuid=True)))
    if "ix_products_group_id" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("products")}:
        op.create_index("ix_products_group_id", "products", ["group_id"])


def downgrade() -> None:
    if "ix_products_group_id" in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("products")}:
        op.drop_index("ix_products_group_id", table_name="products")
    if "group_id" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("products")}:
        op.drop_column("products", "group_id")
