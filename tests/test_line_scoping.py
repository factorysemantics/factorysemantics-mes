"""Orders that know their line, and a schedule built on real cycle times.

Both of these were silent: the scheduler asked equipment for an attribute it
does not have and planned every slot at the fallback, and an order carried no
line at all, so a plant with six of them could only ever show a supervisor
all the orders in the database.
"""

import pytest

from fsmes.domain import Equipment, EquipmentLevel, Routing, RoutingOperation
from fsmes.services import masterdata, scheduling, workorders


@pytest.fixture()
def two_lines(session):
    """Two lines, one with its machine down a cell, so the line a work order
    belongs to has to be walked for rather than looked up."""
    made = {}
    for line_code, cell_code, machine_code, cycle in (
        ("LINE-A", "CELL-A1", "M-A1", 12.0),
        ("LINE-B", None, "M-B1", 30.0),
    ):
        line = Equipment(code=line_code, name=line_code,
                         level=EquipmentLevel.WORK_CENTER, cost_center="CC-" + line_code)
        session.add(line)
        session.flush()
        parent = line
        if cell_code:
            cell = Equipment(code=cell_code, name=cell_code,
                             level=EquipmentLevel.AREA, parent_id=line.id)
            session.add(cell)
            session.flush()
            parent = cell
        machine = Equipment(code=machine_code, name=machine_code,
                            level=EquipmentLevel.WORK_UNIT, parent_id=parent.id,
                            ideal_cycle_seconds=cycle)
        session.add(machine)
        session.flush()
        made[line_code] = (line, machine)

    for line_code, (_line, machine) in made.items():
        material = masterdata.create_material(
            session, code=f"FG-{line_code}", name=line_code, unit="ea", actor="test")
        routing = Routing(code=f"RT-{line_code}", name=line_code, material_id=material.id)
        session.add(routing)
        session.flush()
        session.add(RoutingOperation(routing_id=routing.id, seq=10, name="Run",
                                     equipment_id=machine.id))
        session.flush()
    return made


# ------------------------------------------------------------ the schedule

def test_a_slot_is_sized_by_the_machine_that_runs_it(session, two_lines):
    """The scheduler read `cycle_seconds`; the column is
    `ideal_cycle_seconds`, so getattr returned None and every slot in every
    schedule was the three-second fallback whatever the machine. A
    finite-capacity scheduler built on a constant is a calendar."""
    _, machine = two_lines["LINE-B"]
    workorders.create(session, code="WO-SCHED", material_code="FG-LINE-B",
                              quantity=10, actor="test")
    workorders.release(session, "WO-SCHED", actor="test")
    session.flush()

    plan = scheduling.plan_order(session, "WO-SCHED", actor="test")
    # 10 units x 30 s = 300 s = 5 minutes, not 10 x 3 s = 0.5 minutes.
    assert plan["operations"][0]["minutes"] == pytest.approx(5.0)
    assert machine.ideal_cycle_seconds == 30.0


def test_two_machines_with_different_rates_plan_differently(session, two_lines):
    """The proof the number comes from the machine at all: identical orders
    on machines rated 12 s and 30 s must not plan the same."""
    for code, material in (("WO-A", "FG-LINE-A"), ("WO-B", "FG-LINE-B")):
        workorders.create(session, code=code, material_code=material,
                          quantity=10, actor="test")
        workorders.release(session, code, actor="test")
    session.flush()

    a = scheduling.plan_order(session, "WO-A", actor="test")["operations"][0]["minutes"]
    b = scheduling.plan_order(session, "WO-B", actor="test")["operations"][0]["minutes"]
    assert a == pytest.approx(2.0)      # 10 x 12 s
    assert b == pytest.approx(5.0)      # 10 x 30 s
    assert a != b, "both plans used the fallback, so neither used the machine"


def test_an_uncommissioned_machine_still_plans(session, two_lines):
    """The fallback has to stay: a plant that has not commissioned its
    machines must still be able to produce a plan."""
    _, machine = two_lines["LINE-A"]
    machine.ideal_cycle_seconds = None
    session.flush()

    workorders.create(session, code="WO-RAW", material_code="FG-LINE-A",
                      quantity=20, actor="test")
    workorders.release(session, "WO-RAW", actor="test")
    session.flush()

    plan = scheduling.plan_order(session, "WO-RAW", actor="test")
    assert plan["operations"][0]["minutes"] == pytest.approx(1.0)   # 20 x 3 s fallback


# --------------------------------------------------------------- the line

def test_an_order_takes_the_line_its_route_starts_on(session, two_lines):
    line, _ = two_lines["LINE-A"]
    order = workorders.create(session, code="WO-LINE-A", material_code="FG-LINE-A",
                              quantity=5, actor="test")
    assert order.work_center_id == line.id


def test_the_line_is_found_through_a_cell(session, two_lines):
    """LINE-A's machine hangs off a cell, so this is walked for, not looked
    up — the upward counterpart of work_units_under."""
    _, machine = two_lines["LINE-A"]
    centre = masterdata.work_center_of(session, machine)
    assert centre is not None and centre.code == "LINE-A"


def test_a_machine_outside_any_line_reports_none(session):
    orphan = Equipment(code="LOOSE", name="Loose", level=EquipmentLevel.WORK_UNIT)
    session.add(orphan)
    session.flush()
    assert masterdata.work_center_of(session, orphan) is None


def test_orders_can_be_asked_for_one_line_at_a_time(admin, session, two_lines):
    """A plant with six lines cannot show a supervisor "the orders" and mean
    all of them."""
    workorders.create(session, code="WO-ON-A", material_code="FG-LINE-A",
                      quantity=5, actor="test")
    workorders.create(session, code="WO-ON-B", material_code="FG-LINE-B",
                      quantity=5, actor="test")
    session.flush()

    on_a = {o["code"] for o in admin.get("/workorders?line=LINE-A").json()["items"]}
    on_b = {o["code"] for o in admin.get("/workorders?line=LINE-B").json()["items"]}

    assert "WO-ON-A" in on_a and "WO-ON-B" not in on_a
    assert "WO-ON-B" in on_b and "WO-ON-A" not in on_b


def test_asking_for_a_line_that_does_not_exist_says_so(admin, two_lines):
    assert admin.get("/workorders?line=NOPE").status_code == 404
