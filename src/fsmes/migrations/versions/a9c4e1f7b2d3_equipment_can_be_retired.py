"""equipment: retired_at and retired_reason

A machine taken off the floor keeps its history and leaves the lists that
mean "the machines this plant has now". Both columns are nullable: every
machine that exists is on the floor.

Revision ID: a9c4e1f7b2d3
Revises: 2c6e817b9faf
Create Date: 2026-10-30 06:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a9c4e1f7b2d3'
down_revision: str | None = '2c6e817b9faf'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('equipment') as batch:
        batch.add_column(sa.Column('retired_at', sa.DateTime(), nullable=True,
                                   server_default=sa.text('NULL')))
        batch.add_column(sa.Column('retired_reason', sa.String(length=200), nullable=True,
                                   server_default=sa.text('NULL')))


def downgrade() -> None:
    with op.batch_alter_table('equipment') as batch:
        batch.drop_column('retired_reason')
        batch.drop_column('retired_at')
