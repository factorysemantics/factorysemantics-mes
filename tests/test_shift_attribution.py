"""Which shift a minute belongs to, and what a per-shift report may claim.

A plant is run and measured by shift. Until now nothing in this MES could say
which shift a booking fell in: `shifts` was master data the scheduler consulted
and no row carried the answer. These tests pin the three rules that decide it
(decision 0028), and the two things a per-shift number must never do - count
time the shift has not lived through yet, and give one second to two shifts.

WHICH CLOCK. The suite keeps UTC so a stored instant and the plant's reading of
it are the same number. The tests here are about the boundary, so each one sets
the zone it is about and says so.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from fsmes.config import get_settings
from fsmes.db import utcnow
from fsmes.domain import (
    Equipment,
    EquipmentState,
    EquipmentStateName,
    NonConformance,
    ProductionLog,
    QualityCheck,
)
from fsmes.services import Invalid, analysis, calendar, equipment, execution, quality, workorders


@pytest.fixture()
def zone(monkeypatch):
    """Put the plant in Chicago for the length of one test."""
    def put(name: str) -> ZoneInfo:
        monkeypatch.setenv("MES_PLANT_TIMEZONE", name)
        get_settings.cache_clear()
        return ZoneInfo(name)
    yield put
    get_settings.cache_clear()


def _two_shifts(session) -> None:
    """The pattern Scott is designing for: days on the clock, nights across it."""
    calendar.create_pattern(session, code="DAY", name="Day shift",
                            starts=time(6, 0), ends=time(22, 0), days="1111111")
    calendar.create_pattern(session, code="NIGHT", name="Night shift",
                            starts=time(22, 0), ends=time(6, 0), days="1111111")
    session.flush()


def _at(zone: ZoneInfo, day: date, hour: int, minute: int = 0) -> datetime:
    """A plant wall-clock reading as the naive UTC every table stores."""
    return datetime.combine(day, time(hour, minute), tzinfo=zone).astimezone(UTC).replace(tzinfo=None)


def _machine(session, code: str) -> Equipment:
    return session.scalar(select(Equipment).where(Equipment.code == code))


# ------------------------------------------------------- which shift is it


def test_a_unit_counted_at_the_second_a_shift_begins_belongs_to_the_shift_that_began(session, zone):
    """A shift is half-open. Ten o'clock exactly is the night shift's first
    second, not the day shift's last: a unit that belonged to both would be
    counted twice in any report that added the two shifts up."""
    tz = zone("America/Chicago")
    _two_shifts(session)
    day = date(2026, 9, 10)

    before = calendar.shift_for(session, _at(tz, day, 21, 59))
    at_the_second = calendar.shift_for(session, _at(tz, day, 22, 0))

    assert before.code == "DAY"
    assert at_the_second.code == "NIGHT"
    assert at_the_second.starts_at == _at(tz, day, 22, 0)


def test_a_night_shift_keeps_the_day_it_started_after_midnight_in_chicago(session, zone):
    """Friday night is Friday's shift at two in the morning on Saturday. That
    is how the roster is written and how the plant counts it; filing those
    hours under Saturday moves a third of a shift onto the wrong supervisor."""
    tz = zone("America/Chicago")
    _two_shifts(session)

    small_hours = calendar.shift_for(session, _at(tz, date(2026, 9, 12), 2, 0))

    assert small_hours.code == "NIGHT"
    assert small_hours.day == date(2026, 9, 11)
    assert small_hours.key() == "2026-09-11/NIGHT"


def test_a_night_shift_keeps_the_day_it_started_after_midnight_in_berlin(session, zone):
    """The same plant configuration on the other side of the Atlantic. The
    rule is about the plant's own clock, so it cannot depend on which side of
    Greenwich the plant is."""
    tz = zone("Europe/Berlin")
    _two_shifts(session)

    small_hours = calendar.shift_for(session, _at(tz, date(2026, 9, 12), 2, 0))

    assert small_hours.day == date(2026, 9, 11)
    assert small_hours.starts_at == _at(tz, date(2026, 9, 11), 22, 0)
    assert small_hours.ends_at == _at(tz, date(2026, 9, 12), 6, 0)


def test_the_night_the_clocks_go_forward_is_a_seven_hour_shift(session, zone):
    """Chicago's clocks went forward at two on the morning of 8 March 2026, so
    the shift that started at ten the evening before is seven hours long. The
    plant lived through seven hours and any report that priced it at eight
    would understate that shift's availability by an eighth."""
    zone("America/Chicago")
    _two_shifts(session)

    spring = calendar.resolve_shift(session, "2026-03-07/NIGHT")

    assert spring.nominal_hours == 7.0


def test_an_hour_no_pattern_covers_is_not_attributed_rather_than_filed_somewhere(session, zone):
    """A plant that rosters 06:00-14:00 and nothing else has sixteen hours a
    day belonging to no shift. Pushing them into the nearest one would hide a
    calendar nobody finished; null says so (house rule 2)."""
    tz = zone("America/Chicago")
    calendar.create_pattern(session, code="DAY", name="Day shift",
                            starts=time(6, 0), ends=time(14, 0), days="1111111")
    session.flush()

    assert calendar.shift_for(session, _at(tz, date(2026, 9, 10), 9)).code == "DAY"
    assert calendar.shift_for(session, _at(tz, date(2026, 9, 10), 18)) is None


def test_a_saturday_the_plant_worked_is_attributed_even_though_the_roster_is_dark(session, zone):
    """An overtime day is a day the plant deliberately ran. Leaving its units
    unattributed because the mask says Saturday is off would lose a whole
    day's production out of every per-shift report."""
    tz = zone("America/Chicago")
    calendar.create_pattern(session, code="DAY", name="Day shift",
                            starts=time(6, 0), ends=time(14, 0), days="1111100")
    saturday = date(2026, 9, 12)
    assert calendar.shift_for(session, _at(tz, saturday, 9)) is None

    calendar.add_exception(session, day=saturday, kind="working", reason="catch-up")
    session.flush()

    assert calendar.shift_for(session, _at(tz, saturday, 9)).code == "DAY"


# -------------------------------------------------------- rows carry the shift


def test_every_row_the_floor_writes_carries_the_shift_it_fell_in(session, zone):
    """A booking, a state interval, a measurement and a non-conformance. Each
    one is something that happened on somebody's shift, and a plant reading
    them back has to be able to say whose."""
    zone("UTC")
    _two_shifts(session)
    workorders.create(session, code="WO-SH-1", material_code="FG-COLA", quantity=100)
    workorders.release(session, "WO-SH-1")
    workorders.start_operation(session, "WO-SH-1", 10, actor="test")

    execution.report(session, order_code="WO-SH-1", seq=10, good=5, actor="test")
    equipment.set_state(session, equipment_code="MIX01",
                        state=EquipmentStateName.RUNNING, actor="test")
    quality.record_check(session, material_code="FG-COLA", characteristic="brix",
                         value=10.4, actor="test")
    quality.open_nc(session, description="cap torque low", actor="test")
    session.flush()

    now_shift = calendar.shift_for(session, utcnow())
    for model in (ProductionLog, EquipmentState, QualityCheck, NonConformance):
        row = session.scalars(select(model).order_by(model.id.desc())).first()
        assert row.shift_code == now_shift.code, model.__name__
        assert row.shift_day == now_shift.day, model.__name__


def test_a_count_replayed_from_last_night_lands_on_last_nights_shift(session, zone):
    """Another system's counts arrive with the instant they were counted at.
    Attributing them to the shift that happened to be running when the file
    was read would credit this shift with last night's work."""
    zone("UTC")
    _two_shifts(session)
    last_night = _at(ZoneInfo("UTC"), (utcnow() - timedelta(days=1)).date(), 23, 0)

    row = execution.record_unassigned(session, equipment=_machine(session, "MIX01"),
                                      good=12, ts=last_night)
    session.flush()

    assert row.shift_code == "NIGHT"
    assert row.shift_day == (utcnow() - timedelta(days=1)).date()


def test_a_plant_that_has_not_said_what_its_shifts_are_gets_no_shift_on_its_rows(session):
    """Not an error and not a default. The MES has not been told, so it does
    not say - and the screens report those rows as not attributed."""
    workorders.create(session, code="WO-SH-2", material_code="FG-COLA", quantity=10)
    workorders.release(session, "WO-SH-2")
    workorders.start_operation(session, "WO-SH-2", 10, actor="test")
    execution.report(session, order_code="WO-SH-2", seq=10, good=1, actor="test")
    session.flush()

    row = session.scalars(select(ProductionLog).order_by(ProductionLog.id.desc())).first()
    assert row.shift_code is None
    assert row.shift_day is None
    assert analysis.shifts(session)["shifts_total"] == 0
    assert "no shift patterns" in analysis.shifts(session)["note"]


# --------------------------------------------------------- windowing by shift


def _run_the_night(session, tz: ZoneInfo, day: date) -> None:
    """A machine that ran through a whole night shift and on into the morning.

    Two intervals, one of which crosses the 06:00 boundary: RUNNING from
    22:00 to 02:00, DOWN from 02:00 to 03:00, then RUNNING from 03:00 until
    09:00 the next morning - three hours of which belong to the day shift.
    """
    mixer = _machine(session, "MIX01")
    intervals = [
        EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                       started_at=_at(tz, day, 22), ended_at=_at(tz, day + timedelta(days=1), 2)),
        EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.DOWN, reason="jam",
                       started_at=_at(tz, day + timedelta(days=1), 2),
                       ended_at=_at(tz, day + timedelta(days=1), 3)),
        EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                       started_at=_at(tz, day + timedelta(days=1), 3),
                       ended_at=_at(tz, day + timedelta(days=1), 9)),
    ]
    for interval in intervals:
        # Attributed the way `set_state` attributes one, so the stored answer
        # under test is the product's and not the test's.
        calendar.attribute(session, interval, interval.started_at, mixer.id)
    session.add_all(intervals)
    session.flush()


def test_a_chicago_plant_reports_its_night_shifts_oee_across_midnight(session, zone):
    """The whole point. A 22:00-06:00 shift in America/Chicago is eight hours
    that straddle midnight and sit on a UTC day boundary; asking for it by
    name has to return those eight hours and no others."""
    tz = zone("America/Chicago")
    _two_shifts(session)
    day = date(2026, 9, 10)
    _run_the_night(session, tz, day)

    result = analysis.oee_breakdown(session, line_code="LINE1", shift=f"{day}/NIGHT")

    assert result["window"]["start"] == _at(tz, day, 22)
    assert result["window"]["end"] == _at(tz, day + timedelta(days=1), 6)
    assert result["window"]["hours"] == pytest.approx(8.0)
    assert result["window"]["shift"]["code"] == "NIGHT"
    assert result["window"]["shift"]["day"] == "2026-09-10"
    assert result["window"]["shift"]["in_progress"] is False


def test_a_run_that_crossed_the_boundary_is_split_between_the_two_shifts(session, zone):
    """The machine ran from three in the morning until nine. Three of those
    hours are the night shift's and six are the day shift's, and neither
    report may claim the other's. The interval itself is not split - it is one
    thing the machine did - only the reporting is."""
    tz = zone("America/Chicago")
    _two_shifts(session)
    day = date(2026, 9, 10)
    _run_the_night(session, tz, day)

    night = analysis.oee_breakdown(session, line_code="LINE1", shift=f"{day}/NIGHT")
    morning = analysis.oee_breakdown(session, line_code="LINE1",
                                     shift=f"{day + timedelta(days=1)}/DAY")
    mixer = next(s for s in night["stations"] if s["code"] == "MIX01")
    later = next(s for s in morning["stations"] if s["code"] == "MIX01")

    # 22:00-02:00 and 03:00-06:00 running, 02:00-03:00 down.
    assert mixer["runtime_seconds"] == pytest.approx(7 * 3600)
    assert mixer["downtime_seconds"] == pytest.approx(3600)
    # 06:00-09:00 running, and not a second of it counted twice.
    assert later["runtime_seconds"] == pytest.approx(3 * 3600)
    # The stored row still says which shift the interval *began* in - one
    # record of one thing the machine did, not two half-records.
    crossing = session.scalars(
        select(EquipmentState).where(EquipmentState.started_at == _at(tz, day + timedelta(days=1), 3))
    ).first()
    assert crossing.shift_code == "NIGHT"
    assert crossing.shift_day == day


def test_a_shift_still_running_is_clipped_to_now_not_to_the_hour_it_ends(session, zone):
    """Reporting the whole rostered eight hours of a shift two hours old would
    count six hours the plant has not lived through as time it was not
    running, and call a line that never stopped 25 % available."""
    zone("UTC")
    tz = ZoneInfo("UTC")
    calendar.create_pattern(session, code="ALL", name="Around the clock",
                            starts=time(0, 0), ends=time(0, 0), days="1111111")
    session.flush()
    mixer = _machine(session, "MIX01")
    started = _at(tz, utcnow().date(), 0)
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=started, ended_at=None))
    session.flush()

    result = analysis.oee_breakdown(session, line_code="LINE1", shift="current")

    assert result["window"]["shift"]["in_progress"] is True
    assert result["window"]["end"] <= utcnow()
    assert result["window"]["hours"] < 24.0
    mixer_row = next(s for s in result["stations"] if s["code"] == "MIX01")
    # It ran for every second the shift has so far had.
    assert mixer_row["availability"] == pytest.approx(1.0, abs=0.001)


def test_every_panel_shares_the_shifts_axis(session, zone):
    """The OEE panel, the Gantt, the pareto and the production trend sit above
    each other on one page. One of them drawing a different window is how a
    reader is misled about what they are comparing."""
    tz = zone("America/Chicago")
    _two_shifts(session)
    day = date(2026, 9, 10)
    _run_the_night(session, tz, day)
    key = f"{day}/NIGHT"

    windows = [
        analysis.oee_breakdown(session, line_code="LINE1", shift=key)["window"],
        analysis.state_timeline(session, "LINE1", shift=key)["window"],
        analysis.downtime_pareto(session, line_code="LINE1", shift=key)["window"],
        analysis.production_trend(session, line_code="LINE1", shift=key)["window"],
    ]
    assert len({(w["start"], w["end"]) for w in windows}) == 1
    assert all(w["shift"]["key"] == key for w in windows)


def test_the_previous_shift_is_the_one_that_ended_before_this_one_started(session, zone):
    """"Last shift" is the question a supervisor coming on asks first."""
    tz = zone("America/Chicago")
    _two_shifts(session)
    day = date(2026, 9, 10)
    # Two in the morning: the night shift of the 10th is running.
    moment = _at(tz, day + timedelta(days=1), 2)

    current = calendar.resolve_shift(session, "current", now=moment)
    previous = calendar.resolve_shift(session, "previous", now=moment)

    assert current.key() == f"{day}/NIGHT"
    assert previous.key() == f"{day}/DAY"
    assert previous.ends_at == current.starts_at


def test_a_shift_the_plant_does_not_run_is_refused_with_a_sentence(session, zone):
    """Not an empty window, and never a quiet fall-back to eight hours: a
    screen that said "night shift" over the last eight hours would be worse
    than an error."""
    zone("America/Chicago")
    calendar.create_pattern(session, code="DAY", name="Day shift",
                            starts=time(6, 0), ends=time(14, 0), days="1111100")
    session.flush()

    with pytest.raises(Invalid) as refused:
        calendar.resolve_shift(session, "2026-09-12/DAY")  # a Saturday
    assert "does not run on 2026-09-12" in str(refused.value)

    with pytest.raises(Invalid) as unknown:
        calendar.resolve_shift(session, "2026-09-11/TWILIGHT")
    assert "TWILIGHT" in str(unknown.value) and "DAY" in str(unknown.value)

    with pytest.raises(Invalid) as nonsense:
        calendar.resolve_shift(session, "last tuesday")
    assert "current" in str(nonsense.value)


def test_the_shift_picker_says_which_clock_the_boundaries_are_drawn_on(session, zone):
    """A shift boundary read in the browser's zone on a plant five hours away
    is the bug `fsmes.identity` exists to stop, so the list that offers the
    shifts carries the clock and whether anybody chose it."""
    zone("America/Chicago")
    _two_shifts(session)

    offered = analysis.shifts(session, days=3)

    assert offered["timezone"] == "America/Chicago"
    assert offered["timezone_defaulted"] is False
    # Every list states its total.
    assert offered["shifts_total"] == len(offered["shifts"]) > 0
    assert offered["current"] is not None


# ------------------------------------------------------------------- the API


def test_the_api_refuses_an_unknown_shift_with_four_hundred_and_one_sentence(client):
    """The plant in the test fixtures has no shift patterns at all."""
    answer = client.get("/analysis/oee?shift=current")
    assert answer.status_code == 400
    assert "no shift patterns" in answer.json()["detail"]


def test_the_analysis_screen_offers_a_shift_and_keeps_it_in_the_address_bar(session):
    """#58's rule: a filtered screen can be handed to somebody else."""
    from pathlib import Path

    web = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"
    assert 'id="shift"' in (web / "analysis.html").read_text(encoding="utf-8")
    script = (web / "analysis.js").read_text(encoding="utf-8")
    assert "/analysis/shifts" in script
    assert 'p.set("shift"' in script


# ---------------------------------------------------------------- the upgrade


def test_rows_written_before_the_column_existed_are_backfilled_from_the_pattern(tmp_path):
    """A plant upgrading does not lose its history to a new column.

    The backfill reads the patterns as they stand today, which is an
    assumption and the only one available - there is no history of shift
    patterns in this product - and the migration says so. What it must not do
    is guess: a row no pattern covers stays null.
    """
    import os
    import sqlite3

    from alembic import command

    from fsmes import schema as schema_mod
    from fsmes.config import get_settings as settings_cache

    db = tmp_path / "before.db"
    url = f"sqlite:///{db}"
    command.upgrade(schema_mod.alembic_config(url), "b1f4c73a9e08")

    tz = ZoneInfo("America/Chicago")
    day = date(2026, 9, 10)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO shift_patterns (code, name, starts, ends, days, active) "
                     "VALUES ('NIGHT', 'Night shift', '22:00:00', '06:00:00', '1111111', 1)")
        conn.execute("INSERT INTO shift_patterns (code, name, starts, ends, days, active) "
                     "VALUES ('DAY', 'Day shift', '06:00:00', '14:00:00', '1111111', 1)")
        for at in (_at(tz, day, 23), _at(tz, day + timedelta(days=1), 2),
                   _at(tz, day + timedelta(days=1), 17)):
            conn.execute("INSERT INTO production_logs (good_qty, scrap_qty, source, ts) "
                         "VALUES (10, 0, 'opc', ?)", (at.isoformat(sep=" "),))

    before = os.environ.get("MES_PLANT_TIMEZONE")
    os.environ["MES_PLANT_TIMEZONE"] = "America/Chicago"
    settings_cache.cache_clear()
    try:
        command.upgrade(schema_mod.alembic_config(url), "head")
    finally:
        if before is None:
            os.environ.pop("MES_PLANT_TIMEZONE", None)
        else:
            os.environ["MES_PLANT_TIMEZONE"] = before
        settings_cache.cache_clear()

    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT shift_code, shift_day FROM production_logs ORDER BY id").fetchall()
    # 23:00 and 02:00 are both the night shift that started on the 10th.
    assert rows[0] == ("NIGHT", "2026-09-10")
    assert rows[1] == ("NIGHT", "2026-09-10")
    # Five in the afternoon is between the two shifts this plant rosters, and
    # stays not attributed rather than being pushed into the nearer one.
    assert rows[2] == (None, None)
