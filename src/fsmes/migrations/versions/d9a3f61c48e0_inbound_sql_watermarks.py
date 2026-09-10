"""where a polling inbound driver has read one supplier's table through

The folder driver needs no cursor: a file is read once and moved. A poller
has no such mark on the world, so it keeps its own — one row per
`(source, stream)` holding the supplier's own ordering value as text.

Text, not a typed column, because the value goes back to the supplier's
database in the next query and has to arrive unchanged. A timestamp this
MES normalised into its own convention would move the boundary by the
supplier's UTC offset, and the rows in that gap would be skipped or
repeated with nothing to say so.

Additive: one new table, nothing existing touched. A plant that never runs
the SQL poller has an empty table and reads no differently than before.

Revision ID: d9a3f61c48e0
Revises: b5c1d09e73af
Create Date: 2026-09-10 12:40:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd9a3f61c48e0'
down_revision: str | None = 'b5c1d09e73af'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'inbound_watermarks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=80), nullable=False),
        sa.Column('stream', sa.String(length=30), nullable=False),
        sa.Column('position', sa.String(length=120), nullable=False),
        sa.Column('position_type', sa.String(length=20), nullable=False),
        sa.Column('rows_seen', sa.Integer(), nullable=False),
        sa.Column('held_reason', sa.String(length=300), nullable=True),
        sa.Column('held_key', sa.String(length=120), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source', 'stream', name='uq_inbound_watermark_source_stream'),
    )
    op.create_index('ix_inbound_watermarks_source', 'inbound_watermarks', ['source'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_inbound_watermarks_source', table_name='inbound_watermarks')
    op.drop_table('inbound_watermarks')
