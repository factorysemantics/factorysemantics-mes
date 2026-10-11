"""A characteristic the plant counts, and the lots it counted.

Revision ID: d4e8a1c93b70
Revises: a1c47f8b2d93

Three additive things, all nullable or new, and nothing else:

* `quality_specs.counted` - whether this characteristic is counted rather
  than measured. Null is *measured*, which is every specification written
  before this migration and reads exactly as it did: a gauge gives a number
  and the chart follows the sampling plan. True is an inspector looking at a
  lot and counting - how many were looked at, how many were sent back - and
  the chart is a p chart. See decision 0041, amending 0040.
* `quality_counts` - one row per inspection stretch: the stamp, the
  specification, the order, the station that counted, when it started and
  ended, how many were inspected, how many rejected, what the plant calls
  the lot, the material lot when there is one, who recorded it, and the
  breakdown by defect as JSON.
* `spc_signals.count_id` - the count a rule fired on, when the
  characteristic is counted. Null on every signal a measurement raised,
  which is every signal written before this migration.

**Why a row per stretch and not a row per unit.** A count of ten thousand
bottles is two whole numbers, and decision 0027 refused writing ten thousand
`quality_checks` rows to hold them: a check is one float measured against
one specification, and a bottle that was simply looked at has no float. The
denominator lives here, with its window, because a rate without the number
it was taken out of cannot be charted honestly.

**Nothing is backfilled.** No existing specification is marked counted, no
count is invented for any past inspection, and no existing signal is given a
count. Scrap already recorded elsewhere is not turned into inspection counts
here: scrap is what a station threw away, an inspection count is what
somebody looked at, and the two are different facts with different
denominators. Unknown is not zero.

Downgrade drops the column, the key and the table, and loses every count
recorded into them. Nothing else is touched.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e8a1c93b70"
down_revision: str | None = "a1c47f8b2d93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("quality_specs", sa.Column("counted", sa.Boolean(), nullable=True))

    op.create_table(
        "quality_counts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("spec_id", sa.Integer(), sa.ForeignKey("quality_specs.id"), nullable=False),
        sa.Column("work_order_id", sa.Integer(), sa.ForeignKey("work_orders.id"), nullable=True),
        sa.Column("equipment_id", sa.Integer(), sa.ForeignKey("equipment.id"), nullable=True),
        # The stretch this count covers. Null on both is *not recorded*: a
        # count written down without its window still says how many were
        # looked at, and refusing to store it would lose the only record.
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("inspected", sa.Integer(), nullable=False),
        sa.Column("rejected", sa.Integer(), nullable=False),
        sa.Column("lot_ref", sa.String(length=60), nullable=False),
        sa.Column("lot_id", sa.Integer(), sa.ForeignKey("material_lots.id"), nullable=True),
        sa.Column("checked_by", sa.String(length=40), nullable=True),
        # `[{"code": ..., "count": ...}]`. Null is *not broken down*, which
        # is not the same as *nothing found*; the screen says which.
        sa.Column("defects", sa.JSON(), nullable=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        # The two columns every floor table carries (`domain.common.
        # ShiftStamped`). A count is taken on one shift by one person.
        sa.Column("shift_code", sa.String(length=40), nullable=True),
        sa.Column("shift_day", sa.Date(), nullable=True),
    )
    op.create_index("ix_quality_counts_spec_id", "quality_counts", ["spec_id"])
    op.create_index("ix_quality_counts_work_order_id", "quality_counts", ["work_order_id"])
    op.create_index("ix_quality_counts_equipment_id", "quality_counts", ["equipment_id"])
    op.create_index("ix_quality_counts_lot_ref", "quality_counts", ["lot_ref"])
    op.create_index("ix_quality_counts_lot_id", "quality_counts", ["lot_id"])

    # Named, because SQLite renders a foreign key inside the table definition
    # and an unnamed constraint cannot be dropped again by `batch_alter_table`.
    with op.batch_alter_table("spc_signals") as batch:
        batch.add_column(sa.Column("count_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_spc_signals_count", "quality_counts",
                                 ["count_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("spc_signals") as batch:
        batch.drop_constraint("fk_spc_signals_count", type_="foreignkey")
        batch.drop_column("count_id")

    op.drop_index("ix_quality_counts_lot_id", table_name="quality_counts")
    op.drop_index("ix_quality_counts_lot_ref", table_name="quality_counts")
    op.drop_index("ix_quality_counts_equipment_id", table_name="quality_counts")
    op.drop_index("ix_quality_counts_work_order_id", table_name="quality_counts")
    op.drop_index("ix_quality_counts_spec_id", table_name="quality_counts")
    op.drop_table("quality_counts")

    op.drop_column("quality_specs", "counted")
