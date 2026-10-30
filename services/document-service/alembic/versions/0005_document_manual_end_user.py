"""add documents.end_user_* (one-time manual customer)

Revision ID: 0005_document_manual_end_user
Revises: 0004_director_position
Create Date: 2026-10-29

A document can carry a customer typed by hand for that document only,
instead of linking a saved Customer (documents.customer_id). The five
fields live on the document row; nothing is ever written to customers.

All nullable, no default: existing documents keep NULL and print exactly
as before. Guarded/idempotent like 0004 — each column is only added if it
is missing, so a second `alembic upgrade head` (it runs at every startup)
or a database whose columns came from Base.metadata.create_all() is a no-op.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_document_manual_end_user"
down_revision: Union[str, None] = "0004_director_position"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("end_user_name", sa.String(length=255)),
    ("end_user_organization", sa.String(length=255)),
    ("end_user_address", sa.Text()),
    ("end_user_phone", sa.String(length=50)),
    ("end_user_email", sa.String(length=255)),
)


def _existing_columns(table_name):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    existing = _existing_columns("documents")
    for name, type_ in _COLUMNS:
        if name not in existing:
            op.add_column("documents", sa.Column(name, type_))


def downgrade() -> None:
    existing = _existing_columns("documents")
    for name, _ in _COLUMNS:
        if name in existing:
            op.drop_column("documents", name)
