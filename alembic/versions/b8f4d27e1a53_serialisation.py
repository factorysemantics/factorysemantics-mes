"""Serialisation: tracing a thing, not a batch.

Revision ID: b8f4d27e1a53
Revises: a1c5e83b7602
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8f4d27e1a53"
down_revision: str | None = "a1c5e83b7602"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "serial_units",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("serial", sa.String(length=60), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.Enum("in_process", "good", "scrapped",
                                    "quarantined", "shipped",
                                    name="unitstatus"), nullable=False),
        sa.Column("produced_by_order_id", sa.Integer(), nullable=True),
        sa.Column("produced_at", sa.DateTime(), nullable=False),
        sa.Column("produced_on_id", sa.Integer(), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"]),
        sa.ForeignKeyConstraint(["produced_by_order_id"], ["work_orders.id"]),
        sa.ForeignKeyConstraint(["produced_on_id"], ["equipment.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["serial_units.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("serial"),
    )
    op.create_index("ix_serial_units_serial", "serial_units", ["serial"])
    op.create_index("ix_serial_units_material_id", "serial_units", ["material_id"])
    op.create_index("ix_serial_order_material", "serial_units",
                    ["produced_by_order_id", "material_id"])
    op.create_index("ix_serial_parent", "serial_units", ["parent_id"])

    op.create_table(
        "unit_components",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["unit_id"], ["serial_units.id"]),
        sa.ForeignKeyConstraint(["lot_id"], ["material_lots.id"]),
        sa.ForeignKeyConstraint(["operation_id"], ["work_order_operations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_unit_components_unit_id", "unit_components", ["unit_id"])
    op.create_index("ix_unit_components_lot_id", "unit_components", ["lot_id"])
    op.create_index("ix_unit_component_lot", "unit_components", ["lot_id"])


def downgrade() -> None:
    op.drop_table("unit_components")
    op.drop_index("ix_serial_parent", table_name="serial_units")
    op.drop_index("ix_serial_order_material", table_name="serial_units")
    op.drop_index("ix_serial_units_material_id", table_name="serial_units")
    op.drop_index("ix_serial_units_serial", table_name="serial_units")
    op.drop_table("serial_units")
