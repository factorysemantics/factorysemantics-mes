"""The part's name on a repair order, as the ERP's parts list gave it.

One nullable text column on `maintenance_orders`, `part_name`, written when
an order closes with a part the ERP's list knew. History and the xlsx export
show it beside the code, so a closed job still reads "Heater band" when the
ERP is switched off. No price, and no parts table: the list itself is the
ERP's and is never written down.

Down is dropping the column.

Revision ID: b4e9c2a7d815
Revises: e7b2d4f9a1c3
Create Date: 2026-11-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b4e9c2a7d815"
down_revision: str | None = "e7b2d4f9a1c3"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("maintenance_orders") as batch:
        batch.add_column(sa.Column("part_name", sa.String(140), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("maintenance_orders") as batch:
        batch.drop_column("part_name")
