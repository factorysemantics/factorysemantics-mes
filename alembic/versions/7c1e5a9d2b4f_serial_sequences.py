"""serial_sequences: a counter per serial prefix

next_serial counted every unit with the prefix to number the next one -
a scan per unit, quadratic over a day of serialised production.

Revision ID: 7c1e5a9d2b4f
Revises: f0a1b2c3d4e5
Create Date: 2026-09-05 05:40:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '7c1e5a9d2b4f'
down_revision: str | None = 'f0a1b2c3d4e5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'serial_sequences',
        sa.Column('prefix', sa.String(length=60), nullable=False),
        sa.Column('next', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('prefix'),
    )


def downgrade() -> None:
    op.drop_table('serial_sequences')
