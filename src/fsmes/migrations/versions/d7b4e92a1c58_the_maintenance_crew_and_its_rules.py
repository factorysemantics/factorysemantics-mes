"""The maintenance crew: skills, a roster, the supervisor's rules, and who has the work.

Revision ID: d7b4e92a1c58
Revises: b9f31c7a4e50
Create Date: 2026-10-09

Four new tables - `skills`, `personnel_skills`, `roster`, `dispatch_rules` -
and nine new columns across the two maintenance tables, so that a maintenance
order can say which trade it needs, how much it matters, and who it was given
to and by what.

EVERY NEW COLUMN IS NULLABLE, and no row is backfilled. A plant upgrading from
a release before this has plans that never said which trade they need and
orders nobody was ever assigned, and that is the truth about those rows: a
backfill would have to invent a trade or a priority, and an invented priority
is worse than none because it sorts. The service reads a missing priority as
*routine* and says so where it matters; a missing skill means work anybody on
shift can take. Nothing is lost and nothing is guessed (house rule 2).

The three skills the product ships - ELEC, MECH, GEN - are seeded by
`fsmes pack apply` from pack masterdata, not here: which trades a plant
employs is config, not code (house rule 4), and a migration that inserted them
would be this file having an opinion about somebody else's plant.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7b4e92a1c58"
down_revision: str | None = "b9f31c7a4e50"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: What each maintenance table gains, and in which order, so the upgrade and
#: the downgrade cannot drift apart.
PLAN_COLUMNS = (
    ("skill_code", sa.String(length=40)),
    ("priority", sa.Integer()),
)

ORDER_COLUMNS = (
    ("skill_code", sa.String(length=40)),
    ("priority", sa.Integer()),
    ("assigned_to", sa.String(length=40)),
    ("assigned_at", sa.DateTime()),
    ("assigned_by", sa.String(length=40)),
    ("scheduled_for", sa.DateTime()),
    ("unassigned_reason", sa.String(length=40)),
)


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=400), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_skills_code", "skills", ["code"])

    op.create_table(
        "personnel_skills",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("personnel_id", sa.Integer(), nullable=False),
        sa.Column("skill_code", sa.String(length=40), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["personnel_id"], ["personnel.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("personnel_id", "skill_code", name="uq_personnel_skill"),
    )
    op.create_index("ix_personnel_skills_personnel_id", "personnel_skills",
                    ["personnel_id"])
    op.create_index("ix_personnel_skills_skill_code", "personnel_skills", ["skill_code"])

    op.create_table(
        "roster",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("personnel_id", sa.Integer(), nullable=False),
        sa.Column("shift_code", sa.String(length=40), nullable=False),
        # Null is a standing assignment: on this shift whenever it runs.
        sa.Column("shift_day", sa.Date(), nullable=True),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=160), nullable=True),
        sa.ForeignKeyConstraint(["personnel_id"], ["personnel.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("personnel_id", "shift_code", "shift_day",
                            name="uq_roster_slot"),
    )
    op.create_index("ix_roster_personnel_id", "roster", ["personnel_id"])
    op.create_index("ix_roster_shift", "roster", ["shift_code", "shift_day"])

    op.create_table(
        "dispatch_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("supervisor_code", sa.String(length=40), nullable=True),
        sa.Column("equipment_code", sa.String(length=40), nullable=True),
        sa.Column("skill_code", sa.String(length=40), nullable=True),
        sa.Column("priority_at_least", sa.Integer(), nullable=True),
        # VARCHAR and not a native enum, matching `domain.common.str_enum`, so
        # the same chain runs on SQLite and PostgreSQL and a plant that adds a
        # strategy needs no ALTER TYPE.
        sa.Column("strategy", sa.String(length=30), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_dispatch_rules_code", "dispatch_rules", ["code"])
    op.create_index("ix_dispatch_rules_active_sequence", "dispatch_rules",
                    ["active", "sequence"])

    with op.batch_alter_table("maintenance_plans") as batch:
        for name, type_ in PLAN_COLUMNS:
            batch.add_column(sa.Column(name, type_, nullable=True))

    with op.batch_alter_table("maintenance_orders") as batch:
        for name, type_ in ORDER_COLUMNS:
            batch.add_column(sa.Column(name, type_, nullable=True))
    op.create_index("ix_maintenance_orders_priority", "maintenance_orders", ["priority"])
    op.create_index("ix_maintenance_orders_assigned_to", "maintenance_orders",
                    ["assigned_to"])

    # `assigned` joins the statuses a maintenance order can hold. Nothing to
    # alter: the column is a VARCHAR (`native_enum=False`), which is exactly
    # why - a native PostgreSQL enum would need an ALTER TYPE here and a plant
    # mid-transaction could not have it.


def downgrade() -> None:
    op.drop_index("ix_maintenance_orders_assigned_to", table_name="maintenance_orders")
    op.drop_index("ix_maintenance_orders_priority", table_name="maintenance_orders")
    with op.batch_alter_table("maintenance_orders") as batch:
        for name, _type in reversed(ORDER_COLUMNS):
            batch.drop_column(name)
    with op.batch_alter_table("maintenance_plans") as batch:
        for name, _type in reversed(PLAN_COLUMNS):
            batch.drop_column(name)

    op.drop_index("ix_dispatch_rules_active_sequence", table_name="dispatch_rules")
    op.drop_index("ix_dispatch_rules_code", table_name="dispatch_rules")
    op.drop_table("dispatch_rules")
    op.drop_index("ix_roster_shift", table_name="roster")
    op.drop_index("ix_roster_personnel_id", table_name="roster")
    op.drop_table("roster")
    op.drop_index("ix_personnel_skills_skill_code", table_name="personnel_skills")
    op.drop_index("ix_personnel_skills_personnel_id", table_name="personnel_skills")
    op.drop_table("personnel_skills")
    op.drop_index("ix_skills_code", table_name="skills")
    op.drop_table("skills")
