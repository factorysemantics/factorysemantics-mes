"""recommended adjustments: the only path to a PLC

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recommended_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=40), nullable=False, unique=True),
        sa.Column("equipment_code", sa.String(length=40), nullable=False),
        sa.Column("tag", sa.String(length=60), nullable=False),
        sa.Column("drives", sa.String(length=60), nullable=True),
        sa.Column("current_value", sa.Float(), nullable=True),
        sa.Column("proposed_value", sa.Float(), nullable=False),
        sa.Column("minimum", sa.Float(), nullable=False),
        sa.Column("maximum", sa.Float(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("proposed_by", sa.String(length=40), nullable=False),
        sa.Column("proposed_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="proposed"),
        sa.Column("decided_by", sa.String(length=40), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("written_at", sa.DateTime(), nullable=True),
        sa.Column("written_value", sa.Float(), nullable=True),
        sa.Column("verify_after_seconds", sa.Float(), nullable=False, server_default="120"),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("verification", sa.JSON(), nullable=True),
    )
    op.create_index("ix_recommended_adjustments_equipment_code", "recommended_adjustments", ["equipment_code"])
    op.create_index("ix_recommended_adjustments_status", "recommended_adjustments", ["status"])


def downgrade() -> None:
    op.drop_index("ix_recommended_adjustments_status", table_name="recommended_adjustments")
    op.drop_index("ix_recommended_adjustments_equipment_code", table_name="recommended_adjustments")
    op.drop_table("recommended_adjustments")
