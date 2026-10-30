"""fill in missing Managing Director positions

Revision ID: 0007_md_position_default
Revises: 0006_customer_organization
Create Date: 2026-10-30

Since loop iteration 12 an MD's position is required and the company-level
"Signer position" (companies.position) is no longer used as a fallback on
quotations. So that existing quotations keep printing what they printed,
every MD whose position is NULL/blank gets the company's position — unless
that is blank or just the company's name (data typed into the wrong field),
in which case "Managing Director". No column is added or dropped;
companies.position is kept (unused). Idempotent: a second run finds no MD
without a position and changes nothing.
"""
import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_md_position_default"
down_revision: Union[str, None] = "0006_customer_organization"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    result = op.get_bind().execute(sa.text("""
        UPDATE company_directors
        SET position = COALESCE((
            SELECT TRIM(c.position) FROM companies c
            WHERE c.id = company_directors.company_id
              AND TRIM(COALESCE(c.position, '')) <> ''
              AND LOWER(TRIM(c.position)) <> LOWER(TRIM(c.name))
        ), 'Managing Director')
        WHERE position IS NULL OR TRIM(position) = ''
    """))
    logger.info("0007: filled in the position of %d Managing Director(s)", result.rowcount)


def downgrade() -> None:
    pass  # the old values were NULL/blank; nothing meaningful to restore
