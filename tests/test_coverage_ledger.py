"""The coverage ledger: every second of an OEE window, in one disposition.

The rule this file exists to hold: an OEE window whose seconds do not add up
is a bug, not a rounding difference. Everything else here is that rule applied
to a particular way of losing sight of a machine.
"""

from datetime import timedelta

import pytest

from fsmes.config import get_settings
from fsmes.db import utcnow
from fsmes.domain import ConnectionStateName, EquipmentState, EquipmentStateName
from fsmes.services import analysis, connection, coverage, equipment, masterdata


def _account(session, code="MIX01", *, hours=8.0):
    machine = masterdata.get_equipment(session, code)
    end = utcnow()
    return coverage.ledger(session, equipment_code=machine.code, equipment_id=machine.id,
                           start=end - timedelta(hours=hours), end=end)


def _state(session, code, state, *, from_minutes_ago, to_minutes_ago=None, reason=None):
    machine = masterdata.get_equipment(session, code)
    now = utcnow()
    session.add(EquipmentState(
        equipment_id=machine.id, state=state, reason=reason,
        started_at=now - timedelta(minutes=from_minutes_ago),
        ended_at=None if to_minutes_ago is None else now - timedelta(minutes=to_minutes_ago)))
    session.flush()


def _disconnect(session, *, minutes_ago, for_minutes=None, reason="the OPC server did not answer"):
    now = utcnow()
    connection.set_connection(session, equipment_code="MIX01",
                              state=ConnectionStateName.DISCONNECTED,
                              at=now - timedelta(minutes=minutes_ago), reason=reason)
    if for_minutes is not None:
        connection.set_connection(session, equipment_code="MIX01",
                                  state=ConnectionStateName.CONNECTED,
                                  at=now - timedelta(minutes=minutes_ago - for_minutes))
    session.flush()


# --------------------------------------------------------- the seconds add up


def test_the_ledgers_intervals_tile_the_whole_window_and_nothing_else(session):
    """Half an hour of history inside an eight-hour window: the intervals meet
    end to end, the first starts where the window starts and the last ends
    where it ends."""
    _state(session, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=30, to_minutes_ago=10)
    _state(session, "MIX01", EquipmentStateName.DOWN, from_minutes_ago=10, reason="jam")

    account = _account(session)
    assert account.tiles_exactly(), "the seconds must add up to the window"
    assert account.intervals[0].start == account.start
    assert account.intervals[-1].end == account.end
    for earlier, later in zip(account.intervals, account.intervals[1:], strict=False):
        assert earlier.end == later.start, "no gap and no overlap between intervals"


def test_a_window_whose_seconds_do_not_add_up_fails_loudly(session):
    """Not a rounding difference. A ledger missing a minute is a bug, and the
    check that says so is the one every other test in this file leans on."""
    account = _account(session)
    missing_a_minute = coverage.Ledger(
        equipment="MIX01", start=account.start, end=account.end,
        intervals=(coverage.Interval(account.start, account.end - timedelta(minutes=1),
                                     coverage.NOT_OBSERVED, coverage.NO_STATE_RECORDED),))
    assert not missing_a_minute.tiles_exactly()

    overlapping = coverage.Ledger(
        equipment="MIX01", start=account.start, end=account.end,
        intervals=(coverage.Interval(account.start, account.end, coverage.OBSERVED_RUNNING),
                   coverage.Interval(account.start, account.end, coverage.NOT_OBSERVED,
                                     coverage.NO_STATE_RECORDED)))
    assert not overlapping.tiles_exactly()


def test_every_second_of_an_empty_window_is_named_rather_than_left_out(session):
    """A machine the MES has never recorded anything about. The window is not
    empty — it is eight hours of `before_first_sample`, which is a different
    statement from "nothing to report"."""
    account = _account(session)
    assert account.tiles_exactly()
    assert account.seconds_by_cause == {coverage.BEFORE_FIRST_SAMPLE: account.window_seconds}
    assert account.observed_seconds == 0.0


# --------------------------------------------- what the dispositions mean


def test_a_stop_nobody_named_is_reported_as_unlabelled_rather_than_as_a_reason(session):
    _state(session, "MIX01", EquipmentStateName.DOWN, from_minutes_ago=20, to_minutes_ago=10)
    _state(session, "MIX01", EquipmentStateName.DOWN, from_minutes_ago=10, reason="blade change")

    by_disposition = _account(session).seconds_by_disposition
    assert by_disposition[coverage.OBSERVED_STOPPED_UNLABELLED] == pytest.approx(600, abs=2)
    assert by_disposition[coverage.OBSERVED_STOPPED_LABELLED] == pytest.approx(600, abs=2)


def test_a_disconnected_agent_shows_as_not_observed_never_as_stopped(session):
    """The machine was running when the link went. Those ten minutes are not
    run time and they are not downtime — nobody watched them."""
    _state(session, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=40)
    _disconnect(session, minutes_ago=20, for_minutes=10)

    account = _account(session)
    assert account.tiles_exactly()
    assert account.seconds_by_cause[coverage.DISCONNECTED] == pytest.approx(600, abs=30)
    assert account.seconds_by_disposition[coverage.OBSERVED_STOPPED_UNLABELLED] == 0.0
    gap = next(i for i in account.intervals if i.cause == coverage.DISCONNECTED)
    assert "did not answer" in (gap.detail or ""), "the recorded sentence travels with the gap"


def test_a_machine_that_stopped_being_reported_on_is_not_observed_running(session):
    """The agent was killed outright, so it wrote no disconnection on its way
    down (decision 0030 wrote this down as known and not solved). The state
    history simply stops, and the ledger says so rather than letting the last
    state stand — which is what would put invented run time in the numerator."""
    _state(session, "MIX01", EquipmentStateName.RUNNING,
           from_minutes_ago=40, to_minutes_ago=20)

    account = _account(session)
    assert account.tiles_exactly()
    assert account.seconds_by_cause[coverage.AFTER_LAST_SAMPLE] == pytest.approx(20 * 60, abs=5)
    assert account.running_seconds == pytest.approx(20 * 60, abs=5)
    assert account.availability == pytest.approx(1.0), (
        "the twenty minutes nobody watched leave the denominator rather than "
        "counting as time the machine was not running")


def test_a_hole_in_the_middle_that_nothing_explains_is_named_as_one(session):
    _state(session, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=40, to_minutes_ago=30)
    _state(session, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=20)

    account = _account(session)
    assert account.tiles_exactly()
    assert account.seconds_by_cause[coverage.NO_STATE_RECORDED] == pytest.approx(600, abs=5)


def test_an_observed_interval_says_what_resolution_it_was_built_at(session):
    """A machine flipping state every three seconds, watched on a fifteen-second
    grid, loses run time to the grid. The ledger does not fix that — it states
    the cadence beside the interval so the reader can weigh it."""
    _state(session, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=10)
    account = _account(session)
    watched = next(i for i in account.intervals if i.observed)
    assert watched.sample_interval_seconds == pytest.approx(0.5)
    assert account.sample_interval_source and "MES_OPC_PUBLISH_MS" in account.sample_interval_source


def test_every_interval_names_the_rule_that_produced_it(session):
    _state(session, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=30)
    _disconnect(session, minutes_ago=20, for_minutes=10)
    for interval in _account(session).intervals:
        assert interval.rule, "a plant engineer argues with the rule, not only with the number"


# ------------------------------------------------- the two ways in agree


def test_the_tiled_ledger_and_the_plant_wide_totals_are_the_same_account(session):
    """One path lists the intervals, the other sums them in the database.
    They are read by different screens and they must never disagree."""
    _state(session, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=50, to_minutes_ago=40)
    _state(session, "MIX01", EquipmentStateName.DOWN, from_minutes_ago=40,
           to_minutes_ago=30, reason="jam")
    _state(session, "MIX01", EquipmentStateName.IDLE, from_minutes_ago=30, to_minutes_ago=25)
    _disconnect(session, minutes_ago=20, for_minutes=10)

    machine = masterdata.get_equipment(session, "MIX01")
    end = utcnow()
    start = end - timedelta(hours=8)
    listed = coverage.ledger(session, equipment_code=machine.code, equipment_id=machine.id,
                             start=start, end=end)
    summed = coverage.totals_many(session, [machine.id], start, end)[machine.id]

    assert listed.tiles_exactly() and summed.tiles_exactly()
    for name in coverage.DISPOSITIONS:
        assert listed.seconds_by_disposition[name] == pytest.approx(
            summed.seconds_by_disposition[name], abs=1), name
    for cause in coverage.CAUSES:
        assert listed.seconds_by_cause.get(cause, 0.0) == pytest.approx(
            summed.not_observed_by_cause.get(cause, 0.0), abs=1), cause


# ------------------------------------------------------------ what OEE says


def test_coverage_is_stated_against_the_window_that_was_asked_for(session):
    """Twenty-four minutes of history, eight hours asked for. Availability is
    100 % of what was watched and coverage says that was 5 % of the shift.
    Clamping the coverage denominator too would report 100 % of both, which
    is the reading this whole feature exists to stop."""
    _state(session, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=24)

    result = equipment.oee(session, equipment_code="MIX01", hours=8.0)
    assert result["availability"] == pytest.approx(1.0, abs=0.01)
    assert result["coverage"] == pytest.approx(24 / 480, abs=0.005)
    assert result["ledger"]["tiles_exactly"] is True


def test_a_window_with_no_samples_reports_unknown_never_a_hundred_per_cent(session):
    result = equipment.oee(session, equipment_code="MIX01", hours=8.0)
    assert result["availability"] is None and result["oee"] is None
    assert result["coverage"] == 0.0
    assert result["ledger"]["seconds_by_cause"][coverage.BEFORE_FIRST_SAMPLE] > 0


def test_every_oee_answer_carries_its_coverage_and_its_ledger(session):
    _state(session, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=60)
    result = equipment.oee(session, equipment_code="MIX01", hours=8.0)
    assert result["coverage"] is not None
    assert result["ledger"]["seconds_by_disposition"][coverage.OBSERVED_RUNNING] > 0
    assert result["ledger"]["observed_seconds"] + result["ledger"]["not_observed_seconds"] == \
        pytest.approx(result["ledger"]["window_seconds"], abs=1)


def test_the_line_breakdown_carries_coverage_on_every_station_and_on_the_line(session):
    _state(session, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=60)
    breakdown = analysis.oee_breakdown(session, hours=8.0)
    assert breakdown["coverage"] is not None
    for station in breakdown["stations"]:
        assert "coverage" in station and "ledger" in station


# --------------------------------------------------------------- the floor


@pytest.fixture
def floor_of(monkeypatch):
    """Set this plant's pack floor for one test, the way a pack would."""
    def set_it(value: str | None):
        if value is None:
            monkeypatch.delenv("MES_OEE_COVERAGE_FLOOR", raising=False)
        else:
            monkeypatch.setenv("MES_OEE_COVERAGE_FLOOR", value)
        get_settings.cache_clear()
    yield set_it
    get_settings.cache_clear()


def test_without_a_floor_nothing_is_withheld_and_coverage_is_still_printed(session, floor_of):
    floor_of(None)
    _state(session, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=24)

    result = equipment.oee(session, equipment_code="MIX01", hours=8.0)
    assert result["coverage_floor"] is None
    assert result["availability"] == pytest.approx(1.0, abs=0.01), "no silent default floor"
    assert result["coverage"] is not None


def test_below_the_packs_floor_the_figure_is_unknown_and_the_ledger_stays(session, floor_of):
    """Honest but lower. Twenty-four minutes of an eight-hour shift does not
    clear an 80 % floor, so the figure is withheld — and the evidence for
    withholding it is on the same object."""
    floor_of("0.8")
    _state(session, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=24)

    result = equipment.oee(session, equipment_code="MIX01", hours=8.0)
    assert result["availability"] is None and result["oee"] is None
    assert result["coverage"] == pytest.approx(24 / 480, abs=0.005)
    assert "withheld" in result["coverage_note"]
    assert result["ledger"]["seconds_by_cause"][coverage.BEFORE_FIRST_SAMPLE] > 0
    assert result["runtime_seconds"] > 0, "the seconds are evidence and are never withheld"


def test_above_the_packs_floor_the_figure_is_reported(session, floor_of):
    floor_of("0.8")
    _state(session, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=59)

    result = equipment.oee(session, equipment_code="MIX01", hours=1.0)
    assert result["availability"] == pytest.approx(1.0, abs=0.02)
    assert result["coverage_note"] is None


def test_a_floor_withholds_on_the_line_breakdown_too_and_says_how_many(session, floor_of):
    floor_of("0.9")
    _state(session, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=24)

    breakdown = analysis.oee_breakdown(session, hours=8.0)
    row = next(s for s in breakdown["stations"] if s["code"] == "MIX01")
    assert row["availability"] is None and "withheld" in row["coverage_note"]
    assert breakdown["stations_withheld"] >= 1
    assert breakdown["coverage_floor"] == pytest.approx(0.9)


def test_a_window_whose_coverage_is_unknown_does_not_clear_a_floor(session, floor_of):
    """"We cannot say how much of this we saw" does not clear a bar that asks
    how much of it we saw."""
    floor_of("0.5")
    assert coverage.withhold(None, coverage.floor()) is not None


# ------------------------------------------------------------- /metrics


def test_metrics_export_availability_and_the_coverage_it_was_measured_over(session, anon):
    """A Grafana panel must not be able to show one without being able to show
    the other. That is the whole reason both series are here."""
    _state(session, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=400)
    body = anon.get("/metrics").text
    assert 'mes_equipment_coverage{plant="' in body
    assert 'mes_equipment_availability{plant="' in body
    assert "mes_equipment_not_observed_seconds" in body


def test_metrics_leave_out_an_availability_the_mes_cannot_state(session, anon):
    """Absent, not zero. Prometheus already means unknown by no sample; a zero
    would put a machine nobody watched at the top of a worst-performer board."""
    body = anon.get("/metrics").text
    assert "mes_equipment_availability{" not in body
    assert 'mes_equipment_coverage{plant="' in body, "coverage is stated even so"


def test_metrics_withhold_the_availability_a_pack_floor_withholds(session, anon, floor_of):
    floor_of("0.9")
    _state(session, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=24)
    body = anon.get("/metrics").text
    assert "mes_coverage_floor" in body
    assert 'mes_equipment_availability{plant="fsmes",equipment="MIX01"}' not in body


# ------------------------------------------- `fsmes oee explain`


def _explain(tmp_path, monkeypatch, *args, build=None):
    """Run `fsmes oee explain` against a database of its own.

    The CLI reaches for `MES_DATABASE_URL` rather than the suite's session, so
    this builds a small plant on disk and points the command at it — which is
    also the only way to exercise the command the way a plant engineer runs it.
    """
    from typer.testing import CliRunner

    from fsmes.cli import app
    from fsmes.config import get_settings as settings_for
    from fsmes.db import Base, get_engine, get_sessionmaker, session_scope
    from fsmes.seed import seed_demo_plant

    monkeypatch.setenv("MES_DATABASE_URL", f"sqlite:///{tmp_path / 'explain.db'}")
    for cached in (settings_for, get_engine, get_sessionmaker):
        cached.cache_clear()
    Base.metadata.create_all(get_engine())
    with session_scope() as db:
        seed_demo_plant(db)
        if build:
            build(db)
    try:
        return CliRunner().invoke(app, ["oee", "explain", *args])
    finally:
        for cached in (settings_for, get_engine, get_sessionmaker):
            cached.cache_clear()


def test_oee_explain_prints_every_interval_with_the_rule_that_produced_it(tmp_path, monkeypatch):
    def build(db):
        _state(db, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=90, to_minutes_ago=60)
        _state(db, "MIX01", EquipmentStateName.DOWN, from_minutes_ago=60,
               to_minutes_ago=50, reason="blade change")
        _state(db, "MIX01", EquipmentStateName.RUNNING, from_minutes_ago=50)

    result = _explain(tmp_path, monkeypatch, "MIX01", "2h", build=build)
    assert result.exit_code == 0, result.output
    assert "observed_running" in result.output
    assert "observed_stopped_labelled" in result.output
    assert "before_first_sample" in result.output
    assert "rule:" in result.output
    assert "blade change" in result.output
    assert "coverage" in result.output
    assert "availability (run ÷ observed)" in result.output


def test_oee_explain_refuses_a_machine_this_plant_does_not_have(tmp_path, monkeypatch):
    result = _explain(tmp_path, monkeypatch, "NOSUCH", "2h")
    assert result.exit_code == 1
    assert "NOT OK" in result.output


def test_oee_explain_refuses_a_window_it_cannot_read(tmp_path, monkeypatch):
    result = _explain(tmp_path, monkeypatch, "MIX01", "last tuesday")
    assert result.exit_code != 0
