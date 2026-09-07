"""Work orders know which line they run on.

Taken from where the routing starts. A plant with six lines cannot show a
supervisor "the orders" and mean all of them.

Revision ID: e7b3c9d18a45
Revises: d4a1c7e5b902
"""

import sqlalchemy as sa
from alembic import op

revision = "e7b3c9d18a45"
down_revision = "d4a1c7e5b902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("work_orders", sa.Column("work_center_id", sa.Integer(), nullable=True))
    op.create_index("ix_work_orders_work_center_id", "work_orders", ["work_center_id"])
    # Existing orders keep a null line rather than being guessed at: the
    # routing may have been edited since, and inventing an answer is worse
    # than reporting none.


def downgrade() -> None:
    op.drop_index("ix_work_orders_work_center_id", table_name="work_orders")
    op.drop_column("work_orders", "work_center_id")
