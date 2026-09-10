"""Equipment carries a cost center.

Who pays for what a machine does, inherited down the tree so a plant sets it
once per line and overrides only where accounting differs. A code, never an
amount: the MES reports quantities and time against a cost center and the ERP
owns what they are worth.

Revision ID: d4a1c7e5b902
Revises: c2e9b74a3f81
"""

import sqlalchemy as sa
from alembic import op

revision = "d4a1c7e5b902"
down_revision = "c2e9b74a3f81"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("equipment", sa.Column("cost_center", sa.String(length=40), nullable=True))
    op.create_index("ix_equipment_cost_center", "equipment", ["cost_center"])


def downgrade() -> None:
    op.drop_index("ix_equipment_cost_center", table_name="equipment")
    op.drop_column("equipment", "cost_center")
