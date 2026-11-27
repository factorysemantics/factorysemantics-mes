"""The part a repair order used: a part code and a count.

Two nullable columns on `maintenance_orders`, `part_code` and `part_qty`,
written when the order is closed. Until now the part was only words in the
findings ("band replaced"), which no ERP can book. No price: what the part
cost is the ERP's, and the MES holds no money.

Nothing else changed, so down is dropping the two columns.

Revision ID: e7b2d4f9a1c3
Revises: f3a9c1d27b64
Create Date: 2026-11-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e7b2d4f9a1c3"
down_revision: str | None = "f3a9c1d27b64"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("maintenance_orders") as batch:
        batch.add_column(sa.Column("part_code", sa.String(40), nullable=True))
        batch.add_column(sa.Column("part_qty", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("maintenance_orders") as batch:
        batch.drop_column("part_qty")
        batch.drop_column("part_code")
