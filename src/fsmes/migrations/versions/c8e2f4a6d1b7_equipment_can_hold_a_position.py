"""equipment: pos_x, pos_y, pos_z and heading

Where a machine stands: x and y across the floor and z its height, in
metres, and its heading in degrees. All nullable: a machine nobody has
placed has no position, and the line view lays it out as before.

Revision ID: c8e2f4a6d1b7
Revises: b3d7e2a9c514
Create Date: 2026-10-31 16:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c8e2f4a6d1b7'
down_revision: str | None = 'b3d7e2a9c514'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ('pos_x', 'pos_y', 'pos_z', 'heading')


def upgrade() -> None:
    with op.batch_alter_table('equipment') as batch:
        for name in COLUMNS:
            batch.add_column(sa.Column(name, sa.Float(), nullable=True,
                                       server_default=sa.text('NULL')))


def downgrade() -> None:
    with op.batch_alter_table('equipment') as batch:
        for name in reversed(COLUMNS):
            batch.drop_column(name)
