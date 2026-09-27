"""scholarship ledger entries

Revision ID: b3e1c2d4a5f6
Revises: 5902bb4765f1
Create Date: 2026-09-27 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b3e1c2d4a5f6'
down_revision: Union[str, Sequence[str], None] = '5902bb4765f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Allow 'scholarship' ledger entries (admin credits a student's wallet)."""
    op.drop_constraint(op.f('ck_ledger_entries_type'), 'ledger_entries', type_='check')
    op.create_check_constraint(
        op.f('ck_ledger_entries_type'), 'ledger_entries', "type IN ('topup', 'purchase', 'refund', 'adjustment', 'scholarship')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE ledger_entries SET type = 'adjustment' WHERE type = 'scholarship'")
    op.drop_constraint(op.f('ck_ledger_entries_type'), 'ledger_entries', type_='check')
    op.create_check_constraint(op.f('ck_ledger_entries_type'), 'ledger_entries', "type IN ('topup', 'purchase', 'refund', 'adjustment')")
