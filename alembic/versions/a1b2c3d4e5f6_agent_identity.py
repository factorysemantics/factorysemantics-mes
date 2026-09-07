"""agent identity: on_behalf_of on the audit trail, and idempotency keys

Revision ID: a1b2c3d4e5f6
Revises: f2a8d31c6b74
Create Date: 2026-09-02

An agent acts for someone, and the record says who. A repeated command
returns its first answer instead of running twice.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f2a8d31c6b74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column("on_behalf_of", sa.String(length=40), nullable=True))
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor", sa.String(length=40), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=200), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("body", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("actor", "key", name="uq_idempotency_actor_key"),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.drop_column("on_behalf_of")
