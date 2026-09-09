"""outbox hardening: attempts, backoff, message keys

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-03

A failing confirmation backs off and eventually dies instead of retrying
forever; a message key makes queueing idempotent.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("erp_messages", schema=None) as batch_op:
        batch_op.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("next_attempt_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("message_key", sa.String(length=120), nullable=True))
        batch_op.create_unique_constraint("uq_erp_messages_message_key", ["message_key"])


def downgrade() -> None:
    with op.batch_alter_table("erp_messages", schema=None) as batch_op:
        batch_op.drop_constraint("uq_erp_messages_message_key", type_="unique")
        batch_op.drop_column("message_key")
        batch_op.drop_column("next_attempt_at")
        batch_op.drop_column("attempts")
