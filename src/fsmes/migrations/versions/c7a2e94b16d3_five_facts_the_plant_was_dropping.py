"""Five nullable columns: facts this plant already had and threw away.

Revision ID: c7a2e94b16d3
Revises: b3f9c07e51ad

One migration, five columns, nothing else. Each one is a fact the plant
either already held somewhere else or could derive at the moment it wrote
the row, and dropped:

* `ai_turns.screen` - the browser has always posted it and the API declared
  it; nothing stored it, so *which screen do people ask about* was
  unanswerable in principle.
* `ai_turns.shift_code` / `shift_day` and `audit_log.shift_code` /
  `shift_day` - the same two columns every floor table carries
  (`domain.common.ShiftStamped`), resolved at write time from the plant
  calendar as it stood (decision 0028), so a question and the shift it was
  asked in are one row.
* `production_logs.booked_by` - the actor the `production.reported` audit
  row already carried, on the booking itself.
* `personnel.home_equipment_id` - a nullable work center or station. The
  one link between a person and the equipment tree. It grants nothing and
  restricts nothing; it says where to group somebody.

**Additive and nullable, and nothing is backfilled.** Every row written
before this migration keeps a null in every one of these columns, and null
is read as *not attributed* rather than as a value (house rule 2). It would
be possible to walk `audit_log` and re-resolve a shift for every historical
row the way `c6a4b81f39d7` did for the floor tables - and it would be wrong
here, because that migration stamped rows against patterns that were in
force at the time, while an audit row from a year ago would be stamped
against a roster written since. An unattributed row is the truth; a
plausible one is not. The same goes for `booked_by`: the audit trail can be
read for what a booking's actor was, and copying it back in would turn a
join a person can check into a column they cannot.

Downgrade drops the five columns and loses what was written into them.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7a2e94b16d3"
down_revision: str | None = "b3f9c07e51ad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_turns", sa.Column("screen", sa.String(length=80), nullable=True))
    op.add_column("ai_turns", sa.Column("shift_code", sa.String(length=40), nullable=True))
    op.add_column("ai_turns", sa.Column("shift_day", sa.Date(), nullable=True))

    op.add_column("audit_log", sa.Column("shift_code", sa.String(length=40), nullable=True))
    op.add_column("audit_log", sa.Column("shift_day", sa.Date(), nullable=True))

    op.add_column("production_logs", sa.Column("booked_by", sa.String(length=40), nullable=True))

    # Named, because SQLite renders a foreign key inside the table definition
    # and an unnamed constraint cannot be dropped again by `batch_alter_table`.
    with op.batch_alter_table("personnel") as batch:
        batch.add_column(sa.Column("home_equipment_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_personnel_home_equipment", "equipment",
                                 ["home_equipment_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("personnel") as batch:
        batch.drop_constraint("fk_personnel_home_equipment", type_="foreignkey")
        batch.drop_column("home_equipment_id")

    op.drop_column("production_logs", "booked_by")

    op.drop_column("audit_log", "shift_day")
    op.drop_column("audit_log", "shift_code")

    op.drop_column("ai_turns", "shift_day")
    op.drop_column("ai_turns", "shift_code")
    op.drop_column("ai_turns", "screen")
