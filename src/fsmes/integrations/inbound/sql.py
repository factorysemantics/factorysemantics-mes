"""The second inbound driver: read-only queries against a database the plant has.

A CSV export needs a person every day. A query needs a person once. Where
technicians already label downtime, inspectors already record results and
operators already type counts into some other system, the fastest honest way
to give this MES the same inputs is to read them where they already are.

What this module is, exactly: a scheduler, a bind parameter, and a cursor.
The query is the plant's, written by whoever knows that system; the rows it
returns go through the same mapping and the same writers as a file dropped
in a folder, and mean exactly the same thing when they land.

Five rules make it safe to point at a system somebody else depends on.

*Read, and only read.* The connection is opened read-only wherever the
driver has a way to say so, a statement timeout is set wherever the dialect
has one, and a query containing a word that could change anything is refused
before it is ever sent. None of that replaces read-only credentials, and
`sql-check` says which of the three it managed.

*Never hold their transaction open.* Every row is fetched and the source
connection is closed before this MES writes anything. A long write here can
never become a lock over there.

*Never read twice, never skip.* The cursor is the supplier's own ordering
column, kept as the text the supplier gave, and handed back unchanged in the
next query. Correctness does not rest on it: `inbound_events` is keyed on the
supplier's own id, so a cursor that is behind costs a re-read and changes
nothing.

*A row nothing was done with is not a row that was read.* When a row cannot
be recorded the cursor stops at it and says which row and why. The rows after
it are still recorded — leaving good data unread would be its own dishonesty
— but the cursor does not step over the gap, so the next pass tries again.
Stepping over it is a decision a person makes, in words, with
`fsmes inbound sql-watermark --set`.

*Say what was left out.* Every pass states its totals: rows read, recorded,
already seen, rejected with the reason for each, and where the cursor stood
before and after.

Clean-room, and it is the point of the design: this module contains no
schema, no table name and no query belonging to any commercial system. It
cannot, because it does not know what it is reading — the SQL is
configuration the plant writes for the system the plant owns. The docs
describe the *shape* a query must return and say nothing about where to find
it in any product.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import structlog
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from fsmes.db import utcnow
from fsmes.domain import InboundWatermark
from fsmes.integrations.inbound.contract import EVENT_TYPES
from fsmes.integrations.inbound.folder import MappingError, StreamMapping, folders_for, stream_mapping, to_event
from fsmes.services import inbound as inbound_service

log = structlog.get_logger("inbound.sql")

#: The bind parameter the plant's query must carry. Without it the query has
#: no cursor and every pass would re-read the supplier's whole history.
WATERMARK_PARAM = "watermark"

#: How the supplier's ordering column is understood. `id` for a monotonic
#: number or string, `timestamp` for a time. It decides only how the value is
#: handed back to the supplier's database in the next query.
POSITION_TYPES = ("id", "timestamp")

#: Words that could change something in a system this MES does not own. A
#: query containing one is refused before it is sent, whatever the
#: credentials allow — being read-only should not depend on somebody else
#: having got the grants right.
FORBIDDEN = (
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "merge", "grant", "revoke", "commit", "rollback", "vacuum", "attach",
    "call", "exec", "execute", "lock",
    # `SELECT ... INTO` writes a table in most dialects, and it is how a
    # read-looking statement stops being a read. It also catches MySQL's
    # `REPLACE INTO`, which is why `replace` is not on this list: as a
    # function it is an ordinary way to tidy a string in a SELECT.
    "into",
)


class SqlConfigError(MappingError):
    """The SQL poller's configuration cannot be used as written, and says why."""


# ------------------------------------------------------------------ the config


@dataclass(frozen=True)
class SqlStream:
    """One plant query, and everything needed to run it on a schedule.

    `mapping` is the same column mapping the folder driver uses, so a plant
    that has both interfaces describes its columns the same way twice and
    nothing about a row means something different depending on how it
    arrived.
    """

    mapping: StreamMapping
    #: The supplier's database, as a SQLAlchemy URL. Read-only credentials;
    #: this MES will not send a statement that could change anything, but the
    #: grants are the guarantee and the docs say so.
    url: str
    #: The plant's own SQL. Must carry `:watermark` and an ORDER BY.
    sql: str
    #: The column in the result that carries the ordering value.
    watermark_column: str
    #: `id` or `timestamp`.
    watermark_type: str
    #: Where a cursor that has never run starts. Exclusive: the query asks
    #: for rows strictly after it. Required, because guessing is a choice
    #: between silently skipping the supplier's backlog and silently reading
    #: ten years of it.
    start_from: str
    poll_seconds: float
    statement_timeout_ms: int
    #: The most rows one pass will take. A backlog drains over several
    #: passes rather than in one transaction that holds for an hour.
    max_rows: int
    #: A sentence for whoever reads the config next. Printed by `sql-check`.
    description: str | None = None

    @property
    def name(self) -> str:
        return self.mapping.name

    @property
    def source(self) -> str:
        return self.mapping.source


_REQUIRED = ("url", "sql", "watermark_column", "watermark_type", "start_from")


def load_streams(path: Path) -> dict[str, SqlStream]:
    """Read the poller's configuration, or say exactly what is wrong with it."""
    path = Path(path)
    if not path.is_file():
        raise SqlConfigError(
            f"no inbound SQL configuration at {path}. It is the file that holds this plant's own "
            "queries — the connection, the SQL and what each column means. There is nothing "
            "sensible to default it to: only the plant knows what its systems are called and how "
            "they are laid out."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SqlConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise SqlConfigError(f"{path} should be an object keyed by stream name, one of {sorted(EVENT_TYPES)}")

    streams: dict[str, SqlStream] = {}
    for name, spec in raw.items():
        streams[name] = _one_stream(name, spec, where=str(path))
    return streams


def _one_stream(name: str, spec: object, *, where: str) -> SqlStream:
    try:
        mapping = stream_mapping(name, spec, where=where)
    except MappingError as exc:
        # The shared half of the configuration, complained about under this
        # file's own name: an operator reading it should not have to know
        # which driver's loader noticed.
        raise SqlConfigError(str(exc)) from exc
    assert isinstance(spec, dict)  # stream_mapping has already refused anything else
    for required in _REQUIRED:
        if spec.get(required) in (None, ""):
            raise SqlConfigError(f"{where}: {name} has no {required!r}, and it is not optional")
    sql = str(spec["sql"])
    _check_sql(sql, name=name, where=where)
    position_type = str(spec["watermark_type"])
    if position_type not in POSITION_TYPES:
        raise SqlConfigError(
            f"{where}: {name} has watermark_type {position_type!r}; it is one of {list(POSITION_TYPES)} — "
            "`id` for a column that only counts upwards, `timestamp` for one that holds a time"
        )
    return SqlStream(
        mapping=mapping,
        url=str(spec["url"]),
        sql=sql,
        watermark_column=str(spec["watermark_column"]),
        watermark_type=position_type,
        start_from=str(spec["start_from"]),
        poll_seconds=float(spec.get("poll_seconds", 60.0)),
        statement_timeout_ms=int(spec.get("statement_timeout_ms", 30_000)),
        max_rows=int(spec.get("max_rows", 500)),
        description=spec.get("description"),
    )


def _check_sql(sql: str, *, name: str, where: str) -> None:
    """Refuse a query that cannot be polled, or that could change anything."""
    if f":{WATERMARK_PARAM}" not in sql:
        raise SqlConfigError(
            f"{where}: the {name} query does not use :{WATERMARK_PARAM}. That parameter is the cursor; "
            "a query without it re-reads the supplier's whole history on every pass, and the position "
            "this MES keeps would be decoration."
        )
    if not re.search(r"\border\s+by\b", sql, re.IGNORECASE):
        raise SqlConfigError(
            f"{where}: the {name} query has no ORDER BY. Rows have to arrive in the order of "
            f"{WATERMARK_PARAM}'s column, or the cursor can move past a row that has not been read. "
            "A database is free to return rows in any order it likes when nothing asks."
        )
    body = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.DOTALL)
    if ";" in body.strip().rstrip(";"):
        raise SqlConfigError(
            f"{where}: the {name} query holds more than one statement. One SELECT, so that what is "
            "sent to a system this MES does not own is a thing a person can read in one line."
        )
    if not re.match(r"\s*(?:with|select)\b", body, re.IGNORECASE):
        raise SqlConfigError(
            f"{where}: the {name} query does not begin with SELECT (or WITH). This driver reads; "
            "nothing it sends may change the supplier's system."
        )
    found = sorted({word for word in FORBIDDEN if re.search(rf"\b{word}\b", body, re.IGNORECASE)})
    if found:
        raise SqlConfigError(
            f"{where}: the {name} query contains {found}, and this driver will not send a statement "
            "that could change a system it does not own. If one of those words is only part of a "
            "column or table name, alias it in a view on that side; read-only credentials are still "
            "the real guarantee either way."
        )


# ----------------------------------------------------------- the connection


@dataclass(frozen=True)
class Connection:
    """How this MES asked to be harmless, and what the dialect allowed."""

    url: str
    #: SQL run on the connection before the query, in order.
    setup: tuple[str, ...]
    connect_args: dict
    #: What was achieved, in words, for `sql-check` to print.
    done: tuple[str, ...]
    #: What this dialect gave no way to do. Never empty for a dialect this
    #: MES has not been taught: silence would read as success.
    could_not: tuple[str, ...]


def read_only(url: str, statement_timeout_ms: int) -> Connection:
    """The same URL, opened as read-only as this dialect knows how.

    Nothing here is a substitute for read-only credentials, and `sql-check`
    prints both what was managed and what was not, because a driver that
    quietly did less than it claimed is worse than one that claimed nothing.
    """
    dialect = url.split(":", 1)[0].split("+", 1)[0].lower()
    setup: list[str] = []
    connect_args: dict = {}
    done: list[str] = []
    could_not: list[str] = []
    seconds = max(1, round(statement_timeout_ms / 1000))

    if dialect == "sqlite":
        url = _sqlite_read_only(url)
        connect_args = {"timeout": seconds, "check_same_thread": False}
        setup.append("PRAGMA query_only = 1")
        done.append("opened the file read-only (`mode=ro`) and set `PRAGMA query_only`")
        could_not.append(f"SQLite has no statement timeout; the busy timeout is {seconds}s, which is "
                         "a wait for a lock and not a limit on a slow query")
    elif dialect == "postgresql":
        setup.append("SET TRANSACTION READ ONLY")
        setup.append(f"SET LOCAL statement_timeout = {statement_timeout_ms}")
        done.append("the transaction is READ ONLY")
        done.append(f"statement_timeout is {statement_timeout_ms}ms")
    elif dialect in ("mysql", "mariadb"):
        setup.append("SET SESSION TRANSACTION READ ONLY")
        setup.append(f"SET SESSION MAX_EXECUTION_TIME = {statement_timeout_ms}")
        done.append("the session's transactions are READ ONLY")
        done.append(f"MAX_EXECUTION_TIME is {statement_timeout_ms}ms")
    elif dialect == "oracle":
        setup.append("SET TRANSACTION READ ONLY")
        done.append("the transaction is READ ONLY")
        could_not.append("Oracle's statement timeout is a profile on the account, not something a "
                         "client sets; ask whoever administers it for a CPU_PER_CALL limit")
    elif dialect in ("mssql", "sqlserver"):
        setup.append(f"SET LOCK_TIMEOUT {statement_timeout_ms}")
        done.append(f"LOCK_TIMEOUT is {statement_timeout_ms}ms")
        could_not.append("SQL Server has no read-only session setting; the read-only credentials are "
                         "the only guarantee, so check the grants")
    else:
        could_not.append(f"this MES has not been taught how to make a {dialect!r} connection read-only "
                         "or how to set a statement timeout on it; the read-only credentials are the "
                         "only guarantee")

    return Connection(url=url, setup=tuple(setup), connect_args=connect_args,
                      done=tuple(done), could_not=tuple(could_not))


def _sqlite_read_only(url: str) -> str:
    """`sqlite:///plant.db` as SQLite's own read-only URI form."""
    prefix, separator, rest = url.partition(":///")
    if not separator or not rest or rest.startswith(":memory:"):
        # An in-memory database has no file to open read-only. Nothing this
        # MES can do about that, and `PRAGMA query_only` still applies.
        return url
    if rest.startswith("file:") or "uri=true" in rest:
        return url  # somebody has already written the URI themselves
    path, _, existing = rest.partition("?")
    query = "mode=ro&uri=true" if not existing else f"{existing}&mode=ro&uri=true"
    return f"{prefix}:///file:{quote(path)}?{query}"


def read_batch(stream: SqlStream, watermark: str) -> tuple[list[dict], Connection]:
    """The supplier's next rows, with their connection closed before we return.

    Every row is materialised here on purpose. This MES's write can take as
    long as it takes without ever being a lock in a system somebody else
    depends on.
    """
    connection = read_only(stream.url, stream.statement_timeout_ms)
    engine = create_engine(connection.url, poolclass=NullPool, connect_args=connection.connect_args)
    try:
        with engine.connect() as conn:
            for statement in connection.setup:
                conn.exec_driver_sql(statement)
            result = conn.execute(text(stream.sql), {WATERMARK_PARAM: bind(watermark)})
            rows = [dict(row) for row in result.mappings().fetchmany(stream.max_rows)]
        return rows, connection
    finally:
        engine.dispose()


def bind(watermark: str) -> str:
    """The cursor exactly as the supplier's column gave it: text, unchanged.

    It would be easy to be cleverer — to parse a timestamp back into a
    `datetime`, or an id back into an `int`, so that more databases compare
    it without being asked. The reason not to is that every one of those
    conversions can change the value. A time re-read into this MES's
    convention moves the boundary by the supplier's offset; a formatter that
    adds microseconds moves it by less than a second, which is worse, because
    nobody will look. The boundary of what has been read is the supplier's
    fact, and this driver hands it back the way it was given.

    SQLite compares text against a typed column by the column's own affinity,
    so it needs nothing more. A database that will not — PostgreSQL will say
    so plainly, and `fsmes inbound sql-check` is where you will see it — wants
    a cast, and the cast belongs in the plant's own query: `WHERE stamp >
    CAST(:watermark AS timestamp)`. The query is the part of this that is
    theirs.
    """
    return watermark


def position_of(value: object) -> str:
    """The supplier's ordering value as the text that will be handed back."""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


# --------------------------------------------------------------- the report


@dataclass
class RowProblem:
    """One row that was not recorded, and why, in one sentence."""

    row: int
    key: str | None
    position: str | None
    reason: str

    def render(self) -> str:
        named = f"row {self.row}"
        if self.key:
            named += f" ({self.key})"
        return f"  {named}: {self.reason}"


@dataclass
class StreamReport:
    """What one pass over one query did. Totals are over the batch."""

    stream: str
    source: str
    rows: int = 0
    applied: int = 0
    duplicates: int = 0
    problems: list[RowProblem] = field(default_factory=list)
    watermark_before: str | None = None
    watermark_after: str | None = None
    held: RowProblem | None = None
    #: True when the batch filled `max_rows`, so more rows are waiting.
    batch_full: bool = False
    #: Set when the whole stream failed — the supplier could not be reached,
    #: or the query would not run. No row was read; the cursor did not move.
    error: str | None = None
    rejects_file: str | None = None
    #: What the connection managed and what it could not, for `sql-check`.
    connection: Connection | None = None

    @property
    def rejected(self) -> int:
        return len(self.problems)

    def render(self) -> list[str]:
        if self.error:
            return [f"{self.stream}: {self.error}",
                    f"  the cursor did not move; it is still at {self.watermark_before!r}"]
        lines = [f"{self.stream} from {self.source}: {self.rows} row{'' if self.rows == 1 else 's'} read, "
                 f"{self.applied} recorded, {self.duplicates} already seen, {self.rejected} rejected"]
        lines += [problem.render() for problem in self.problems]
        if self.watermark_before != self.watermark_after:
            lines.append(f"  cursor {self.watermark_before!r} -> {self.watermark_after!r}")
        else:
            lines.append(f"  cursor unchanged at {self.watermark_before!r}")
        if self.held is not None:
            lines.append(f"  HELD at {self.held.position!r} by row {self.held.row}"
                         + (f" ({self.held.key})" if self.held.key else "")
                         + f": {self.held.reason}")
            lines.append("  Every later row in the batch was still recorded. The cursor stays here and "
                         "this row is tried again next pass. Fix it where it is, or step over it "
                         f"deliberately with `fsmes inbound sql-watermark --stream {self.stream} --set "
                         "<value>`, which says in words what it is skipping.")
        if self.batch_full:
            lines.append(f"  the batch was full at max_rows={self.rows}; more rows are waiting and the "
                         "next pass will take them")
        if self.rejects_file:
            lines.append(f"  the same lines are in {self.rejects_file}")
        return lines


@dataclass
class PollReport:
    """What one pass over every configured query did."""

    streams: list[StreamReport] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return sum(s.rows for s in self.streams)

    @property
    def applied(self) -> int:
        return sum(s.applied for s in self.streams)

    @property
    def duplicates(self) -> int:
        return sum(s.duplicates for s in self.streams)

    @property
    def rejected(self) -> int:
        return sum(s.rejected for s in self.streams)

    @property
    def held(self) -> list[str]:
        return [s.stream for s in self.streams if s.held is not None]

    @property
    def failed(self) -> list[str]:
        return [s.stream for s in self.streams if s.error]

    def anything_to_say(self) -> bool:
        return any(s.rows or s.error for s in self.streams)

    def render(self) -> list[str]:
        lines: list[str] = []
        for report in self.streams:
            lines.extend(report.render())
        count = len(self.streams)
        lines.append(f"{count} quer{'y' if count == 1 else 'ies'}, {self.rows} "
                     f"row{'' if self.rows == 1 else 's'}: {self.applied} recorded, "
                     f"{self.duplicates} already seen, {self.rejected} rejected"
                     + (f"; {len(self.held)} cursor{'' if len(self.held) == 1 else 's'} held "
                        f"({', '.join(self.held)})" if self.held else "")
                     + (f"; {len(self.failed)} quer{'y' if len(self.failed) == 1 else 'ies'} failed "
                        f"({', '.join(self.failed)})" if self.failed else ""))
        return lines


# ------------------------------------------------------------------ the cursor


def watermark_row(session, stream: SqlStream) -> InboundWatermark | None:
    from sqlalchemy import select

    return session.scalar(
        select(InboundWatermark).where(
            InboundWatermark.source == stream.source,
            InboundWatermark.stream == stream.name,
        )
    )


def watermark_of(session, stream: SqlStream) -> str:
    """Where this stream has read through, or where its configuration starts it."""
    row = watermark_row(session, stream)
    return row.position if row is not None else stream.start_from


def _save_watermark(session, stream: SqlStream, position: str, *, rows: int,
                    held: RowProblem | None) -> None:
    row = watermark_row(session, stream)
    if row is None:
        row = InboundWatermark(source=stream.source, stream=stream.name,
                               position=position, position_type=stream.watermark_type,
                               rows_seen=0)
        session.add(row)
    row.position = position[:120]
    row.position_type = stream.watermark_type
    row.rows_seen = (row.rows_seen or 0) + rows
    row.held_reason = held.reason[:300] if held is not None else None
    row.held_key = (held.key or held.position or "")[:120] if held is not None else None
    row.updated_at = utcnow()
    session.flush()


def set_watermark(session, stream: SqlStream, position: str) -> str:
    """Move the cursor by hand, and say where it was. Never called by a pass.

    This is the one operation here that can lose data: rows between where the
    cursor stood and where it is being put will never be read, because the
    query will not ask for them again. The caller prints what that is before
    doing it.
    """
    was = watermark_of(session, stream)
    _save_watermark(session, stream, position, rows=0, held=None)
    return was


# --------------------------------------------------------------------- a pass


def poll_stream(session_scope, stream: SqlStream, *, rejects_root: Path | None = None) -> StreamReport:
    """Read one query's next batch, record it, and move the cursor honestly."""
    report = StreamReport(stream=stream.name, source=stream.source)
    with session_scope() as session:
        watermark = watermark_of(session, stream)
    report.watermark_before = watermark
    report.watermark_after = watermark

    try:
        rows, connection = read_batch(stream, watermark)
    except SQLAlchemyError as exc:
        # Reaching the supplier is the part of this that is somebody else's
        # uptime. It is reported and the cursor does not move; the next pass
        # asks for exactly the same rows.
        log.warning("inbound sql query failed", stream=stream.name, error=str(exc))
        report.error = f"the query could not be run: {_one_line(exc)}"
        return report
    report.connection = connection
    report.rows = len(rows)
    report.batch_full = len(rows) >= stream.max_rows
    if rows and stream.watermark_column not in rows[0]:
        # Every row would say the same thing, so say it once, before anything
        # is written. Nothing was recorded and the cursor cannot move: it has
        # no column to move along.
        report.error = (f"the result has no column {stream.watermark_column!r}, named as the "
                        f"watermark; its columns are {sorted(rows[0])}")
        return report

    write = inbound_service.WRITERS[stream.name]
    advance_to: str | None = None
    still_advancing = True
    with session_scope() as session:
        for number, row in enumerate(rows, start=1):
            position = position_of(row[stream.watermark_column])
            key = _key_in(row, stream)
            savepoint = session.begin_nested()
            try:
                event = to_event(_as_text(row), stream.mapping)
                outcome = write(session, event)
            except (ValueError, inbound_service.Refused) as exc:
                savepoint.rollback()
                problem = RowProblem(row=number, key=key, position=position, reason=str(exc))
                report.problems.append(problem)
                if still_advancing:
                    report.held = problem
                    still_advancing = False
                continue
            except Exception as exc:  # one bad row must not stall the interface
                savepoint.rollback()
                log.error("inbound sql row failed", stream=stream.name, row=number, error=str(exc))
                problem = RowProblem(row=number, key=key, position=position,
                                     reason=f"{type(exc).__name__}: {exc}")
                report.problems.append(problem)
                if still_advancing:
                    report.held = problem
                    still_advancing = False
                continue
            savepoint.commit()
            if outcome.duplicate:
                report.duplicates += 1
            else:
                report.applied += 1
            if still_advancing:
                advance_to = position

        if advance_to is not None:
            _save_watermark(session, stream, advance_to, rows=report.applied + report.duplicates,
                            held=report.held)
            report.watermark_after = advance_to
        elif report.held is not None:
            _save_watermark(session, stream, watermark, rows=0, held=report.held)

    if report.problems and rejects_root is not None:
        report.rejects_file = _write_rejects(report, stream, rejects_root)
    return report


def poll_once(session_scope, streams: dict[str, SqlStream], *,
              rejects_root: Path | None = None) -> PollReport:
    """One pass over every configured query, in stream order."""
    report = PollReport()
    for _, stream in sorted(streams.items()):
        report.streams.append(poll_stream(session_scope, stream, rejects_root=rejects_root))
    return report


def _as_text(row: dict) -> dict:
    """The supplier's row with every value as text, for the shared mapper.

    The folder driver's mapper reads strings, because a CSV has nothing else.
    A database hands back real types, and `str()` of each of them is a form
    that mapper parses back — a naive datetime stays naive, so the mapping's
    `timezone` still decides what it means, which is the whole point.
    """
    return {key: (None if value is None else position_of(value)) for key, value in row.items()}


def _key_in(row: dict, stream: SqlStream) -> str | None:
    """The supplier's own id for the row, if the mapping says which column it is."""
    column = stream.mapping.columns.get("external_key")
    if column is None or column not in row or row[column] is None:
        return None
    return str(row[column])


def _one_line(exc: Exception) -> str:
    """A driver's error as one sentence: the first line, and nothing else."""
    return str(exc).strip().splitlines()[0][:300]


def _write_rejects(report: StreamReport, stream: SqlStream, root: Path) -> str:
    """The rejects report, beside the folder driver's, so the habit is one habit."""
    folder = folders_for(root, stream.name).rejected
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"sql.{stream.name}.{utcnow().strftime('%Y%m%dT%H%M%S')}.rejects.txt"
    counter = 1
    while target.exists():
        target = folder / f"sql.{stream.name}.{utcnow().strftime('%Y%m%dT%H%M%S')}-{counter}.rejects.txt"
        counter += 1
    lines = [f"{stream.name} from {stream.source}", *report.render()[0:1],
             f"cursor before {report.watermark_before!r}, after {report.watermark_after!r}", ""]
    lines += [problem.render().strip() for problem in report.problems]
    lines.append("")
    lines.append("Nothing above was recorded. The rows are still in the supplying system; fix them "
                 "there, or fix the mapping, and the next pass will read them again. Anything already "
                 "recorded will not be recorded twice.")
    target.write_text("\n".join(lines), encoding="utf-8")
    return str(target)
