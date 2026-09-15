"""equipment_connections: whether the MES can see a machine, separately from what it is doing

The connection is a second interval history beside equipment_states, not a
fifth state inside it. A machine can be broken and reachable, or fine and
unreachable. See decision 0028.

Revision ID: c3d7e91a4b28
Revises: b1f4c73a9e08
Create Date: 2026-09-14 19:20:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d7e91a4b28"
down_revision: str | None = "b1f4c73a9e08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "equipment_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("equipment_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.Enum("connected", "disconnected", name="connectionstatename",
                                   native_enum=False, length=30), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("source", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["equipment_id"], ["equipment.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_equipment_connections_eq_started", "equipment_connections",
                    ["equipment_id", "started_at"], unique=False)
    op.create_index("ix_equipment_connections_ended", "equipment_connections",
                    ["ended_at"], unique=False)
    # Nothing is back-filled. A database upgraded into this revision has no
    # connection facts about any of its machines, and "unknown" is the honest
    # answer for every one of them until an agent says otherwise. Writing
    # `connected` rows for the machines that happen to have an open state now
    # would be inventing an observation nobody made.


def downgrade() -> None:
    op.drop_index("ix_equipment_connections_ended", table_name="equipment_connections")
    op.drop_index("ix_equipment_connections_eq_started", table_name="equipment_connections")
    op.drop_table("equipment_connections")
