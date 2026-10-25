"""Status and kind columns are plain text on PostgreSQL too, as the code always believed.

Revision ID: 2c6e817b9faf
Revises: b6f0c2a71d58

Nine columns created by early migrations were written as `sa.Enum(...,
name=...)` without `native_enum=False`. On SQLite that is a VARCHAR; on
PostgreSQL it is a native enum type, a closed list of words the database
itself enforces. The code never meant that: every one of these columns is
declared in `fsmes.domain` with `domain.common.str_enum`, which is a VARCHAR(30)
on every database, and a plant made with `create_all` - and the suite's own
PostgreSQL run - has always had VARCHAR here.

The drift cost a real plant its Maintenance screen. Revision d7b4e92a1c58
(2026-10-09) added `assigned` to `MaintenanceStatus` and wrote, in a comment,
that nothing needed altering because the column was a VARCHAR. On a PostgreSQL
plant created before that day and upgraded since, it was not:
`maintenance_orders.status` was the native type `maintenancestatus` holding
`{due, in_progress, done, skipped}`. Every query that named `assigned`
failed with `invalid input value for enum maintenancestatus: "assigned"`, so
`/maintenance/due`, `/shift` and `/roster` answered 500 and the screen sat at
"reconnecting..." for good.

**Why convert to VARCHAR rather than `ALTER TYPE ... ADD VALUE 'assigned'`.**
Adding the one word would fix today's screen and leave the trap set: the next
member added to any of these nine `StrEnum`s would need its own `ALTER TYPE`,
which cannot run inside a transaction on older PostgreSQL, and which nobody
remembers because SQLite and the suite never need it. Converting makes the
schema what the code and the later migrations already describe, the same on
both databases, and closes the whole class rather than one instance of it.
The other eight native types were checked against their domain enums on
2026-10-25 and none had drifted yet; they are converted for the same reason.

Each column is converted only if it is still a native enum, and each type is
dropped only if it exists, so a database that was never on PostgreSQL, or was
built with `create_all`, or was upgraded here once already, is left as it is.
On SQLite this revision does nothing at all.

Nothing is lost: `status::text` is the exact word that was stored. The
`ALTER COLUMN ... TYPE` rewrites each table under a brief exclusive lock;
these are small tables (orders, plans, gauges, documents, calendar rows,
serial units) and on a running plant the lock waits for in-flight
transactions and holds writers back for the moment of the rewrite.

**Downgrade does nothing, on purpose.** Turning a column back into a native
enum would fail on the first row that says `assigned` (a value the old type
never had), and on PostgreSQL a value cannot be removed from an enum type
anyway. The previous revision's code reads and writes these columns as
VARCHAR - that is what `str_enum` declares - so a database left as text after
a downgrade is one that code works with.
"""

from alembic import op

revision = "2c6e817b9faf"
down_revision = "b6f0c2a71d58"
branch_labels = None
depends_on = None

#: (table, column, native type the early migrations created).
NATIVE_ENUM_COLUMNS = [
    ("calendar_exceptions", "kind", "exceptionkind"),
    ("scheduled_slots", "kind", "slotkind"),
    ("documents", "status", "documentstatus"),
    ("gauges", "status", "gaugestatus"),
    ("calibrations", "result", "calibrationresult"),
    ("serial_units", "status", "unitstatus"),
    ("maintenance_plans", "trigger", "triggerkind"),
    ("maintenance_orders", "kind", "maintenancekind"),
    ("maintenance_orders", "status", "maintenancestatus"),
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table, column, _type_name in NATIVE_ENUM_COLUMNS:
        still_native = bind.exec_driver_sql(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %(t)s "
            "AND column_name = %(c)s AND data_type = 'USER-DEFINED'",
            {"t": table, "c": column},
        ).first()
        if still_native:
            op.execute(
                f'ALTER TABLE "{table}" ALTER COLUMN "{column}" '
                f'TYPE VARCHAR(30) USING "{column}"::text'
            )
    for _table, _column, type_name in NATIVE_ENUM_COLUMNS:
        op.execute(f'DROP TYPE IF EXISTS "{type_name}"')


def downgrade() -> None:
    # See the docstring: the columns stay text, which the previous revision's
    # code reads and writes as it always did.
    pass
