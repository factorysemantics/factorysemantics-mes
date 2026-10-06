"""A characteristic can be inspected n pieces at a time.

Revision ID: b9f31c7a4e50
Revises: c7a2e94b16d3

Three additive, nullable things, and nothing else:

* `quality_specs.sample_size` - how many pieces this characteristic is
  inspected at a time. The sampling plan is the plant's decision, not the
  product's, so it is a column on the specification rather than a setting.
  Null is *no plan stated*, which is every specification written before this
  migration, and it reads exactly as 1 did: one piece at a time, an
  individuals-and-moving-range chart, unchanged.
* `quality_samples` - one row per subgroup: the stamp, the specification,
  the order, the station, the gauge and who took it. The facts that belong
  to the *sample* rather than to any one reading.
* `quality_checks.sample_id` - which sample a reading is one of. Null is a
  reading taken on its own.

**Why the readings stay in `quality_checks`, one row each.** A sample stored
as five numbers in one row would be the smaller schema and the larger lie:
`quality_checks` is what the measurements card, the inspection history, the
certificate of analysis, a gauge's impact list and every count of *how many
checks has this plant recorded* already read, and each of those would have
become wrong in a different way. One row per reading plus a nullable key
tying them together is the smallest shape that leaves all of them true, and
the arithmetic that needs the subgroup gets it from the key.

**Nothing is backfilled.** No existing reading is given a sample, and no
existing specification is given a sample size - inferring either would be
inventing a sampling plan nobody wrote down, and a chart drawn from an
invented plan is wrong in exactly the way this whole feature exists to
avoid (house rule 2: unknown is not 1 either, it is unknown, and here the
code reads *unknown* as *one at a time* only because that is what the plant
was actually doing).

Downgrade drops the column, the key and the table, and loses the samples
recorded into them. The readings themselves survive, as individual checks.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9f31c7a4e50"
down_revision: str | None = "c7a2e94b16d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("quality_specs", sa.Column("sample_size", sa.Integer(), nullable=True))

    op.create_table(
        "quality_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("spec_id", sa.Integer(), sa.ForeignKey("quality_specs.id"), nullable=False),
        sa.Column("work_order_id", sa.Integer(), sa.ForeignKey("work_orders.id"), nullable=True),
        sa.Column("equipment_id", sa.Integer(), sa.ForeignKey("equipment.id"), nullable=True),
        sa.Column("gauge_id", sa.Integer(), sa.ForeignKey("gauges.id"), nullable=True),
        sa.Column("checked_by", sa.String(length=40), nullable=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        # The two columns every floor table carries (`domain.common.
        # ShiftStamped`). A sample is one act of measurement in one shift.
        sa.Column("shift_code", sa.String(length=40), nullable=True),
        sa.Column("shift_day", sa.Date(), nullable=True),
    )
    op.create_index("ix_quality_samples_spec_id", "quality_samples", ["spec_id"])
    op.create_index("ix_quality_samples_work_order_id", "quality_samples", ["work_order_id"])
    op.create_index("ix_quality_samples_equipment_id", "quality_samples", ["equipment_id"])
    op.create_index("ix_quality_samples_gauge_id", "quality_samples", ["gauge_id"])

    # Named, because SQLite renders a foreign key inside the table definition
    # and an unnamed constraint cannot be dropped again by `batch_alter_table`.
    with op.batch_alter_table("quality_checks") as batch:
        batch.add_column(sa.Column("sample_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_quality_checks_sample", "quality_samples",
                                 ["sample_id"], ["id"])
    op.create_index("ix_quality_checks_sample_id", "quality_checks", ["sample_id"])


def downgrade() -> None:
    op.drop_index("ix_quality_checks_sample_id", table_name="quality_checks")
    with op.batch_alter_table("quality_checks") as batch:
        batch.drop_constraint("fk_quality_checks_sample", type_="foreignkey")
        batch.drop_column("sample_id")

    op.drop_index("ix_quality_samples_gauge_id", table_name="quality_samples")
    op.drop_index("ix_quality_samples_equipment_id", table_name="quality_samples")
    op.drop_index("ix_quality_samples_work_order_id", table_name="quality_samples")
    op.drop_index("ix_quality_samples_spec_id", table_name="quality_samples")
    op.drop_table("quality_samples")

    op.drop_column("quality_specs", "sample_size")
