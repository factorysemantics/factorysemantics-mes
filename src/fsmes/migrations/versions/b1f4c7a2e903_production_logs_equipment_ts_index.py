"""Two indexes the floor screen's own questions ask for

production_logs (equipment_id, ts): every OEE window asks this table for one
machine's bookings between two instants. On `equipment_id` alone that reads
every booking the machine ever made and filters to the window afterwards, so
the cost of a screen refresh grows with the whole history rather than with the
window it is about.

tag_values (equipment_id, id): the machine card's "what did this machine last
publish" on a plant whose tag map names no process value for it. Without this
the database read every tag row the machine had ever written and sorted them
to find the newest.

Revision ID: b1f4c7a2e903
Revises: c3d7e91a4b28
Create Date: 2026-09-18 05:10:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = 'b1f4c7a2e903'
down_revision: str | None = 'c3d7e91a4b28'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index('ix_production_logs_eq_ts', 'production_logs', ['equipment_id', 'ts'], unique=False)
    op.create_index('ix_tag_values_eq_id', 'tag_values', ['equipment_id', 'id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_tag_values_eq_id', table_name='tag_values')
    op.drop_index('ix_production_logs_eq_ts', table_name='production_logs')
