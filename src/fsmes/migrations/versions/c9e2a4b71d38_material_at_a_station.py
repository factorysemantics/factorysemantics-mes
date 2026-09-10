"""Material goes in at a station.

Revision ID: c9e2a4b71d38
Revises: b3f8d2a41c67

A bill of materials that cannot say which station consumes a component is a
BOM for a job shop, not for a line. Both columns are nullable: a consumable
nobody tracks to a station is a real case, and inventing one would be worse
than leaving it null.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9e2a4b71d38"
down_revision: str | None = "b3f8d2a41c67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bom_items") as batch:
        batch.add_column(sa.Column("operation_seq", sa.Integer(), nullable=True))
    with op.batch_alter_table("lot_consumptions") as batch:
        batch.add_column(sa.Column("operation_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("equipment_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_consumption_operation", "work_order_operations",
                                 ["operation_id"], ["id"])
        batch.create_foreign_key("fk_consumption_equipment", "equipment",
                                 ["equipment_id"], ["id"])
    op.create_index("ix_lot_consumptions_operation", "lot_consumptions", ["operation_id"])


def downgrade() -> None:
    op.drop_index("ix_lot_consumptions_operation", table_name="lot_consumptions")
    with op.batch_alter_table("lot_consumptions") as batch:
        batch.drop_column("equipment_id")
        batch.drop_column("operation_id")
    with op.batch_alter_table("bom_items") as batch:
        batch.drop_column("operation_seq")
