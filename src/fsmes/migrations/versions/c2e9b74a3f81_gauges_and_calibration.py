"""Gauges and calibration.

Revision ID: c2e9b74a3f81
Revises: b8f4d27e1a53

A reading from an out-of-calibration gauge is not a measurement, it is a
number - and every chart and capability index built on it inherits that. The
link from a quality check to its gauge is what lets a failed calibration name
the measurements it invalidated.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2e9b74a3f81"
down_revision: str | None = "b8f4d27e1a53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gauges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("location", sa.String(length=120), nullable=True),
        sa.Column("status", sa.Enum("in_service", "overdue", "out_of_service", "lost",
                                    name="gaugestatus"), nullable=False),
        sa.Column("interval_days", sa.Integer(), nullable=False),
        sa.Column("last_calibrated", sa.Date(), nullable=True),
        sa.Column("resolution", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "calibrations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("gauge_id", sa.Integer(), nullable=False),
        sa.Column("performed_on", sa.Date(), nullable=False),
        sa.Column("result", sa.Enum("pass", "fail_as_found", "adjusted",
                                    name="calibrationresult"), nullable=False),
        sa.Column("performed_by", sa.String(length=40), nullable=False),
        sa.Column("certificate", sa.String(length=120), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["gauge_id"], ["gauges.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_calibrations_gauge_id", "calibrations", ["gauge_id"])

    with op.batch_alter_table("quality_checks") as batch:
        batch.add_column(sa.Column("gauge_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_check_gauge", "gauges", ["gauge_id"], ["id"])
    op.create_index("ix_quality_checks_gauge_id", "quality_checks", ["gauge_id"])


def downgrade() -> None:
    op.drop_index("ix_quality_checks_gauge_id", table_name="quality_checks")
    with op.batch_alter_table("quality_checks") as batch:
        batch.drop_column("gauge_id")
    op.drop_index("ix_calibrations_gauge_id", table_name="calibrations")
    op.drop_table("calibrations")
    op.drop_table("gauges")
