"""The plant's non-conformance severity vocabulary.

One additive table, and nothing is rewritten. `non_conformances.severity`
keeps its column, its length and every value already in it: a hold raised as
`major` before this existed reads `major` after it, whatever the plant's list
comes to say. The vocabulary decides what may be written next, never what was
written before - the same rule the downtime reasons keep about an interval
they labelled.

A plant that applies no severity vocabulary behaves exactly as it does today:
the column takes what it is given.

Revision ID: d2b7e51c9a04
Revises: c1e7a04b93d5
Create Date: 2026-09-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d2b7e51c9a04"
down_revision: str | None = "c1e7a04b93d5"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "nc_severities",
        sa.Column("id", sa.Integer(), nullable=False),
        # Twenty, matching `non_conformances.severity`: a list that could
        # approve a word too long to store would refuse at the one moment it
        # mattered - a machine raising a hold.
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        # VARCHAR, not a native enum: `str_enum` stores StrEnum values as
        # plain text so the same schema works on SQLite and PostgreSQL.
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("retires", sa.Boolean(), nullable=False),
        sa.Column("labels_records", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("approved_by", sa.String(length=40), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("on_behalf_of", sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "revision", name="uq_nc_severity_revision"),
    )


def downgrade() -> None:
    op.drop_table("nc_severities")
