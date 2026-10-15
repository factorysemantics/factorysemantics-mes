"""A lease a node holds, and the counter baseline it books against.

Revision ID: b6f0c2a71d58
Revises: d4e8a1c93b70

Two new tables, nothing else touched, nothing backfilled.

* `plant_leases` - one row per role (`reader`, `floor`, `housekeeping`) saying
  which node may do that job and until when. Rows are created by the first
  node that asks for a role, not here: a plant that runs on one box never has
  one, and `fsmes plant status` then says nothing holds anything rather than
  naming three roles nobody wants. See decision 0042.
* `counter_baselines` - the last value of each machine counter that has
  already been booked, keyed by the machine's code and the tag name. Written
  in the same transaction as the booking it belongs to.

**Nothing is backfilled, and that is deliberate.** The baseline of a counter
that has been read by a reader whose memory is now gone cannot be recovered
from `production_logs`: those rows hold the deltas that were booked, not the
absolute value the PLC showed, and a machine that was replaced or a PLC that
was reset breaks any sum. So the first reading after this migration finds no
baseline for that machine and books nothing from it - the existing rule for a
counter with no baseline - and the second reading onwards books the
difference. The alternative was to guess an absolute counter value from a sum
of deltas, which is house rule 1 exactly: unknown is not zero.

Downgrade drops both tables. A plant that downgrades loses its baselines and
is in the state described above for one reading per machine; a plant that
downgrades while two nodes are running loses the lease they were using to stay
out of each other's way, which is why `[redundancy]` and a downgrade do not
belong in the same maintenance window.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6f0c2a71d58"
down_revision: str | None = "d4e8a1c93b70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plant_leases",
        sa.Column("role", sa.String(length=40), primary_key=True),
        sa.Column("holder", sa.String(length=80), nullable=False),
        # Every one of these is written from the database's own clock, read
        # back inside the transaction that writes the row. Two boxes do not
        # agree about the time; the database is the thing they both agree on.
        sa.Column("acquired_at", sa.DateTime(), nullable=False),
        sa.Column("renewed_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "counter_baselines",
        sa.Column("equipment_code", sa.String(length=40), primary_key=True),
        sa.Column("tag", sa.String(length=80), primary_key=True),
        sa.Column("value", sa.BigInteger(), nullable=False),
        # Which node booked up to here. Null is *not recorded*: a plant that
        # does not run redundantly has no node name to write down.
        sa.Column("node", sa.String(length=80), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("counter_baselines")
    op.drop_table("plant_leases")
