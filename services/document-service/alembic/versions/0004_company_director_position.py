"""add company_directors.position

Revision ID: 0004_director_position
Revises: 0003_document_deleted_by_at
Create Date: 2026-10-09

The quotation's Supplier block shows the position of the Managing Director
selected on the document (documents.director_id -> company_directors).
The position lives on the director row — there is deliberately no
documents.position column, so it can never drift from the selected MD.

Nullable with no default: directors (and therefore documents) that existed
before this migration have NULL, and the quotation simply omits the
Position row for them. Guarded/idempotent like 0002 — a no-op where
Base.metadata.create_all() already created the column.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_director_position"
down_revision: Union[str, None] = "0003_document_deleted_by_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(table_name):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    if "position" not in _existing_columns("company_directors"):
        op.add_column("company_directors", sa.Column("position", sa.String(length=100)))


def downgrade() -> None:
    if "position" in _existing_columns("company_directors"):
        op.drop_column("company_directors", "position")
