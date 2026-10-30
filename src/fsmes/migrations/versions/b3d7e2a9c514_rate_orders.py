"""rate_orders and rate_order_lines: a standing order for a mix of parts

A rate order is a name and, per item, a target rate in units a minute. It has
no quantity, no due date and no status, and never closes. A plant with none
has no target.

Revision ID: b3d7e2a9c514
Revises: a9c4e1f7b2d3
Create Date: 2026-10-30 08:30:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b3d7e2a9c514'
down_revision: str | None = 'a9c4e1f7b2d3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'rate_orders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=40), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by', sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )
    op.create_table(
        'rate_order_lines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('rate_order_id', sa.Integer(), nullable=False),
        sa.Column('item', sa.String(length=80), nullable=False),
        sa.Column('per_minute', sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(['rate_order_id'], ['rate_orders.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('rate_order_id', 'item'),
    )
    op.create_index('ix_rate_order_lines_rate_order_id', 'rate_order_lines', ['rate_order_id'])


def downgrade() -> None:
    op.drop_index('ix_rate_order_lines_rate_order_id', table_name='rate_order_lines')
    op.drop_table('rate_order_lines')
    op.drop_table('rate_orders')
