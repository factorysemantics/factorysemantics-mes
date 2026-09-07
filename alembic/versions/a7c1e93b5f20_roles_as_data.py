"""Roles as data, so a plant can define its own.

Revision ID: a7c1e93b5f20
Revises: 9c4d2c836d05

The MES gated on a role ladder, which cannot express "records inspections and
nothing else". This adds the table that lets a role be a bundle of
capabilities; the built-ins are seeded by ensure_builtin_roles() on every
start, so this migration only has to make the shape.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c1e93b5f20"
down_revision: str | None = "9c4d2c836d05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=400), nullable=True),
        sa.Column("capabilities", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )


def downgrade() -> None:
    op.drop_table("roles")
