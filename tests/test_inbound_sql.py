"""The SQL poller: reading events out of a database the plant already has.

Six promises are pinned here, because each of them is a way a polling
interface loses data quietly or misbehaves in somebody else's system.

It only reads, and it says so where it could not make that true.
It never holds the supplier's transaction open across our own write.
It reads nothing twice: the cursor is the supplier's own ordering column.
It skips nothing: a row that could not be recorded holds the cursor where it
is, by name, rather than being stepped over.
It states its totals, over the batch, with the reason for every row refused.
And nothing in the repository knows any commercial system's schema — the
query is the plant's, so every query in this file is an invention of this
file.
"""

import dataclasses
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from fsmes.domain import InboundWatermark, ProductionLog
from fsmes.integrations.inbound import sql
from fsmes.services import inbound as inbound_service

# Every name below is this file's own invention. A plant's real query names
# its own tables, which is why the SQL is configuration and not code.
COUNTS_TABLE = """
CREATE TABLE manual_counts (
    entry_id   INTEGER PRIMARY KEY,
    entered_at TEXT,
    counted_at TEXT,
    machine    TEXT,
    order_no   TEXT,
    good_qty   REAL,
    scrap_qty  REAL
)
"""

COUNTS_SQL = ("SELECT entry_id, entered_at, counted_at, machine, order_no, good_qty, scrap_qty "
              "FROM manual_counts WHERE entry_id > :watermark ORDER BY entry_id")

COUNTS_COLUMNS = {
    "external_key": "entry_id",
    "recorded_at": "entered_at",
    "when": "counted_at",
    "equipment": "machine",
    "order": "order_no",
    "good": "good_qty",
    "scrap": "scrap_qty",
}


def spec(**overrides) -> dict:
    base = {
        "source": "replay:another-system",
        "source_kind": "replay",
        "timezone": "UTC",
        "url": "sqlite:///nowhere.sqlite",
        "sql": COUNTS_SQL,
        "watermark_column": "entry_id",
        "watermark_type": "id",
        "start_from": "0",
        "poll_seconds": 60,
        "max_rows": 500,
        "columns": COUNTS_COLUMNS,
    }
    base.update(overrides)
    return base


def config(tmp_path: Path, **overrides) -> dict[str, sql.SqlStream]:
    path = tmp_path / "inbound_sql.json"
    path.write_text(json.dumps({"counts": spec(**overrides)}), encoding="utf-8")
    return sql.load_streams(path)


@pytest.fixture()
def source(tmp_path) -> Path:
    """A supplier's database: one table, three rows, nothing else."""
    path = tmp_path / "example_source.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(COUNTS_TABLE)
        db.executemany(
            "INSERT INTO manual_counts VALUES (?,?,?,?,?,?,?)",
            [(1, "2026-09-10 17:00:00", "2026-09-10 16:30:00", "MIX01", "", 3, 1),
             (2, "2026-09-10 17:05:00", "2026-09-10 16:40:00", "MIX01", "", 2, 0),
             (3, "2026-09-10 17:10:00", "2026-09-10 16:50:00", "MIX01", "", 5, 0)],
        )
    return path


@pytest.fixture()
def streams(tmp_path, source) -> dict[str, sql.SqlStream]:
    return config(tmp_path, url=f"sqlite:///{source}")


def add_row(source: Path, *values) -> None:
    with sqlite3.connect(source) as db:
        db.execute("INSERT INTO manual_counts VALUES (?,?,?,?,?,?,?)", values)


# ------------------------------------------------------------- configuration


def test_a_missing_configuration_file_says_what_it_was_for(tmp_path):
    with pytest.raises(sql.SqlConfigError, match="this plant's own"):
        sql.load_streams(tmp_path / "nowhere.json")


def test_a_query_with_no_cursor_parameter_is_refused_because_it_would_re_read_everything(tmp_path):
    with pytest.raises(sql.SqlConfigError, match="re-reads the supplier's whole history"):
        config(tmp_path, sql="SELECT entry_id FROM manual_counts ORDER BY entry_id")


def test_a_query_with_no_order_by_is_refused_because_the_cursor_could_step_over_a_row(tmp_path):
    with pytest.raises(sql.SqlConfigError, match="no ORDER BY"):
        config(tmp_path, sql="SELECT entry_id FROM manual_counts WHERE entry_id > :watermark")


def test_a_query_that_is_not_a_select_at_all_is_refused(tmp_path):
    with pytest.raises(sql.SqlConfigError, match="does not begin with SELECT"):
        config(tmp_path, sql="UPDATE manual_counts SET good_qty = 0 WHERE entry_id > :watermark "
                             "ORDER BY entry_id")


def test_a_write_smuggled_inside_a_select_is_refused_before_it_is_ever_sent(tmp_path):
    """A data-modifying CTE is a real thing and it starts with the word SELECT's friend."""
    with pytest.raises(sql.SqlConfigError, match="will not send a statement"):
        config(tmp_path, sql="WITH gone AS (DELETE FROM manual_counts RETURNING entry_id) "
                             "SELECT entry_id FROM gone WHERE entry_id > :watermark ORDER BY entry_id")


def test_a_second_statement_smuggled_after_a_semicolon_is_refused(tmp_path):
    with pytest.raises(sql.SqlConfigError, match="more than one statement"):
        config(tmp_path, sql="SELECT entry_id FROM manual_counts WHERE entry_id > :watermark "
                             "ORDER BY entry_id; SELECT 1")


def test_a_stream_with_no_start_from_is_refused_rather_than_guessing_where_to_begin(tmp_path):
    """Guessing is a choice between skipping the backlog and reading ten years of it."""
    path = tmp_path / "c.json"
    without = {key: value for key, value in spec().items() if key != "start_from"}
    path.write_text(json.dumps({"counts": without}), encoding="utf-8")
    with pytest.raises(sql.SqlConfigError, match="start_from"):
        sql.load_streams(path)


def test_a_cursor_type_this_driver_does_not_know_is_refused(tmp_path):
    with pytest.raises(sql.SqlConfigError, match="watermark_type"):
        config(tmp_path, watermark_type="guid")


def test_a_mapping_naming_a_field_the_contract_does_not_have_is_refused(tmp_path):
    with pytest.raises(sql.SqlConfigError, match="widgets"):
        config(tmp_path, columns={"external_key": "entry_id", "widgets": "w"})


def test_the_configuration_the_repository_ships_is_one_the_poller_can_read():
    """The packaged example has to load, or a fresh install cannot start.

    It also has to name no product: the assertion below is on the shape,
    and the clean-room rule is on the file.
    """
    streams = sql.load_streams(Path("config/inbound_sql.json"))
    assert streams, "the shipped example configures no query"
    for stream in streams.values():
        assert stream.mapping.timezone, "the example must say which zone its timestamps are in"
        assert stream.url.startswith("sqlite:"), (
            "the shipped example may name no database but a SQLite file the docs "
            "tell you how to make")


# ---------------------------------------------------------------- read-only


def test_a_sqlite_source_is_opened_read_only_and_a_write_through_it_is_refused(source):
    connection = sql.read_only(f"sqlite:///{source}", 30_000)

    engine = create_engine(connection.url, poolclass=None, connect_args=connection.connect_args)
    with engine.connect() as conn:
        for statement in connection.setup:
            conn.exec_driver_sql(statement)
        assert conn.execute(text("SELECT count(*) FROM manual_counts")).scalar() == 3
        with pytest.raises(SQLAlchemyError):
            conn.exec_driver_sql("INSERT INTO manual_counts (entry_id) VALUES (99)")
    engine.dispose()
    assert connection.done, "nothing was reported as achieved"


def test_a_dialect_this_mes_cannot_make_read_only_says_so_rather_than_staying_quiet():
    """Silence would read as success, which is the whole problem with a claim."""
    unknown = sql.read_only("somedb+driver://user:pw@host/db", 30_000)
    assert unknown.done == ()
    assert any("only guarantee" in line for line in unknown.could_not)

    sqlserver = sql.read_only("mssql+pyodbc://user:pw@host/db", 30_000)
    assert any("read-only session setting" in line for line in sqlserver.could_not)


def test_a_statement_timeout_is_asked_for_where_the_dialect_has_one():
    postgres = sql.read_only("postgresql+psycopg://user:pw@host/db", 12_000)
    assert "SET TRANSACTION READ ONLY" in postgres.setup
    assert any("statement_timeout = 12000" in line for line in postgres.setup)

    lite = sql.read_only("sqlite:///plant.db", 30_000)
    assert any("no statement timeout" in line for line in lite.could_not), (
        "SQLite has none, and a driver that implied otherwise would be lying")


def test_the_suppliers_transaction_is_closed_before_this_mes_writes_anything(
        session, scope, streams, source, monkeypatch):
    """A long write here must never be a lock in a system somebody depends on.

    SQLite is the proof: a reader holds a shared lock for as long as its
    transaction is open, so if the poller still had one, the write below —
    made through a second connection while our own writer is running — could
    not get through.
    """
    written = []
    real = inbound_service.WRITERS["counts"]

    def write_to_the_supplier_first(mes_session, event):
        with sqlite3.connect(source, timeout=1) as db:
            db.execute("UPDATE manual_counts SET machine = 'MIX01' WHERE entry_id = 1")
        written.append(event.external_key)
        return real(mes_session, event)

    monkeypatch.setitem(inbound_service.WRITERS, "counts", write_to_the_supplier_first)
    report = sql.poll_stream(scope, streams["counts"])

    assert report.error is None, report.error
    assert written == ["1", "2", "3"]


# --------------------------------------------------------------- reading it


def test_counts_polled_from_another_system_appear_in_the_mes_attributed_to_their_source(
        session, scope, streams):
    report = sql.poll_stream(scope, streams["counts"])

    assert (report.rows, report.applied, report.duplicates, report.rejected) == (3, 3, 0, 0)
    rows = session.query(ProductionLog).all()
    assert [row.good_qty for row in rows] == [3.0, 2.0, 5.0]
    assert {row.source_system for row in rows} == {"replay:another-system"}
    assert (report.watermark_before, report.watermark_after) == ("0", "3")


def test_the_supplier_s_own_clock_is_what_is_recorded_and_never_ours(session, scope, streams):
    sql.poll_stream(scope, streams["counts"])

    first = session.query(ProductionLog).order_by(ProductionLog.id).first()
    # `counted_at` in the supplier's rows, read as UTC because the mapping says so.
    assert first.ts == datetime(2026, 9, 10, 16, 30)


def test_a_second_pass_reads_only_what_the_supplier_has_added_since(session, scope, streams, source):
    sql.poll_stream(scope, streams["counts"])
    add_row(source, 4, "2026-09-10 18:00:00", "2026-09-10 17:30:00", "MIX01", "", 7, 0)

    second = sql.poll_stream(scope, streams["counts"])

    assert (second.rows, second.applied, second.duplicates) == (1, 1, 0)
    assert second.watermark_after == "4"
    assert session.query(ProductionLog).count() == 4


def test_a_pass_with_nothing_new_reads_nothing_and_moves_nothing(session, scope, streams):
    sql.poll_stream(scope, streams["counts"])

    again = sql.poll_stream(scope, streams["counts"])

    assert (again.rows, again.applied) == (0, 0)
    assert again.watermark_before == again.watermark_after == "3"


def test_the_cursor_is_where_a_restart_picks_up_from(session, scope, streams, tmp_path, source):
    """Nothing is held in the process: the cursor is a row in this database."""
    sql.poll_stream(scope, streams["counts"])
    add_row(source, 4, "2026-09-10 18:00:00", "2026-09-10 17:30:00", "MIX01", "", 7, 0)

    # A new process reads its configuration again and knows nothing else.
    reloaded = config(tmp_path, url=f"sqlite:///{source}")
    after_restart = sql.poll_stream(scope, reloaded["counts"])

    assert (after_restart.rows, after_restart.applied) == (1, 1)
    assert session.query(ProductionLog).count() == 4


def test_a_cursor_wound_back_by_hand_records_nothing_twice(session, scope, streams):
    """The cursor is an optimisation. The supplier's own key is the guarantee."""
    sql.poll_stream(scope, streams["counts"])

    was = sql.set_watermark(session, streams["counts"], "0")
    again = sql.poll_stream(scope, streams["counts"])

    assert was == "3"
    assert (again.rows, again.applied, again.duplicates) == (3, 0, 3)
    assert session.query(ProductionLog).count() == 3


def test_a_timestamp_cursor_goes_back_to_the_supplier_in_its_own_words(session, scope, tmp_path, source):
    """A time normalised into this MES's convention would move the boundary."""
    streams = config(
        tmp_path, url=f"sqlite:///{source}", watermark_column="entered_at",
        watermark_type="timestamp", start_from="2026-09-10 17:00:00",
        sql="SELECT entry_id, entered_at, counted_at, machine, order_no, good_qty, scrap_qty "
            "FROM manual_counts WHERE entered_at > :watermark ORDER BY entered_at")

    first = sql.poll_stream(scope, streams["counts"])

    # Row 1 is at exactly the start, which is exclusive, so two rows arrive.
    assert (first.rows, first.applied) == (2, 2)
    assert first.watermark_after == "2026-09-10 17:10:00", (
        "the cursor must be the text the supplier's column gave, not our own reading of it")
    assert sql.poll_stream(scope, streams["counts"]).rows == 0


# ----------------------------------------------------- what will not be read


def test_the_cursor_does_not_move_past_a_row_that_could_not_be_recorded(session, scope, tmp_path, source):
    add_row(source, 4, "2026-09-10 18:00:00", "2026-09-10 17:30:00", "NOPE", "", 7, 0)
    add_row(source, 5, "2026-09-10 18:05:00", "2026-09-10 17:35:00", "MIX01", "", 8, 0)
    streams = config(tmp_path, url=f"sqlite:///{source}")

    report = sql.poll_stream(scope, streams["counts"])

    assert (report.rows, report.applied, report.rejected) == (5, 4, 1)
    # Row 5 was still recorded - leaving good data unread would be its own
    # dishonesty - but the cursor stops at row 4 and says which row holds it.
    assert report.watermark_after == "3"
    assert report.held is not None
    assert report.held.row == 4 and report.held.key == "4"
    assert "NOPE" in report.held.reason


def test_a_held_cursor_asks_for_the_same_row_again_on_the_next_pass(session, scope, tmp_path, source):
    add_row(source, 4, "2026-09-10 18:00:00", "2026-09-10 17:30:00", "NOPE", "", 7, 0)
    streams = config(tmp_path, url=f"sqlite:///{source}")
    sql.poll_stream(scope, streams["counts"])

    again = sql.poll_stream(scope, streams["counts"])

    assert (again.rows, again.applied, again.duplicates, again.rejected) == (1, 0, 0, 1)
    assert again.held is not None
    assert session.query(ProductionLog).count() == 3


def test_a_held_cursor_is_recorded_where_a_person_can_find_it(session, scope, tmp_path, source):
    add_row(source, 4, "2026-09-10 18:00:00", "2026-09-10 17:30:00", "NOPE", "", 7, 0)
    streams = config(tmp_path, url=f"sqlite:///{source}")

    sql.poll_stream(scope, streams["counts"])

    row = session.query(InboundWatermark).one()
    assert (row.source, row.stream, row.position) == ("replay:another-system", "counts", "3")
    assert row.held_key == "4"
    assert "NOPE" in row.held_reason
    assert row.rows_seen == 3


def test_stepping_over_a_held_row_is_a_decision_a_person_makes_by_hand(session, scope, tmp_path, source):
    add_row(source, 4, "2026-09-10 18:00:00", "2026-09-10 17:30:00", "NOPE", "", 7, 0)
    add_row(source, 5, "2026-09-10 18:05:00", "2026-09-10 17:35:00", "MIX01", "", 8, 0)
    streams = config(tmp_path, url=f"sqlite:///{source}")
    sql.poll_stream(scope, streams["counts"])

    was = sql.set_watermark(session, streams["counts"], "4")
    after = sql.poll_stream(scope, streams["counts"])

    assert was == "3"
    assert (after.rows, after.duplicates, after.held) == (1, 1, None)
    assert session.query(InboundWatermark).one().held_reason is None


def test_a_report_states_its_totals_and_the_reason_for_every_row_refused(session, scope, tmp_path, source):
    add_row(source, 4, "2026-09-10 18:00:00", "2026-09-10 17:30:00", "NOPE", "", 7, 0)
    streams = config(tmp_path, url=f"sqlite:///{source}")

    lines = sql.poll_once(scope, streams).render()

    assert "4 rows read, 3 recorded, 0 already seen, 1 rejected" in lines[0]
    assert any("HELD at '4'" in line for line in lines)
    assert any("row 4 (4):" in line for line in lines)
    assert lines[-1].startswith("1 query, 4 rows: 3 recorded, 0 already seen, 1 rejected; "
                                "1 cursor held (counts)")


def test_the_rejected_rows_are_written_beside_the_folder_drivers_report(
        session, scope, tmp_path, source):
    add_row(source, 4, "2026-09-10 18:00:00", "2026-09-10 17:30:00", "NOPE", "", 7, 0)
    streams = config(tmp_path, url=f"sqlite:///{source}")
    root = tmp_path / "inbound"

    report = sql.poll_stream(scope, streams["counts"], rejects_root=root)

    written = Path(report.rejects_file)
    assert written.parent == root / "rejected" / "counts"
    body = written.read_text(encoding="utf-8")
    assert "row 4 (4)" in body and "NOPE" in body
    assert "will not be recorded twice" in body


def test_a_naive_timestamp_with_no_configured_zone_is_rejected_rather_than_read_as_utc(
        session, scope, tmp_path, source):
    """A local time read as UTC moves every count in a shift by hours."""
    streams = config(tmp_path, url=f"sqlite:///{source}", timezone=None)

    report = sql.poll_stream(scope, streams["counts"])

    assert (report.rows, report.applied, report.rejected) == (3, 0, 3)
    assert "could move it by hours" in report.problems[0].reason
    assert report.watermark_after == "0", "nothing was read, so nothing was read through"


# ------------------------------------------------------- somebody else's box


def test_a_source_that_cannot_be_reached_moves_no_cursor_and_says_so(session, scope, tmp_path):
    streams = config(tmp_path, url=f"sqlite:///{tmp_path / 'not-there.sqlite'}")

    report = sql.poll_stream(scope, streams["counts"])

    assert report.error is not None
    assert report.watermark_before == report.watermark_after == "0"
    assert any("did not move" in line for line in report.render())
    assert session.query(InboundWatermark).count() == 0


def test_a_result_without_the_cursor_column_is_refused_before_anything_is_written(
        session, scope, tmp_path, source):
    streams = config(tmp_path, url=f"sqlite:///{source}",
                     sql="SELECT entered_at, counted_at, machine, order_no, good_qty, scrap_qty "
                         "FROM manual_counts WHERE entry_id > :watermark ORDER BY entry_id")

    report = sql.poll_stream(scope, streams["counts"])

    assert report.error is not None and "entry_id" in report.error
    assert session.query(ProductionLog).count() == 0


def test_a_backlog_is_drained_over_several_passes_rather_than_one_long_transaction(
        session, scope, tmp_path, source):
    streams = config(tmp_path, url=f"sqlite:///{source}", max_rows=2)

    first = sql.poll_stream(scope, streams["counts"])
    second = sql.poll_stream(scope, streams["counts"])

    assert (first.rows, first.batch_full, first.watermark_after) == (2, True, "2")
    assert any("more rows are waiting" in line for line in first.render())
    assert (second.rows, second.batch_full, second.watermark_after) == (1, False, "3")
    assert session.query(ProductionLog).count() == 3


# --------------------------------------------------------------- the example


def test_the_shipped_example_query_runs_against_the_database_the_docs_describe(
        session, scope, tmp_path):
    """The snippet on the inbound page has to work, or it is not documentation."""
    made = tmp_path / "example_source.sqlite"
    with sqlite3.connect(made) as db:
        db.execute(COUNTS_TABLE)
        db.execute("INSERT INTO manual_counts VALUES "
                   "(1, '2026-09-10 17:00:00', '2026-09-10 16:30:00', 'MIX01', '', 4, 0)")

    shipped = sql.load_streams(Path("config/inbound_sql.json"))["counts"]
    report = sql.poll_stream(scope, dataclasses.replace(shipped, url=f"sqlite:///{made}"))

    assert (report.rows, report.applied, report.rejected) == (1, 1, 0)
    assert session.query(ProductionLog).one().good_qty == 4.0
