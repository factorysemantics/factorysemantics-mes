"""unit_inspections: one automated inspection per identified unit

Revision ID: b7d2e9f4a1c3
Revises: 7c1e5a9d2b4f
Create Date: 2026-09-06 15:10:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b7d2e9f4a1c3'
down_revision: str | None = '7c1e5a9d2b4f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'unit_inspections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('unit_id', sa.Integer(), nullable=False),
        sa.Column('equipment_id', sa.Integer(), nullable=False),
        sa.Column('seq', sa.BigInteger(), nullable=False),
        sa.Column('ts', sa.DateTime(), nullable=False),
        sa.Column('passed', sa.Boolean(), nullable=False),
        sa.Column('fail_mask', sa.Integer(), nullable=False),
        sa.Column('values', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['unit_id'], ['serial_units.id']),
        sa.ForeignKeyConstraint(['equipment_id'], ['equipment.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_unit_inspections_equipment_ts', 'unit_inspections', ['equipment_id', 'ts'])
    op.create_index('ix_unit_inspections_unit', 'unit_inspections', ['unit_id'])


def downgrade() -> None:
    op.drop_index('ix_unit_inspections_unit', table_name='unit_inspections')
    op.drop_index('ix_unit_inspections_equipment_ts', table_name='unit_inspections')
    op.drop_table('unit_inspections')
