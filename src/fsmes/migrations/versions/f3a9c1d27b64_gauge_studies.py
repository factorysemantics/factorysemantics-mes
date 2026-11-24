"""Gauge studies (gage R&R): the plan and its readings.

`gauge_studies` - one crossed study: a gauge, a characteristic on a material,
how many parts, which appraisers, how many trials, and the tolerance it is
judged against, copied from the specification when it was planned.

`gauge_study_readings` - one reading per part, appraiser and trial, with who
recorded it and when. These are study readings and never production checks:
the same parts measured again and again on purpose, kept out of every chart
of the plant's output.

Two new tables and nothing else changed, so down is dropping them.

Revision ID: f3a9c1d27b64
Revises: c8e2f4a6d1b7
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f3a9c1d27b64"
down_revision: str | None = "c8e2f4a6d1b7"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "gauge_studies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("gauge_id", sa.Integer(), sa.ForeignKey("gauges.id"), nullable=False),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id"), nullable=False),
        sa.Column("characteristic", sa.String(80), nullable=False),
        sa.Column("lower_spec", sa.Float(), nullable=True),
        sa.Column("upper_spec", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(20), nullable=False, server_default=""),
        sa.Column("parts", sa.Integer(), nullable=False),
        sa.Column("trials", sa.Integer(), nullable=False),
        sa.Column("appraisers", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False, server_default="planned"),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("planned_by", sa.String(40), nullable=False),
        sa.Column("planned_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_gauge_studies_gauge_id", "gauge_studies", ["gauge_id"])
    op.create_table(
        "gauge_study_readings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("study_id", sa.Integer(), sa.ForeignKey("gauge_studies.id"), nullable=False),
        sa.Column("part", sa.Integer(), nullable=False),
        sa.Column("appraiser", sa.String(40), nullable=False),
        sa.Column("trial", sa.Integer(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("recorded_by", sa.String(40), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("study_id", "part", "appraiser", "trial",
                            name="uq_gauge_study_reading"),
    )
    op.create_index("ix_gauge_study_readings_study_id", "gauge_study_readings",
                    ["study_id"])


def downgrade() -> None:
    op.drop_index("ix_gauge_study_readings_study_id", table_name="gauge_study_readings")
    op.drop_table("gauge_study_readings")
    op.drop_index("ix_gauge_studies_gauge_id", table_name="gauge_studies")
    op.drop_table("gauge_studies")
