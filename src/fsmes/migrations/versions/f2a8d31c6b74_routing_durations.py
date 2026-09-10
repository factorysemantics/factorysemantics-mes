"""Routing operations carry durations, and may target a work center.

Three durations because a plant is billed for three different things: setup
once per order, machine time and labour time per unit. All nullable - absent
means "not time-studied", which the scheduler reports rather than treats as
zero.

`work_center_id` lets an operation name a cell instead of one machine, which
is how a plant with interchangeable machines actually plans. `equipment_id`
becomes nullable for the same reason; a check that one of the two is present
lives in the service, not the schema, so the error can say what to do.

Revision ID: f2a8d31c6b74
Revises: e7b3c9d18a45
"""

import sqlalchemy as sa
from alembic import op

revision = "f2a8d31c6b74"
down_revision = "e7b3c9d18a45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("routing_operations") as batch:
        batch.add_column(sa.Column("work_center_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("setup_seconds", sa.Float(), nullable=True))
        batch.add_column(sa.Column("run_seconds_per_unit", sa.Float(), nullable=True))
        batch.add_column(sa.Column("labour_seconds_per_unit", sa.Float(), nullable=True))
        # SQLite cannot drop NOT NULL in place; batch mode rebuilds the table.
        batch.alter_column("equipment_id", existing_type=sa.Integer(), nullable=True)

    with op.batch_alter_table("work_order_operations") as batch:
        batch.add_column(sa.Column("setup_seconds", sa.Float(), nullable=True))
        batch.add_column(sa.Column("run_seconds_per_unit", sa.Float(), nullable=True))
        batch.add_column(sa.Column("labour_seconds_per_unit", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("work_order_operations") as batch:
        batch.drop_column("labour_seconds_per_unit")
        batch.drop_column("run_seconds_per_unit")
        batch.drop_column("setup_seconds")

    with op.batch_alter_table("routing_operations") as batch:
        batch.alter_column("equipment_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_column("labour_seconds_per_unit")
        batch.drop_column("run_seconds_per_unit")
        batch.drop_column("setup_seconds")
        batch.drop_column("work_center_id")
