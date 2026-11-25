"""products.unit (No, M, Set …)

Revision ID: 0004_product_unit
Revises: 0003_duplicate_dismissals
Create Date: 2026-11-25

Existing products get "No" (server default). Guarded like 0001-0003, since
startup runs create_all() + upgrade on every boot.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_product_unit"
down_revision: Union[str, None] = "0003_duplicate_dismissals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "unit" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("products")}:
        op.add_column("products", sa.Column("unit", sa.String(20), nullable=False, server_default="No"))


def downgrade() -> None:
    if "unit" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("products")}:
        op.drop_column("products", "unit")
