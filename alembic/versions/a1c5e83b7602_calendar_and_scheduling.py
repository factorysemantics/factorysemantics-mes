"""Shift calendar and finite-capacity scheduling.

Revision ID: a1c5e83b7602
Revises: f7d1a58e2c94
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c5e83b7602"
down_revision: str | None = "f7d1a58e2c94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shift_patterns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("starts", sa.Time(), nullable=False),
        sa.Column("ends", sa.Time(), nullable=False),
        sa.Column("days", sa.String(length=7), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("equipment_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["equipment_id"], ["equipment.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "calendar_exceptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("kind", sa.Enum("non_working", "working", name="exceptionkind"),
                  nullable=False),
        sa.Column("reason", sa.String(length=160), nullable=False),
        sa.Column("equipment_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["equipment_id"], ["equipment.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_calendar_exceptions_day", "calendar_exceptions", ["day"])

    op.create_table(
        "scheduled_slots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("equipment_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Enum("production", "maintenance", name="slotkind"),
                  nullable=False),
        sa.Column("work_order_id", sa.Integer(), nullable=True),
        sa.Column("operation_id", sa.Integer(), nullable=True),
        sa.Column("maintenance_order_id", sa.Integer(), nullable=True),
        sa.Column("planned_start", sa.DateTime(), nullable=False),
        sa.Column("planned_end", sa.DateTime(), nullable=False),
        sa.Column("minutes", sa.Float(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("planned_at", sa.DateTime(), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["equipment_id"], ["equipment.id"]),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"]),
        sa.ForeignKeyConstraint(["operation_id"], ["work_order_operations.id"]),
        sa.ForeignKeyConstraint(["maintenance_order_id"], ["maintenance_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheduled_slots_equipment_id", "scheduled_slots", ["equipment_id"])
    op.create_index("ix_scheduled_slots_planned_start", "scheduled_slots", ["planned_start"])
    op.create_index("ix_slot_equipment_start", "scheduled_slots",
                    ["equipment_id", "planned_start"])


def downgrade() -> None:
    op.drop_index("ix_slot_equipment_start", table_name="scheduled_slots")
    op.drop_index("ix_scheduled_slots_planned_start", table_name="scheduled_slots")
    op.drop_index("ix_scheduled_slots_equipment_id", table_name="scheduled_slots")
    op.drop_table("scheduled_slots")
    op.drop_index("ix_calendar_exceptions_day", table_name="calendar_exceptions")
    op.drop_table("calendar_exceptions")
    op.drop_table("shift_patterns")
