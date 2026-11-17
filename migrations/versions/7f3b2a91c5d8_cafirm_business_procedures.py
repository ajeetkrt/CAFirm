"""CAFirm business procedures (usp_* API layer)

Every API endpoint calls one named stored procedure. The SQL statements live
in ``app.db.procedures`` — the single source of truth — and are installed
verbatim here (production) and by the test fixtures (cafarm_test).

Revision ID: 7f3b2a91c5d8
Revises: ed98814725c4
Create Date: 2026-11-13
"""
from alembic import op

from app.database.procedures import (
    ALL_STATEMENTS,
    DROP_STATEMENTS,
    PGCRYPTO_SQL,
    SEQUENCE_SQL,
    TIMESTAMP_DEFAULTS_SQL,
)

# revision identifiers, used by Alembic.
revision = '7f3b2a91c5d8'
down_revision = 'ed98814725c4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in (PGCRYPTO_SQL, SEQUENCE_SQL, *TIMESTAMP_DEFAULTS_SQL, *ALL_STATEMENTS):
        op.execute(statement)


def downgrade() -> None:
    for statement in DROP_STATEMENTS:
        op.execute(statement)
