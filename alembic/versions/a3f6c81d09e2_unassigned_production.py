"""A production booking may have no work order.

Units a machine counts with no order open to book them against are recorded
against the equipment instead of dropped to a log line, so `work_order_id`
becomes nullable. Nothing existing changes: every row written before this
has an order, and keeps it.

Revision ID: a3f6c81d09e2
Revises: 153379d6cf19
"""

import sqlalchemy as sa
from alembic import op

revision = "a3f6c81d09e2"
down_revision = "153379d6cf19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("production_logs", schema=None) as batch_op:
        batch_op.alter_column("work_order_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # Deleting the unassigned rows to make the column NOT NULL again would
    # throw away production the plant really made, which is the whole thing
    # this migration exists to stop. A downgrade with such rows present has
    # to be a decision a person makes, so it fails loudly instead.
    bind = op.get_bind()
    orphans = bind.scalar(
        sa.text("SELECT COUNT(*) FROM production_logs WHERE work_order_id IS NULL"))
    if orphans:
        raise RuntimeError(
            f"{orphans} production bookings have no work order (unassigned production). "
            "Decide what happens to those units before downgrading; this migration "
            "will not discard them.")
    with op.batch_alter_table("production_logs", schema=None) as batch_op:
        batch_op.alter_column("work_order_id", existing_type=sa.Integer(), nullable=False)
