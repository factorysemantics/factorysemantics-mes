"""inbound events: the ledger, and where each number came from

A second front door beside the OPC agent: downtime labels, quality results
and counts supplied by another system. Three additive columns say which
system supplied a number, one says what verdict came with a reading, and one
new table records what has already been applied so the same file dropped
twice changes nothing.

Every column is nullable and nothing existing is rewritten: a row from
before this revision was observed by this MES, and null is exactly the right
answer to "which other system told us".

Revision ID: b5c1d09e73af
Revises: a3f6c81d09e2
Create Date: 2026-09-10 05:20:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b5c1d09e73af'
down_revision: str | None = 'a3f6c81d09e2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('production_logs', sa.Column('source_system', sa.String(length=80), nullable=True))
    op.add_column('equipment_states', sa.Column('reason_source', sa.String(length=80), nullable=True))
    op.add_column('quality_checks', sa.Column('source_system', sa.String(length=80), nullable=True))
    op.add_column('quality_checks', sa.Column(
        'supplied_result',
        sa.Enum('pass', 'fail', name='checkresult', native_enum=False, length=30),
        nullable=True))
    op.create_table(
        'inbound_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=80), nullable=False),
        sa.Column('kind', sa.Enum('downtime_label', 'quality_result', 'manual_count',
                                  name='inboundkind', native_enum=False, length=30), nullable=False),
        sa.Column('external_key', sa.String(length=120), nullable=False),
        sa.Column('recorded_at', sa.DateTime(), nullable=False),
        sa.Column('entity_type', sa.String(length=30), nullable=True),
        sa.Column('entity_id', sa.String(length=60), nullable=True),
        sa.Column('detail', sa.String(length=300), nullable=True),
        sa.Column('applied_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source', 'kind', 'external_key', name='uq_inbound_source_kind_key'),
    )
    op.create_index('ix_inbound_events_source', 'inbound_events', ['source'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_inbound_events_source', table_name='inbound_events')
    op.drop_table('inbound_events')
    op.drop_column('quality_checks', 'supplied_result')
    op.drop_column('quality_checks', 'source_system')
    op.drop_column('equipment_states', 'reason_source')
    op.drop_column('production_logs', 'source_system')
