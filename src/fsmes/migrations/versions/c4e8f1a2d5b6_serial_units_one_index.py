"""serial_units: the serial column carries one index, not two

The model declared unique=True and index=True together, which built the
unique index and a second plain one on the same column - the largest index
on the largest table, for nothing.

Revision ID: c4e8f1a2d5b6
Revises: b7d2e9f4a1c3
Create Date: 2026-09-06 18:40:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = 'c4e8f1a2d5b6'
down_revision: str | None = 'b7d2e9f4a1c3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index('ix_serial_units_serial', table_name='serial_units')


def downgrade() -> None:
    op.create_index('ix_serial_units_serial', 'serial_units', ['serial'], unique=False)
