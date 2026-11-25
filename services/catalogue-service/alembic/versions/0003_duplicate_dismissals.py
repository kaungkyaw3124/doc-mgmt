""""Not a duplicate" dismissals for same-name products

Revision ID: 0003_duplicate_dismissals
Revises: 0002_concurrency
Create Date: 2026-11-20

startup runs create_all() first, so the table normally exists already;
every step is guarded, like 0001/0002.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0003_duplicate_dismissals"
down_revision: Union[str, None] = "0002_concurrency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "product_duplicate_dismissals"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names():
        op.create_table(
            TABLE,
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("group_id", UUID(as_uuid=True)),
            sa.Column("product_a_id", UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
            sa.Column("product_b_id", UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
            sa.Column("fingerprint_a", sa.String(64), nullable=False),
            sa.Column("fingerprint_b", sa.String(64), nullable=False),
            sa.Column("created_by", sa.String(150)),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("product_a_id", "product_b_id", name="uq_dismissal_pair"),
        )
    indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(TABLE)}
    for col in ("group_id", "product_b_id"):
        if f"ix_{TABLE}_{col}" not in indexes:
            op.create_index(f"ix_{TABLE}_{col}", TABLE, [col])


def downgrade() -> None:
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table(TABLE)
