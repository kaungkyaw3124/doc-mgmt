"""add group_id (owning department) to documents, customers and projects

Revision ID: 0009_group_id
Revises: 0008_company_letterhead
Create Date: 2026-11-18

Nullable and indexed; nothing is backfilled. Existing rows stay NULL
("unassigned" — visible to superusers only) until a superuser assigns them
with POST /documents/assign-unassigned (scripts/assign-unassigned.sh).
group_id is an auth-service groups.id, so there is no foreign key (other
database). Guarded/idempotent like the earlier migrations.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0009_group_id"
down_revision: Union[str, None] = "0008_company_letterhead"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("documents", "customers", "projects")


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    for table in TABLES:
        insp = _inspector()
        if "group_id" not in {c["name"] for c in insp.get_columns(table)}:
            op.add_column(table, sa.Column("group_id", UUID(as_uuid=True)))
        if f"ix_{table}_group_id" not in {i["name"] for i in _inspector().get_indexes(table)}:
            op.create_index(f"ix_{table}_group_id", table, ["group_id"])


def downgrade() -> None:
    for table in TABLES:
        insp = _inspector()
        if f"ix_{table}_group_id" in {i["name"] for i in insp.get_indexes(table)}:
            op.drop_index(f"ix_{table}_group_id", table_name=table)
        if "group_id" in {c["name"] for c in _inspector().get_columns(table)}:
            op.drop_column(table, "group_id")
