"""Scheduling against a real calendar, and per-unit traceability.

The two things that separate a plan from a wish and a recall from a shutdown.
"""

from datetime import date, datetime, time, timedelta

import pytest

from fsmes.services import Invalid, calendar, scheduling, serialization

# ------------------------------------------------------------- the calendar

@pytest.fixture()
def two_shifts(session):
    calendar.create_pattern(session, code="DAY", name="Day",
                            starts=time(6, 0), ends=time(18, 0), days="1111111")
    calendar.create_pattern(session, code="NIGHT", name="Night",
                            starts=time(18, 0), ends=time(6, 0), days="1111111")
    session.flush()


@pytest.fixture()
def weekdays_only(session):
    calendar.create_pattern(session, code="DAY", name="Day",
                            starts=time(8, 0), ends=time(16, 0), days="1111100")
    session.flush()


def test_a_plant_with_no_calendar_is_assumed_to_run(session):
    """Refusing to schedule anything would be worse for a plant mid-setup than
    assuming continuous running - but it is stated, not silent."""
    assert calendar.is_working(session, datetime(2026, 1, 3, 3, 0)) is True
    assert calendar.describe(session)["note"] is not None


def test_the_plant_is_shut_outside_its_shifts(session, weekdays_only):
    assert calendar.is_working(session, datetime(2026, 1, 5, 10, 0)) is True   # Mon
    assert calendar.is_working(session, datetime(2026, 1, 5, 20, 0)) is False  # evening
    assert calendar.is_working(session, datetime(2026, 1, 10, 10, 0)) is False  # Sat


def test_a_night_shift_belongs_to_the_day_it_started(session, two_shifts):
    """The small hours of Saturday are Friday's shift. That is how a plant
    counts it and how the roster is written."""
    friday_night = datetime(2026, 1, 2, 23, 0)
    saturday_small_hours = datetime(2026, 1, 3, 2, 0)
    assert calendar.is_working(session, friday_night) is True
    assert calendar.is_working(session, saturday_small_hours) is True


def test_a_night_shift_that_only_runs_weekdays_stops_on_saturday_morning(session):
    calendar.create_pattern(session, code="N", name="Nights",
                            starts=time(22, 0), ends=time(6, 0), days="1111100")
    session.flush()
    # Saturday 02:00 belongs to Friday's shift, which runs.
    assert calendar.is_working(session, datetime(2026, 1, 3, 2, 0)) is True
    # Sunday 02:00 belongs to Saturday, which does not.
    assert calendar.is_working(session, datetime(2026, 1, 4, 2, 0)) is False


def test_a_shutdown_day_removes_capacity(session, weekdays_only):
    calendar.add_exception(session, day=date(2026, 1, 5), kind="non_working",
                           reason="Plant shutdown")
    session.flush()
    assert calendar.is_working(session, datetime(2026, 1, 5, 10, 0)) is False


def test_an_overtime_day_adds_capacity(session, weekdays_only):
    """Both directions matter. A planner counting on Saturday needs Saturday."""
    calendar.add_exception(session, day=date(2026, 1, 10), kind="working",
                           reason="Overtime Saturday")
    session.flush()
    assert calendar.is_working(session, datetime(2026, 1, 10, 10, 0)) is True


def test_working_time_skips_the_hours_the_plant_is_dark(session, weekdays_only):
    """The whole reason the calendar exists: a six-hour job started at 14:00
    does not finish at 20:00 on a plant that stops at 16:00."""
    start = datetime(2026, 1, 5, 14, 0)          # Monday afternoon
    finish = calendar.add_working(session, start, minutes=6 * 60)
    assert finish.date() == date(2026, 1, 6)     # spills into Tuesday
    assert finish.hour == 12                     # 2h Monday + 4h Tuesday


def test_a_bad_day_mask_is_refused(session):
    with pytest.raises(Invalid, match="seven characters"):
        calendar.create_pattern(session, code="X", name="X", starts=time(8),
                                ends=time(16), days="111")


# ------------------------------------------------------------- scheduling

def _order(session, code="WO-S1", qty=100):
    from fsmes.services import workorders
    workorders.create(session, code=code, material_code="FG-COLA", quantity=qty,
                      actor="test")
    workorders.release(session, code, actor="test")
    session.flush()
    return code


def test_an_order_is_scheduled_in_route_sequence(session, two_shifts):
    plan = scheduling.plan_order(session, _order(session))
    seqs = [op["seq"] for op in plan["operations"]]
    assert seqs == sorted(seqs)
    # A routing is a sequence, not a set: nothing starts before the step
    # before it finishes.
    for earlier, later in zip(plan["operations"], plan["operations"][1:], strict=False):
        assert later["planned_start"] >= earlier["planned_end"]


def test_maintenance_the_machine_owes_is_placed_before_production(session, two_shifts):
    """A plan that schedules through a service is a prediction that something
    will go wrong."""
    from fsmes.services import maintenance
    maintenance.create_plan(session, code="PM-S", name="Seals",
                            equipment_code="MIX01", trigger="calendar_days",
                            interval=1.0, expected_minutes=90.0)
    maintenance.raise_due(session)
    session.flush()

    scheduling.plan_order(session, _order(session, "WO-S2"))
    board = scheduling.board(session, equipment_code="MIX01", hours=72)
    slots = board["machines"][0]["slots"]
    assert slots[0]["kind"] == "maintenance"
    assert board["maintenance_minutes"] == 90.0


def test_replanning_replaces_slots_rather_than_adding_to_them(session, two_shifts):
    """Otherwise a planner running it twice fills the board with ghosts."""
    code = _order(session, "WO-S3")
    first = scheduling.plan_order(session, code)
    second = scheduling.plan_order(session, code)
    assert len(first["operations"]) == len(second["operations"])


def test_a_promise_says_whether_the_plan_misses_the_date(session, two_shifts):
    """The most useful thing a scheduler produces, and the thing a
    spreadsheet never says out loud."""
    from fsmes.services import workorders
    code = "WO-S4"
    workorders.create(session, code=code, material_code="FG-COLA", quantity=100000,
                      due_date=datetime.utcnow() + timedelta(hours=1), actor="test")
    workorders.release(session, code, actor="test")
    session.flush()

    scheduling.plan_order(session, code)
    promise = scheduling.promise(session, code)
    assert promise["scheduled"] is True
    assert promise["late"] is True and promise["late_by_hours"] > 0


def test_an_unscheduled_order_says_so_rather_than_guessing(session):
    assert scheduling.promise(session, _order(session, "WO-S5"))["scheduled"] is False


def test_a_finished_order_cannot_be_scheduled(session):
    from fsmes.services import workorders
    code = _order(session, "WO-S6")
    workorders.cancel(session, code, actor="test")
    session.flush()
    with pytest.raises(Invalid, match="nothing to schedule"):
        scheduling.plan_order(session, code)


# ---------------------------------------------------------- serialisation

def test_a_unit_can_be_packed_into_another(session):
    bottle = serialization.produce(session, material_code="FG-COLA")
    case = serialization.produce(session, material_code="FG-COLA")
    serialization.pack(session, serial=bottle.serial, into=case.serial)
    tree = serialization.contents(session, case.serial)
    assert [c["serial"] for c in tree["contains"]] == [bottle.serial]


def test_packing_cannot_make_a_loop(session):
    """A pallet inside one of its own bottles would make every trace an
    infinite walk."""
    outer = serialization.produce(session, material_code="FG-COLA")
    inner = serialization.produce(session, material_code="FG-COLA")
    serialization.pack(session, serial=inner.serial, into=outer.serial)
    with pytest.raises(Invalid, match="already inside"):
        serialization.pack(session, serial=outer.serial, into=inner.serial)


def test_a_unit_cannot_contain_itself(session):
    unit = serialization.produce(session, material_code="FG-COLA")
    with pytest.raises(Invalid, match="cannot contain itself"):
        serialization.pack(session, serial=unit.serial, into=unit.serial)


def test_holding_a_pallet_holds_everything_on_it(session):
    """Holding a pallet without holding the cases on it holds nothing."""
    pallet = serialization.produce(session, material_code="FG-COLA")
    cases = [serialization.produce(session, material_code="FG-COLA") for _ in range(3)]
    for case in cases:
        serialization.pack(session, serial=case.serial, into=pallet.serial)

    touched = serialization.set_status(session, pallet.serial, "quarantined",
                                       note="suspect cap lot", cascade=True)
    assert len(touched) == 4
    assert all(u.status.value == "quarantined" for u in touched)


def test_a_recall_names_packages_not_ten_thousand_bottles(session):
    """A warehouse holds pallets. A list of bottle serials is not an
    instruction anybody can act on."""
    from fsmes.services import execution, workorders

    workorders.create(session, code="WO-T1", material_code="FG-COLA", quantity=10,
                      actor="test")
    workorders.release(session, "WO-T1", actor="test")
    execution.create_lot(session, code="LOT-SUSPECT", material_code="RAW-SUGAR",
                         quantity=500, actor="test")
    session.flush()
    execution.consume(session, order_code="WO-T1", lot_code="LOT-SUSPECT",
                      quantity=10, actor="test")
    session.flush()

    pallet = serialization.produce(session, material_code="FG-COLA")
    for _ in range(4):
        bottle = serialization.produce(session, material_code="FG-COLA",
                                       order_code="WO-T1")
        serialization.pack(session, serial=bottle.serial, into=pallet.serial)
    session.flush()

    found = serialization.where_used(session, "LOT-SUSPECT")
    assert found["units_affected"] == 4
    assert [p["package"] for p in found["packages_to_hold"]] == [pallet.serial]


def test_tracing_a_pack_finds_what_went_into_it(session):
    from fsmes.services import execution, workorders

    workorders.create(session, code="WO-T2", material_code="FG-COLA", quantity=10,
                      actor="test")
    workorders.release(session, "WO-T2", actor="test")
    execution.create_lot(session, code="LOT-TRACE", material_code="RAW-SUGAR",
                         quantity=100, actor="test")
    session.flush()
    execution.consume(session, order_code="WO-T2", lot_code="LOT-TRACE",
                      quantity=5, actor="test")
    session.flush()

    unit = serialization.produce(session, material_code="FG-COLA", order_code="WO-T2")
    trace = serialization.trace_back(session, unit.serial)
    assert [c["lot"] for c in trace["components"]] == ["LOT-TRACE"]


def test_an_unknown_status_is_refused(session):
    unit = serialization.produce(session, material_code="FG-COLA")
    with pytest.raises(Invalid, match="unknown status"):
        serialization.set_status(session, unit.serial, "vaporised")
