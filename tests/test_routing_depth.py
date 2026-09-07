"""Routings that can be costed, and operations that target a cell.

A routing operation used to say only "step 10, Mill, on MILL01". It could not
say how long the step takes, so nothing downstream could cost it and the
scheduler had to guess from the machine; and it could not say "any machine in
this cell", which is how a plant with interchangeable machines plans.
"""

import pytest

from fsmes.domain import Equipment, EquipmentLevel
from fsmes.services import Invalid, masterdata, scheduling, workorders


@pytest.fixture()
def cell(session):
    """A line with a cell holding two interchangeable machines."""
    line = Equipment(code="L1", name="Line 1", level=EquipmentLevel.WORK_CENTER,
                     cost_center="CC-1")
    session.add(line)
    session.flush()
    group = Equipment(code="CELL-1", name="Cell 1", level=EquipmentLevel.AREA,
                      parent_id=line.id)
    session.add(group)
    session.flush()
    session.add_all([
        Equipment(code="MILL-B", name="Mill B", level=EquipmentLevel.WORK_UNIT,
                  parent_id=group.id, ideal_cycle_seconds=6.0),
        Equipment(code="MILL-A", name="Mill A", level=EquipmentLevel.WORK_UNIT,
                  parent_id=group.id, ideal_cycle_seconds=6.0),
    ])
    session.flush()
    masterdata.create_material(session, code="FG-X", name="X", unit="ea", actor="t")
    return group


# ------------------------------------------------------------- durations

def test_a_routing_carries_the_three_times_a_plant_is_billed_for(session, cell):
    routing = masterdata.create_routing(
        session, code="RT-X", name="X", material_code="FG-X", actor="t",
        operations=[{"seq": 10, "name": "Mill", "equipment": "MILL-A",
                     "setup_seconds": 600, "run_seconds_per_unit": 12,
                     "labour_seconds_per_unit": 4}])
    [op] = routing.operations
    assert (op.setup_seconds, op.run_seconds_per_unit, op.labour_seconds_per_unit) \
        == (600, 12, 4)


def test_a_time_studied_route_beats_the_machines_rated_cycle(session, cell):
    """Setup happens once and run time scales — the arithmetic every planner
    already does. The machine is rated 6 s; the route says 12 s plus ten
    minutes of setup, and the route wins."""
    masterdata.create_routing(
        session, code="RT-X", name="X", material_code="FG-X", actor="t",
        operations=[{"seq": 10, "name": "Mill", "equipment": "MILL-A",
                     "setup_seconds": 600, "run_seconds_per_unit": 12}])
    workorders.create(session, code="WO-T", material_code="FG-X", quantity=10, actor="t")
    workorders.release(session, "WO-T", actor="t")
    session.flush()

    plan = scheduling.plan_order(session, "WO-T", actor="t")
    # 600 s setup + 10 x 12 s = 720 s = 12 minutes. The machine would say 1.
    assert plan["operations"][0]["minutes"] == pytest.approx(12.0)


def test_an_untimed_route_still_plans_from_the_machine(session, cell):
    """A plant that has not time-studied its routes must still be able to
    plan; the fallback is reported, not hidden."""
    masterdata.create_routing(
        session, code="RT-X", name="X", material_code="FG-X", actor="t",
        operations=[{"seq": 10, "name": "Mill", "equipment": "MILL-A"}])
    workorders.create(session, code="WO-U", material_code="FG-X", quantity=10, actor="t")
    workorders.release(session, "WO-U", actor="t")
    session.flush()

    plan = scheduling.plan_order(session, "WO-U", actor="t")
    assert plan["operations"][0]["minutes"] == pytest.approx(1.0)   # 10 x 6 s


def test_the_plan_says_where_its_numbers_came_from(session, cell):
    """A plan built on a fallback is a guess, and a planner is entitled to
    know that before promising a date."""
    routing = masterdata.create_routing(
        session, code="RT-X", name="X", material_code="FG-X", actor="t",
        operations=[{"seq": 10, "name": "Mill", "equipment": "MILL-A",
                     "run_seconds_per_unit": 12}])
    timed = routing.operations[0]
    assert scheduling.operation_minutes(timed, 10)[1] == "routing"

    timed.run_seconds_per_unit = None
    assert scheduling.operation_minutes(timed, 10)[1] == "machine"


def test_durations_are_snapshotted_onto_the_order(session, cell):
    """A route re-timed next month must not silently re-plan an order already
    running to it — the same rule the rest of the operation already follows."""
    routing = masterdata.create_routing(
        session, code="RT-X", name="X", material_code="FG-X", actor="t",
        operations=[{"seq": 10, "name": "Mill", "equipment": "MILL-A",
                     "run_seconds_per_unit": 12}])
    order = workorders.create(session, code="WO-S", material_code="FG-X",
                              quantity=5, actor="t")
    routing.operations[0].run_seconds_per_unit = 999
    session.flush()

    assert order.operations[0].run_seconds_per_unit == 12


# ------------------------------------------------------------ work centres

def test_an_operation_can_name_a_cell_instead_of_a_machine(session, cell):
    routing = masterdata.create_routing(
        session, code="RT-C", name="X", material_code="FG-X", actor="t",
        operations=[{"seq": 10, "name": "Mill", "work_center": "CELL-1"}])
    [op] = routing.operations
    assert op.equipment_id is None
    assert op.work_center.code == "CELL-1"


def test_an_order_on_a_cell_lands_on_a_member_machine(session, cell):
    """Deterministic by code, so two identical orders plan identically and a
    test can assert an answer."""
    masterdata.create_routing(
        session, code="RT-C", name="X", material_code="FG-X", actor="t",
        operations=[{"seq": 10, "name": "Mill", "work_center": "CELL-1"}])
    order = workorders.create(session, code="WO-C", material_code="FG-X",
                              quantity=5, actor="t")

    machine = masterdata.get_equipment(session, "MILL-A")
    assert order.operations[0].equipment_id == machine.id, "MILL-A sorts before MILL-B"


def test_an_operation_with_nowhere_to_happen_is_refused(session, cell):
    """Master data that will fail at dispatch, hours later, to somebody who
    did not write it."""
    with pytest.raises(Invalid, match="somewhere to happen"):
        masterdata.create_routing(
            session, code="RT-N", name="X", material_code="FG-X", actor="t",
            operations=[{"seq": 10, "name": "Mill"}])


def test_an_empty_cell_is_refused_when_the_order_is_built(session, cell):
    empty = Equipment(code="CELL-EMPTY", name="Empty", level=EquipmentLevel.AREA)
    session.add(empty)
    session.flush()
    masterdata.create_routing(
        session, code="RT-E", name="X", material_code="FG-X", actor="t",
        operations=[{"seq": 10, "name": "Mill", "work_center": "CELL-EMPTY"}])

    with pytest.raises(Invalid, match="no machines"):
        workorders.create(session, code="WO-E", material_code="FG-X",
                          quantity=1, actor="t")


def test_the_mes_still_holds_no_money(session):
    """Durations and cost centers, never rates. The ERP owns valuation."""
    from fsmes.domain import RoutingOperation

    columns = set(RoutingOperation.__table__.columns.keys())
    for forbidden in ("cost", "rate", "price", "currency", "amount"):
        assert not any(forbidden in c for c in columns), (
            f"a monetary-looking column {forbidden!r} appeared on RoutingOperation")


def test_an_order_routed_to_a_cell_still_knows_its_line(session, cell):
    """The line is read from the order operation, not the routing step: a
    step naming a cell has no machine, and asking the routing would hand
    session.get a null id and answer None."""
    masterdata.create_routing(
        session, code="RT-L", name="X", material_code="FG-X", actor="t",
        operations=[{"seq": 10, "name": "Mill", "work_center": "CELL-1"}])
    order = workorders.create(session, code="WO-L", material_code="FG-X",
                              quantity=5, actor="t")

    line = masterdata.get_equipment(session, "L1")
    assert order.work_center_id == line.id
