"""What the plant's AI did, one row per turn.

Revision ID: b3f9c07e51ad
Revises: a1c5e70b2d94

The trace behind the AI workspace: every turn of every conversation the
assistant and the design chat have had on this plant, with the tool calls,
the proposals and their outcomes, the errors and the cost. Written by the
agent as it answers; read by `/dashboard/ai` and by `fsmes ai`.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3f9c07e51ad"
down_revision: str | None = "a1c5e70b2d94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_turns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("session", sa.String(length=40), nullable=False),
        sa.Column("brain", sa.String(length=20), nullable=False),
        sa.Column("person", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=60), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("asked", sa.Text(), nullable=False),
        sa.Column("said", sa.Text(), nullable=False),
        sa.Column("tools", sa.JSON(), nullable=True),
        sa.Column("proposals", sa.JSON(), nullable=True),
        sa.Column("guide", sa.String(length=60), nullable=True),
        sa.Column("guide_steps", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(length=60), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("usd", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_turns_session", "ai_turns", ["session", "id"])
    op.create_index("ix_ai_turns_ts", "ai_turns", ["ts"])
    op.create_index("ix_ai_turns_person", "ai_turns", ["person"])


def downgrade() -> None:
    op.drop_index("ix_ai_turns_person", table_name="ai_turns")
    op.drop_index("ix_ai_turns_ts", table_name="ai_turns")
    op.drop_index("ix_ai_turns_session", table_name="ai_turns")
    op.drop_table("ai_turns")
