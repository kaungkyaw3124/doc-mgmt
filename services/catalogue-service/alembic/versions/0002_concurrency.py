"""safe simultaneous use: SKU counters, idempotency keys, product versions

Revision ID: 0002_concurrency
Revises: 0001_product_group_id
Create Date: 2026-11-18

- counters: one row per SKU prefix ("sku:COM"), seeded here from the highest
  SKU number already used, so numbering continues without reuse (prefixes not
  seen here are seeded on first use, see app/core/concurrency.py).
- idempotency_keys: Idempotency-Key header replay for POST /products.
- products.version: NOT NULL, starts at 1 (optimistic locking).
- products.sku unique: create_all always made it, so this is only a guard.
startup runs create_all() first, so on a new database the tables already
exist; every step is guarded, like 0001.
"""
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0002_concurrency"
down_revision: Union[str, None] = "0001_product_group_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _insp():
    return sa.inspect(op.get_bind())


def _has_unique(table, column):
    insp = _insp()
    return any(u["column_names"] == [column] for u in insp.get_unique_constraints(table)) or any(
        i["unique"] and i["column_names"] == [column] for i in insp.get_indexes(table)
    )


def upgrade() -> None:
    tables = set(_insp().get_table_names())
    if "counters" not in tables:
        op.create_table("counters", sa.Column("name", sa.String(120), primary_key=True), sa.Column("value", sa.Integer, nullable=False))
    if "idempotency_keys" not in tables:
        op.create_table(
            "idempotency_keys",
            sa.Column("key", sa.String(255), primary_key=True),
            sa.Column("resource_id", UUID(as_uuid=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "ix_idempotency_keys_created_at" not in {i["name"] for i in _insp().get_indexes("idempotency_keys")}:
        op.create_index("ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"])

    if "version" not in {c["name"] for c in _insp().get_columns("products")}:
        op.add_column("products", sa.Column("version", sa.Integer, nullable=False, server_default="1"))

    if not _has_unique("products", "sku"):
        op.create_unique_constraint("uq_products_sku", "products", ["sku"])

    # Seed: highest N per "<prefix>-N" SKU already in use.
    highest = {}
    for (sku,) in op.get_bind().execute(sa.text("SELECT sku FROM products")):
        match = re.fullmatch(r"(.+)-(\d+)", sku or "")
        if match:
            name = "sku:" + match.group(1)
            highest[name] = max(highest.get(name, 0), int(match.group(2)))
    for name, value in highest.items():
        op.get_bind().execute(
            sa.text("INSERT INTO counters (name, value) VALUES (:n, :v) ON CONFLICT (name) DO NOTHING"), {"n": name, "v": value}
        )


def downgrade() -> None:
    for table in ("idempotency_keys", "counters"):
        if table in _insp().get_table_names():
            op.drop_table(table)
    if "version" in {c["name"] for c in _insp().get_columns("products")}:
        op.drop_column("products", "version")
