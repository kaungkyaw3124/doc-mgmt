"""labour cost per line item: documents.include_labour, line_items.labour_unit_price

Revision ID: 0011_labour
Revises: 0010_concurrency
Create Date: 2026-11-18

include_labour switches a document's Labour columns on (existing rows: false,
so their exports stay as they are). labour_unit_price is the per-line labour
price; the labour total is always Qty x price, computed, not stored.
Guarded/idempotent like the earlier migrations (runs on every startup).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011_labour"
down_revision: Union[str, None] = "0010_concurrency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "include_labour" not in _columns("documents"):
        op.add_column("documents", sa.Column("include_labour", sa.Boolean, nullable=False, server_default=sa.false()))
    if "labour_unit_price" not in _columns("line_items"):
        op.add_column("line_items", sa.Column("labour_unit_price", sa.Numeric(12, 2)))


def downgrade() -> None:
    if "labour_unit_price" in _columns("line_items"):
        op.drop_column("line_items", "labour_unit_price")
    if "include_labour" in _columns("documents"):
        op.drop_column("documents", "include_labour")
