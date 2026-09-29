"""Five nullable columns, and what each one is allowed to say.

Milestone D1 of `docs/design/deep-analysis.md`. Each of these columns is a
fact the plant either already held somewhere else or could work out at the
moment it wrote the row, and dropped: the screen a question was asked from,
the shift a turn and an audit row fell in, who booked a quantity, and where
a person normally works.

**What these tests are really pinning is the null.** Every column is
nullable and nothing backfills, so the failure to guard against is not a
missing value - it is a plausible one. A booking with `booked_by="system"`,
a turn stamped with today's roster because it was written before the
column, a person quietly filed under the first line on the list: each of
those turns *we were not told* into an answer, and each would be read as
one. So the tests come in pairs - what is written when the plant knows, and
what is written when it does not.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from fsmes.config import get_settings
from fsmes.db import utcnow
from fsmes.domain import AiTurn, AuditLog, Equipment, Person, ProductionLog, ProductionSource
from fsmes.services import ai_trace, calendar, execution, masterdata, trace_analysis, workorders


@pytest.fixture()
def zone(monkeypatch):
    """Put the plant in Chicago for the length of one test."""
    def put(name: str) -> ZoneInfo:
        monkeypatch.setenv("MES_PLANT_TIMEZONE", name)
        get_settings.cache_clear()
        return ZoneInfo(name)
    yield put
    get_settings.cache_clear()


def _day_shift(session) -> None:
    calendar.create_pattern(session, code="DAY", name="Day shift",
                            starts=time(6, 0), ends=time(22, 0), days="1111111")
    session.flush()


def _at(tz: ZoneInfo, day: date, hour: int, minute: int = 0) -> datetime:
    """A plant wall-clock reading as the naive UTC every table stores."""
    return datetime.combine(day, time(hour, minute),
                            tzinfo=tz).astimezone(UTC).replace(tzinfo=None)


def _turn(db, **over) -> AiTurn:
    row = {"session": "s1", "user": "SCOTT", "model": "claude-sonnet-5",
           "kind": "reply", "asked": "how do I record a check", "said": "here"}
    return ai_trace.record(db, row | over)


# ------------------------------------------------ the screen a question came from


def test_a_question_asked_from_a_configuration_screen_records_the_path_and_not_the_setting(session):
    """The column says *where*, and a query string says *what*.

    `/dashboard/config/engineering?setting=nc_code_prefix` names the row the
    person was looking at. Keeping it would make a column meant for "which
    screens generate questions" into a second, ungated copy of what they were
    reading - and it would be read as the first.
    """
    turn = _turn(session, screen="/dashboard/config/engineering?setting=nc_code_prefix")

    assert turn.screen == "/dashboard/config/engineering"


def test_a_turn_that_names_no_screen_records_none_rather_than_a_screen_nobody_was_on(session):
    """Null is *not recorded*: the design chat names its screen in words and
    a replayed turn was asked from nowhere. Neither is a path."""
    assert _turn(session).screen is None
    assert _turn(session, screen="").screen is None
    assert _turn(session, screen="Production").screen is None


def test_the_assistant_route_hands_the_trace_the_screen_the_browser_posted(session, monkeypatch):
    """The wiring, not the column. `AgentIn.screen` has been posted by every
    browser since the panel existed and the route dropped it on the floor;
    what is under test is that the route is the layer that passes it on,
    because `agent` builds the turn and is never told where anybody stood.
    """
    from fsmes.api.routers import assist

    class _Session:
        def __init__(self):
            self.id = "s9"
            self.user = "SCOTT"
            self.last_turn = {"session": "s9", "user": "SCOTT", "kind": "reply",
                              "asked": "what now", "said": "this"}

    written: list[dict] = []
    monkeypatch.setattr(assist.ai_trace, "record_quietly",
                        lambda db, row, **kw: written.append(row))
    monkeypatch.setattr(assist, "_trace_days", lambda db: 90)

    assist._record(_Session(), screen="/dashboard/quality?tab=checks")

    assert written and written[0]["screen"] == "/dashboard/quality?tab=checks"
    # And what the trace makes of it, which is where the query string goes.
    assert ai_trace.screen_path(written[0]["screen"]) == "/dashboard/quality"

    # A confirm and a decline post no screen, and none is guessed from the
    # message before: the plant was never told where that click happened.
    written.clear()
    assist._record(_Session())
    assert "screen" not in written[0]


# --------------------------------------------------------------- the shift stamp


def test_a_turn_taken_at_two_in_the_afternoon_carries_the_day_shift(session, zone):
    tz = zone("America/Chicago")
    _day_shift(session)

    turn = _turn(session, ts=_at(tz, date(2026, 9, 10), 14, 0))

    assert (turn.shift_code, turn.shift_day) == ("DAY", date(2026, 9, 10))


def test_a_turn_taken_at_three_in_the_morning_on_a_day_only_plant_carries_no_shift(session, zone):
    """Null is *not attributed*, not a shift nobody named. An hour nobody
    rostered is a finding about the calendar, and folding it into the
    nearest shift would hide it (decision 0028)."""
    tz = zone("America/Chicago")
    _day_shift(session)

    turn = _turn(session, ts=_at(tz, date(2026, 9, 10), 3, 0))

    assert (turn.shift_code, turn.shift_day) == (None, None)


def test_a_plant_with_no_shift_patterns_stamps_nothing_on_a_turn(session):
    turn = _turn(session)
    assert (turn.shift_code, turn.shift_day) == (None, None)


def test_an_audit_row_carries_the_shift_the_action_fell_in(session, zone):
    """A change and the production it explains should be readable in the
    same unit a plant is run in."""
    tz = zone("America/Chicago")
    _day_shift(session)
    at_two = _at(tz, date(2026, 9, 10), 14, 0)

    import fsmes.services.audit as audit_module
    monkey = pytest.MonkeyPatch()
    monkey.setattr(audit_module, "utcnow", lambda: at_two)
    try:
        masterdata.create_person(session, code="NEW1", name="New Person", actor="ADMIN")
    finally:
        monkey.undo()

    row = session.scalars(
        select(AuditLog).where(AuditLog.entity_id == "NEW1")).one()
    assert (row.shift_code, row.shift_day) == ("DAY", date(2026, 9, 10))


def test_an_audit_row_is_still_written_when_the_calendar_cannot_be_read(session, caplog):
    """The record outranks the attribution. An audit trail that refused a
    write because it could not name a shift would be the tail wagging the
    dog; the row is written unattributed and the failure is a log line."""
    import fsmes.services.calendar as calendar_module

    monkey = pytest.MonkeyPatch()
    monkey.setattr(calendar_module, "shift_for",
                   lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no calendar")))
    try:
        masterdata.create_person(session, code="NEW2", name="Other", actor="ADMIN")
    finally:
        monkey.undo()

    row = session.scalars(select(AuditLog).where(AuditLog.entity_id == "NEW2")).one()
    assert (row.shift_code, row.shift_day) == (None, None)


# ------------------------------------------------------- who booked a quantity


@pytest.fixture()
def released_order(session):
    workorders.create(session, code="WO-1", material_code="FG-COLA", quantity=40, actor="test")
    workorders.release(session, "WO-1", "test")
    workorders.start_operation(session, "WO-1", 10, actor="test")


def _bookings(session) -> list[ProductionLog]:
    return list(session.scalars(select(ProductionLog).order_by(ProductionLog.id)))


def test_a_booking_somebody_typed_names_them_and_names_the_same_account_as_its_audit_row(
        session, released_order):
    execution.report(session, order_code="WO-1", seq=10, good=3, actor="SCOTT")
    session.flush()

    booking = _bookings(session)[-1]
    audit_row = session.scalars(
        select(AuditLog).where(AuditLog.action == "production.reported")).one()

    assert booking.booked_by == "SCOTT"
    assert booking.booked_by == audit_row.actor


def test_a_booking_an_agent_made_names_the_person_it_acted_for(session, released_order):
    """Principle 3: an agent token earns no role of its own standing, and
    what it does is done for somebody. The booking names the somebody, the
    way the audit row already did."""
    class _Actor(str):
        on_behalf_of = "SCOTT"

    execution.report(session, order_code="WO-1", seq=10, good=2, actor=_Actor("AGENT"))
    session.flush()

    assert _bookings(session)[-1].booked_by == "SCOTT"


def test_a_counter_delta_names_nobody_rather_than_naming_the_system(session, released_order):
    """A machine has no actor. `"system"` in this column would turn *nobody
    typed this* into an answer, and the OEE screen would read it as one."""
    execution.report(session, equipment_code="MIX01", good=5,
                     source=ProductionSource.OPC, actor="opc-agent")
    session.flush()

    assert _bookings(session)[-1].booked_by is None


def test_a_count_another_system_handed_over_names_nobody_and_keeps_the_system(
        session, released_order):
    """`source_system` is where "the incumbent said so" belongs, and it is
    already there. Whoever this MES's inbound contract runs as is a fact
    about this MES, not about who made the units."""
    execution.report(session, equipment_code="MIX01", good=7,
                     source=ProductionSource.EXTERNAL, source_system="replay:incumbent-mes",
                     actor="inbound")
    session.flush()

    booking = _bookings(session)[-1]
    assert booking.booked_by is None
    assert booking.source_system == "replay:incumbent-mes"


def test_units_a_machine_counted_with_no_order_open_name_nobody_either(session):
    execution.report(session, equipment_code="MIX01", good=3, source=ProductionSource.OPC)
    session.flush()

    assert _bookings(session)[-1].booked_by is None


# -------------------------------------------------------- where a person works


def _machine(session, code: str) -> Equipment:
    return session.scalars(select(Equipment).where(Equipment.code == code)).one()


def test_a_person_the_plant_has_not_placed_reads_null_and_is_not_put_somewhere_plausible(
        session, admin):
    reply = admin.get("/masterdata/personnel?q=SCOTT")

    assert reply.status_code == 200, reply.text
    assert reply.json()[0]["home_equipment"] is None


def test_a_person_can_be_put_at_a_station_and_taken_off_it_again(session, admin):
    put = admin.put("/masterdata/personnel/SCOTT/home-equipment", json={"equipment": "PACK01"})
    assert put.status_code == 200, put.text
    assert put.json()["home_equipment"] == "PACK01"

    cleared = admin.put("/masterdata/personnel/SCOTT/home-equipment", json={"equipment": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["home_equipment"] is None


def test_clearing_where_somebody_works_is_an_audited_edit_and_not_a_silence(session, admin):
    """A plant that has stopped knowing where somebody works should be able
    to say so, and the person should be able to see that somebody said it."""
    admin.put("/masterdata/personnel/SCOTT/home-equipment", json={"equipment": "PACK01"})
    admin.put("/masterdata/personnel/SCOTT/home-equipment", json={"equipment": None})

    rows = list(session.scalars(
        select(AuditLog).where(AuditLog.action == "person.home_equipment_set")
        .order_by(AuditLog.id)))
    assert [(r.before["home_equipment"], r.after["home_equipment"]) for r in rows] == [
        (None, "PACK01"), ("PACK01", None)]


def test_an_operator_cannot_say_where_anybody_works(session, client):
    """It grants nothing, and it is still master data: `users.manage`."""
    reply = client.put("/masterdata/personnel/SCOTT/home-equipment",
                       json={"equipment": "PACK01"})
    assert reply.status_code == 403, reply.text


def test_a_home_station_groups_a_person_under_the_line_above_it(session):
    """A plant is analysed by line. Somebody put at `PACK01` is grouped
    under `LINE1`, because the question is which line asks what, and a
    station is how a plant says it."""
    masterdata.set_home_equipment(session, code="SCOTT", equipment="PACK01", actor="ADMIN")
    session.flush()

    assert masterdata.home_work_centers(session)["SCOTT"] == "LINE1"
    assert masterdata.home_work_centers(session)["ADMIN"] is None


def test_a_home_node_that_hangs_outside_any_line_is_unattributed_and_not_invented(session):
    """A station under the area rather than under a line has no work centre
    above it. That is *not attributed*, the same as having no home at all -
    a plant with an odd tree is not a plant whose people belong to LINE1."""
    area = _machine(session, "PKG")
    loose = Equipment(code="LOOSE1", name="Loose machine",
                      level=_machine(session, "MIX01").level, parent=area)
    session.add(loose)
    session.flush()
    masterdata.set_home_equipment(session, code="SCOTT", equipment="LOOSE1", actor="ADMIN")
    session.flush()

    assert masterdata.home_work_centers(session)["SCOTT"] is None


# ---------------------------------------- what the trace and the rollups now say


def test_the_conversation_list_says_which_screen_it_was_opened_from(session):
    _turn(session, session="c1", screen="/dashboard/production", ts=utcnow() - timedelta(minutes=5))
    _turn(session, session="c1", screen="/dashboard/quality", ts=utcnow() - timedelta(minutes=4))

    row = ai_trace.conversations(session)["conversations"][0]

    assert row["screen"] == "/dashboard/production"
    assert row["screens"] == 2
    assert row["turns_without_a_screen"] == 0


def test_a_conversation_from_before_the_column_existed_says_so_rather_than_naming_a_screen(session):
    """Nothing is backfilled. A row with no screen is counted, so "four
    questions came from Quality" cannot be read off a window where half the
    turns record nowhere at all."""
    _turn(session, session="c2")
    _turn(session, session="c2", screen="/dashboard/ai")

    row = ai_trace.conversations(session)["conversations"][0]

    assert row["screen"] == "/dashboard/ai"
    assert row["turns_without_a_screen"] == 1


def test_a_screen_filter_now_matches_the_turns_recorded_against_that_screen(session):
    """It used to match nothing, because there was no column; returning
    everything would have been reporting an unfiltered answer as a filtered
    one. Now it filters, and still says how many turns record no screen."""
    _turn(session, asked="why is this held", screen="/dashboard/production")
    _turn(session, asked="how do I book scrap", screen="/dashboard/quality")
    _turn(session, asked="what is this alarm")

    answer = trace_analysis.trace_rollup(session, hours=24, screen="/dashboard/production")

    assert [g["asked"] for g in answer["groups"]] == ["why is this held"]
    assert answer["turns_without_a_screen"] == 1
    assert "not backfilled" in answer["note"]


def test_a_rollup_by_workcenter_counts_the_people_nobody_placed(session):
    """The unattributed count is the point: a breakdown with one line on it
    and no number for everybody else reads as the whole plant."""
    masterdata.set_home_equipment(session, code="SCOTT", equipment="PACK01", actor="ADMIN")
    session.flush()
    _turn(session, asked="why is this held", user="SCOTT")
    _turn(session, asked="why is this held", user="ADMIN")

    answer = trace_analysis.trace_rollup(session, hours=24, by="workcenter")

    group = answer["groups"][0]
    assert group["by"] == {"LINE1": 1}
    assert group["unattributed"] == 1
    assert answer["unattributed_turns"] == 1
    assert "LINE1" not in answer["note"]


def test_a_rollup_by_shift_reads_the_stamp_the_turn_was_written_with(session, zone):
    """The shift the plant was running *then*, not the shift today's roster
    would put that instant in. A roster rewritten in March must not move a
    question asked in January (decision 0028)."""
    tz = zone("America/Chicago")
    _day_shift(session)
    _turn(session, asked="why is this held", ts=_at(tz, date(2026, 9, 10), 14, 0))

    # The pattern is withdrawn; the stamp on the row stays what it was.
    for pattern in calendar.patterns(session):
        pattern.active = False
    session.flush()
    session.info.pop("fsmes.calendar.patterns", None)

    answer = trace_analysis.trace_rollup(
        session, hours=24 * 400, by="shift")

    assert answer["groups"][0]["by"] == {"DAY": 1}


def test_the_graph_draws_the_screen_a_question_came_from_and_a_hole_where_none_was_recorded(session):
    _turn(session, asked="why is this held", screen="/dashboard/production")
    _turn(session, asked="what is this alarm")

    graph = trace_analysis.trace_graph(session, hours=24)

    kinds = {edge["kind"] for edge in graph["edges"]}
    assert "asked_from" in kinds
    assert graph["node_kinds"]["screen"] == 1
    assert any(n["id"] == "unattributed:screen" and n["degree"] > 0 for n in graph["nodes"])
    # An edge kind with a source is no longer declared empty.
    assert "asked_from" not in graph["empty_edge_kinds"]
    assert "visited" in graph["empty_edge_kinds"]


def test_a_node_kind_this_plant_has_none_of_says_why_it_might_be_empty(session):
    _turn(session, asked="why is this held")

    graph = trace_analysis.trace_graph(session, hours=24)

    assert "not backfilled" in graph["empty_node_kinds"]["screen"]
    assert "personnel.home_equipment_id" in graph["empty_node_kinds"]["workcenter"]


def test_the_five_columns_are_nullable_so_a_plant_that_upgrades_loses_nothing(session):
    """Additive and nullable, every one. A migration that made any of these
    NOT NULL would have to invent a value for every row already there."""
    nullable = {
        (AiTurn, "screen"), (AiTurn, "shift_code"), (AiTurn, "shift_day"),
        (AuditLog, "shift_code"), (AuditLog, "shift_day"),
        (ProductionLog, "booked_by"), (Person, "home_equipment_id"),
    }
    for model, column in nullable:
        assert model.__table__.columns[column].nullable, f"{model.__tablename__}.{column}"
