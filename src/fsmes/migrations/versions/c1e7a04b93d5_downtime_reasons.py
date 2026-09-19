"""The plant's downtime vocabulary, and the code beside the text.

Two additive changes, and nothing is rewritten:

* `downtime_reasons` - one revision of one reason per row, shaped like
  `documents`, so a list the whole plant's downtime is measured against is
  drafted by one person and put in force by another.
* `equipment_states.reason_code` - nullable, *beside* the existing free-text
  `reason` and never in place of it. Every interval a plant already recorded
  keeps exactly the text it has and gets a null code, which is the truth:
  those stops were not chosen from a list, because there was no list.

Revision ID: c1e7a04b93d5
Revises: b1f4c7a2e903
Create Date: 2026-09-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c1e7a04b93d5"
down_revision: str | None = "b1f4c7a2e903"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "downtime_reasons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        # VARCHAR, not a native enum: `str_enum` stores StrEnum values as
        # plain text so the same schema works on SQLite and PostgreSQL.
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("retires", sa.Boolean(), nullable=False),
        sa.Column("labels_intervals", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("approved_by", sa.String(length=40), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("on_behalf_of", sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "revision", name="uq_downtime_reason_revision"),
    )
    with op.batch_alter_table("equipment_states") as batch:
        batch.add_column(sa.Column("reason_code", sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("equipment_states") as batch:
        batch.drop_column("reason_code")
    op.drop_table("downtime_reasons")
