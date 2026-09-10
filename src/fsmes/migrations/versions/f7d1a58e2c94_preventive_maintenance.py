"""Preventive maintenance.

Revision ID: f7d1a58e2c94
Revises: e6b3c92d4f17

Plans that come due on use rather than on a date, and the work they raise.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7d1a58e2c94"
down_revision: str | None = "e6b3c92d4f17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "maintenance_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("equipment_id", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.Enum("runtime_hours", "calendar_days", "produced_qty",
                                     name="triggerkind"), nullable=False),
        sa.Column("interval", sa.Float(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("document_code", sa.String(length=60), nullable=True),
        sa.Column("expected_minutes", sa.Float(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_done_at", sa.DateTime(), nullable=True),
        sa.Column("last_done_runtime_hours", sa.Float(), nullable=True),
        sa.Column("last_done_qty", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["equipment_id"], ["equipment.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_maintenance_plans_equipment_id", "maintenance_plans",
                    ["equipment_id"])

    op.create_table(
        "maintenance_orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("equipment_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.Enum("preventive", "corrective",
                                  name="maintenancekind"), nullable=False),
        sa.Column("status", sa.Enum("due", "in_progress", "done", "skipped",
                                    name="maintenancestatus"), nullable=False),
        sa.Column("summary", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("raised_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("performed_by", sa.String(length=40), nullable=True),
        sa.Column("findings", sa.Text(), nullable=True),
        sa.Column("downtime_minutes", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["equipment_id"], ["equipment.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["maintenance_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_maintenance_orders_equipment_id", "maintenance_orders",
                    ["equipment_id"])
    op.create_index("ix_maint_equipment_status", "maintenance_orders",
                    ["equipment_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_maint_equipment_status", table_name="maintenance_orders")
    op.drop_index("ix_maintenance_orders_equipment_id", table_name="maintenance_orders")
    op.drop_table("maintenance_orders")
    op.drop_index("ix_maintenance_plans_equipment_id", table_name="maintenance_plans")
    op.drop_table("maintenance_plans")
