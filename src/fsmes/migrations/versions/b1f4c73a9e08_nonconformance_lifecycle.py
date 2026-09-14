"""A non-conformance has a life: who reviewed it, what was decided, who closed it.

Revision ID: b1f4c73a9e08
Revises: c8b1e40d7a92
Create Date: 2026-09-14

Eight nullable columns on `non_conformances` and no data change. The rows that
already exist keep their status — `open` or `closed` — and their new columns
stay null, which is the truth: this MES did not record who reviewed them,
because it did not ask. See docs/decisions/0024.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1f4c73a9e08"
down_revision: str | None = "c8b1e40d7a92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every column added here, so the upgrade and the downgrade cannot drift.
COLUMNS = (
    ("raised_by", sa.String(length=40)),
    ("reviewed_by", sa.String(length=40)),
    ("reviewed_at", sa.DateTime()),
    ("disposition", sa.String(length=30)),
    ("disposition_reason", sa.String(length=400)),
    ("disposition_by", sa.String(length=40)),
    ("disposition_at", sa.DateTime()),
    ("closed_by", sa.String(length=40)),
)


def upgrade() -> None:
    with op.batch_alter_table("non_conformances") as batch:
        for name, type_ in COLUMNS:
            batch.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("non_conformances") as batch:
        for name, _type in COLUMNS:
            batch.drop_column(name)
