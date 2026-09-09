"""equipment_states: index ended_at

The open-interval lookup (ended_at IS NULL) and the OEE window filter
(ended_at > start) scanned the whole table; after a day of a 108-station
plant that was 1.4 million rows per Floor refresh.

Revision ID: f0a1b2c3d4e5
Revises: e5f6a7b8c9d0
Create Date: 2026-09-05 05:10:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = 'f0a1b2c3d4e5'
down_revision: str | None = 'e5f6a7b8c9d0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index('ix_equipment_states_ended', 'equipment_states', ['ended_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_equipment_states_ended', table_name='equipment_states')
