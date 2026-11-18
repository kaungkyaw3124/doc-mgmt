"""safe simultaneous use: number counters, idempotency keys, row versions

Revision ID: 0010_concurrency
Revises: 0009_group_id
Create Date: 2026-11-18

- counters: one row per document-number prefix+day ("doc:SS-20261118/"),
  seeded here from the highest number already used, so numbering continues
  without reuse. (Prefixes not seen here are seeded on first use, see
  app/core/concurrency.py.)
- idempotency_keys: Idempotency-Key header replay for create endpoints.
- version on documents/customers/projects/companies: NOT NULL, starts at 1.
  documents.version existed (nullable, never used) — NULLs become 1, because
  the ORM's version check (`WHERE version = NULL`) would never match them.
- doc_number unique: create_all always made it, so this is only a guard.
Guarded/idempotent like the earlier migrations (runs on every startup).
"""
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0010_concurrency"
down_revision: Union[str, None] = "0009_group_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSIONED = ("documents", "customers", "projects", "companies")


def _insp():
    return sa.inspect(op.get_bind())


def _columns(table):
    return {c["name"] for c in _insp().get_columns(table)}


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

    for table in VERSIONED:
        if "version" not in _columns(table):
            op.add_column(table, sa.Column("version", sa.Integer, nullable=False, server_default="1"))
        else:
            op.execute(f"UPDATE {table} SET version = 1 WHERE version IS NULL")
            op.alter_column(table, "version", existing_type=sa.Integer, nullable=False, server_default="1")

    if not _has_unique("documents", "doc_number"):
        op.create_unique_constraint("uq_documents_doc_number", "documents", ["doc_number"])

    # Seed: highest sequence per "<prefix>-<YYYYMMDD>/" already in use.
    highest = {}
    for (number,) in op.get_bind().execute(sa.text("SELECT doc_number FROM documents")):
        match = re.fullmatch(r"(.+/)(\d+)", number or "")
        if match:
            name = "doc:" + match.group(1)
            highest[name] = max(highest.get(name, 0), int(match.group(2)))
    for name, value in highest.items():
        op.get_bind().execute(
            sa.text("INSERT INTO counters (name, value) VALUES (:n, :v) ON CONFLICT (name) DO NOTHING"), {"n": name, "v": value}
        )


def downgrade() -> None:
    for table in ("idempotency_keys", "counters"):
        if table in _insp().get_table_names():
            op.drop_table(table)
    for table in ("customers", "projects", "companies"):
        if "version" in _columns(table):
            op.drop_column(table, "version")
    # documents.version predates this revision: back to nullable, kept.
    op.alter_column("documents", "version", existing_type=sa.Integer, nullable=True, server_default=None)
