"""An SPC signal is recorded, carries its evidence, and names the station that measured.

Revision ID: a9c4e17b3d60
Revises: b1f4c73a9e08
Create Date: 2026-09-14

Three changes and no data change:

* `spc_signals` - one row per Western Electric rule firing, with the window
  it judged. The unique key (spec, rule, window) is what makes evaluating
  the rules on every write idempotent.
* `non_conformances.evidence` - what the MES saw, when the MES raised it.
  Rows that already exist stay null: nobody recorded evidence for them,
  because nothing asked.
* `quality_checks.equipment_id` - the station that took the reading, for
  readings a station took. Existing rows stay null, which is the truth:
  this MES did not record where a measurement was taken.

See docs/decisions/0027.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9c4e17b3d60"
down_revision: str | None = "b1f4c73a9e08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "spc_signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("spec_id", sa.Integer(), sa.ForeignKey("quality_specs.id"), nullable=False),
        sa.Column("rule", sa.Integer(), nullable=False),
        sa.Column("window_key", sa.String(length=60), nullable=False),
        sa.Column("check_id", sa.Integer(), sa.ForeignKey("quality_checks.id"), nullable=True),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("what", sa.String(length=120), nullable=False),
        sa.Column("window", sa.JSON(), nullable=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("nonconformance_id", sa.Integer(), sa.ForeignKey("non_conformances.id"), nullable=True),
        sa.UniqueConstraint("spec_id", "rule", "window_key", name="uq_spc_signals_spec_rule_window"),
    )
    op.create_index("ix_spc_signals_spec_id", "spc_signals", ["spec_id"])
    op.create_index("ix_spc_signals_nonconformance_id", "spc_signals", ["nonconformance_id"])

    with op.batch_alter_table("non_conformances") as batch:
        batch.add_column(sa.Column("evidence", sa.JSON(), nullable=True))

    with op.batch_alter_table("quality_checks") as batch:
        batch.add_column(sa.Column("equipment_id", sa.Integer(), nullable=True))
    op.create_index("ix_quality_checks_equipment_id", "quality_checks", ["equipment_id"])


def downgrade() -> None:
    op.drop_index("ix_quality_checks_equipment_id", table_name="quality_checks")
    with op.batch_alter_table("quality_checks") as batch:
        batch.drop_column("equipment_id")
    with op.batch_alter_table("non_conformances") as batch:
        batch.drop_column("evidence")
    op.drop_index("ix_spc_signals_nonconformance_id", table_name="spc_signals")
    op.drop_index("ix_spc_signals_spec_id", table_name="spc_signals")
    op.drop_table("spc_signals")
