"""documents: kind, steps, needs - recorded walkthroughs

Revision ID: a7c2e5d1b9f0
Revises: e5f6a7b8c9d0
Create Date: 2026-09-04 09:30:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a7c2e5d1b9f0'
down_revision: str | None = 'e5f6a7b8c9d0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=20), nullable=False, server_default='instruction'))
        batch_op.add_column(sa.Column('steps', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('needs', sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_column('needs')
        batch_op.drop_column('steps')
        batch_op.drop_column('kind')
