"""unified namespace publications: a second reader of the outbox

Revision ID: a4d9c2e70f18
Revises: 153379d6cf19
Create Date: 2026-09-09

The ERP marks its delivery progress on the outbox message itself, so a
second consumer cannot share it — whichever delivered first would hide the
message from the other. The unified-namespace publisher keeps its own row
per outbox message, with the same attempts/backoff/dead discipline.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4d9c2e70f18"
down_revision: str | None = "153379d6cf19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "uns_publications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(length=400), nullable=True),
        sa.Column("status", sa.Enum("pending", "sent", "processed", "error", "dead",
                                    name="messagestatus", native_enum=False, length=30),
                  nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.String(length=400), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["message_id"], ["erp_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_uns_publications_message_id", "uns_publications", ["message_id"], unique=True)
    op.create_index("ix_uns_publications_status", "uns_publications", ["status"])


def downgrade() -> None:
    op.drop_index("ix_uns_publications_status", table_name="uns_publications")
    op.drop_index("ix_uns_publications_message_id", table_name="uns_publications")
    op.drop_table("uns_publications")
