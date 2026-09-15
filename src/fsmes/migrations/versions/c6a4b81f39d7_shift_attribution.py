"""Every row the floor writes says which shift it fell in.

Revision ID: c6a4b81f39d7
Revises: b1f4c73a9e08
Create Date: 2026-09-14

Two nullable columns — `shift_code` and `shift_day` — on the four tables that
record something happening on the floor: `production_logs`, `equipment_states`,
`quality_checks` and `non_conformances`. New rows are attributed as they are
written. See docs/decisions/0028.

THE BACKFILL, AND WHAT IT ASSUMES. Rows that already exist are attributed from
the shift patterns **as they stand today**, read on this plant's clock
(`MES_PLANT_TIMEZONE`). There is no history of shift patterns in this product,
so that is an assumption, and it is the only one available: a plant that moved
its shift boundary last month will see last month's rows attributed to this
month's pattern. A row no pattern covers is left null and reported as *not
attributed* rather than pushed into the nearest shift. A plant with no patterns
at all gets nulls throughout, which is the truth.

It runs in chunks and touches only rows it can attribute, so a plant with a
year of one-second counters is a long UPDATE rather than a long read of every
row into memory.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta

import sqlalchemy as sa
from alembic import op

revision: str = "c6a4b81f39d7"
down_revision: str | None = "b1f4c73a9e08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table and the timestamp its shift is read from, so the upgrade, the
# backfill and the downgrade cannot drift apart.
TABLES = (
    ("production_logs", "ts"),
    ("equipment_states", "started_at"),
    ("quality_checks", "ts"),
    ("non_conformances", "created_at"),
)

COLUMNS = (
    ("shift_code", sa.String(length=40)),
    ("shift_day", sa.Date()),
)

CHUNK = 5000


def _zone():
    """The plant's clock, the same one the running product reads."""
    from fsmes import identity

    return identity.clock().tz


#: Declared rather than reflected, and typed, so SQLite and PostgreSQL both
#: hand back `time` and `datetime` objects instead of one of each.
SHIFT_PATTERNS = sa.table(
    "shift_patterns",
    sa.column("code", sa.String),
    sa.column("starts", sa.Time),
    sa.column("ends", sa.Time),
    sa.column("days", sa.String),
    sa.column("active", sa.Boolean),
)


def _rows_of(table: str, moment_column: str):
    return sa.table(table, sa.column("id", sa.Integer),
                    sa.column(moment_column, sa.DateTime),
                    sa.column("shift_code", sa.String),
                    sa.column("shift_day", sa.Date))


def _patterns(connection) -> list[dict]:
    rows = connection.execute(
        sa.select(SHIFT_PATTERNS.c.code, SHIFT_PATTERNS.c.starts,
                  SHIFT_PATTERNS.c.ends, SHIFT_PATTERNS.c.days)
        .where(SHIFT_PATTERNS.c.active.is_(True))
    ).mappings().all()
    out = []
    for row in rows:
        starts, ends = _as_time(row["starts"]), _as_time(row["ends"])
        out.append({"code": row["code"], "starts": starts, "ends": ends,
                    "days": row["days"], "crosses": ends <= starts})
    return out


def _as_time(value) -> time:
    """A database that stored the column as text still answers with text."""
    if isinstance(value, time):
        return value
    return time.fromisoformat(str(value))


def _shift_for(patterns: list[dict], moment: datetime, zone):
    """The shift a stored (naive UTC) instant falls in, or None.

    The same three rules as `fsmes.services.calendar.shift_for`: half-open
    interval, a crossing shift belongs to the day it started, and no pattern
    means not attributed. Calendar exceptions are not consulted — an overtime
    day an old row fell on would need the exception to have been recorded, and
    leaving those rows unattributed is honest where guessing is not.
    """
    local = moment.replace(tzinfo=UTC).astimezone(zone)
    at = local.time()
    best = None
    for pattern in patterns:
        if pattern["crosses"]:
            if at >= pattern["starts"]:
                day = local.date()
            elif at < pattern["ends"]:
                day = local.date() - timedelta(days=1)
            else:
                continue
        elif pattern["starts"] <= at < pattern["ends"]:
            day = local.date()
        else:
            continue
        if pattern["days"][day.weekday()] != "1":
            continue
        if best is None or (day, pattern["starts"]) > (best[1], best[2]):
            best = (pattern["code"], day, pattern["starts"])
    return (best[0], best[1]) if best else None


def upgrade() -> None:
    for table, _column in TABLES:
        with op.batch_alter_table(table) as batch:
            for name, type_ in COLUMNS:
                batch.add_column(sa.Column(name, type_, nullable=True))

    connection = op.get_bind()
    patterns = _patterns(connection)
    if not patterns:
        # Nothing to attribute from. Not an error: a plant that has not told
        # this MES its shifts has rows with no shift, and that is the truth.
        return

    zone = _zone()
    for name, column in TABLES:
        table = _rows_of(name, column)
        moment_column = table.c[column]
        last = -1
        while True:
            rows = connection.execute(
                sa.select(table.c.id, moment_column.label("moment"))
                .where(table.c.id > last).order_by(table.c.id).limit(CHUNK)
            ).mappings().all()
            if not rows:
                break
            updates = []
            for row in rows:
                moment = row["moment"]
                if isinstance(moment, str):
                    moment = datetime.fromisoformat(moment)
                if moment is None:
                    continue
                found = _shift_for(patterns, moment, zone)
                if found is not None:
                    updates.append({"row_id": row["id"], "code": found[0], "day": found[1]})
            if updates:
                connection.execute(
                    table.update()
                    .where(table.c.id == sa.bindparam("row_id"))
                    .values(shift_code=sa.bindparam("code"), shift_day=sa.bindparam("day")),
                    updates,
                )
            last = rows[-1]["id"]


def downgrade() -> None:
    for table, _column in TABLES:
        with op.batch_alter_table(table) as batch:
            for name, _type in COLUMNS:
                batch.drop_column(name)
