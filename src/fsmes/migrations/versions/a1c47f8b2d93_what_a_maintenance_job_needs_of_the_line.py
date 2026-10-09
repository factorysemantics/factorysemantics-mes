"""What a maintenance job needs of the line: a stop, and a window.

Revision ID: a1c47f8b2d93
Revises: d7b4e92a1c58
Create Date: 2026-10-09

Two columns on each of the two maintenance tables. `needs_stop` says the
machine has to be stopped for the job; `window` says when the job may be done
- `anytime`, `between_orders`, `end_of_shift`.

The pair exists because an order sitting at `assigned` for a whole shift has
two completely different readings and the records could not tell them apart: a
mechanic ignoring his list, or a plant that ran to the end of its order and
never gave him the line. The first is a problem; the second is a plant working
as intended. A maintenance screen that shows both the same way sends a
supervisor to have the wrong conversation.

`window` is nullable and is NOT backfilled: a plan written before this release
never said when its job may be done, and "nobody has said" is a different fact
from "anytime" (house rule 2 - unknown is not zero). The dispatcher reads an
unsaid window as open, which is how the plant behaved before this column
existed, so nothing changes for an old plan until somebody answers the
question. `fsmes pack apply` fills it from masterdata only where it is still
empty, so a plant that has answered on the screen keeps its answer.

`needs_stop` is NOT NULL with a server default of false, which is the one
place here that states something about old rows. It is safe in the direction
that matters: a job that has never said it needs the line stopped does not get
to stop the line. The opposite default would let an upgrade start stopping
machines on the strength of a column nobody had filled in.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c47f8b2d93"
down_revision: str | None = "d7b4e92a1c58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Both tables gain the same pair, in the same order, so the upgrade and the
#: downgrade cannot drift apart. VARCHAR for the window and not a native enum,
#: matching `domain.common.str_enum`: the same chain runs on SQLite and
#: PostgreSQL, and a plant that adds a window needs no ALTER TYPE.
TABLES = ("maintenance_plans", "maintenance_orders")

COLUMNS = (
    ("needs_stop", sa.Boolean(), False, sa.false()),
    ("window", sa.String(length=20), True, None),
)


def upgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            for name, type_, nullable, default in COLUMNS:
                batch.add_column(sa.Column(name, type_, nullable=nullable,
                                           server_default=default))


def downgrade() -> None:
    for table in reversed(TABLES):
        with op.batch_alter_table(table) as batch:
            for name, _type, _nullable, _default in reversed(COLUMNS):
                batch.drop_column(name)
