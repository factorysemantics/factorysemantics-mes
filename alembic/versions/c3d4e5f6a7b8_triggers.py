"""triggers: conditions as data, actions from a catalog, every firing kept

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "triggers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=40), nullable=False, unique=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("equipment_code", sa.String(length=40), nullable=True),
        sa.Column("tag", sa.String(length=60), nullable=False),
        sa.Column("condition", sa.String(length=20), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("sustained_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("cooldown_seconds", sa.Float(), nullable=False, server_default="300"),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("action_params", sa.JSON(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(length=40), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("approved_by", sa.String(length=40), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("last_fired_at", sa.DateTime(), nullable=True),
        sa.Column("fire_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_triggers_equipment_code", "triggers", ["equipment_code"])
    op.create_index("ix_triggers_status", "triggers", ["status"])
    op.create_table(
        "trigger_firings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trigger_id", sa.Integer(), sa.ForeignKey("triggers.id"), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("equipment_code", sa.String(length=40), nullable=False),
        sa.Column("tag", sa.String(length=60), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("outcome", sa.JSON(), nullable=True),
    )
    op.create_index("ix_trigger_firings_trigger_ts", "trigger_firings", ["trigger_id", "ts"])


def downgrade() -> None:
    op.drop_index("ix_trigger_firings_trigger_ts", table_name="trigger_firings")
    op.drop_table("trigger_firings")
    op.drop_index("ix_triggers_status", table_name="triggers")
    op.drop_index("ix_triggers_equipment_code", table_name="triggers")
    op.drop_table("triggers")
