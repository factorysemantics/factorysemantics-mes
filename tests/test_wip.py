"""WIP at each stage.

The question an ERP asks first, and the one this MES could not answer: how
much is sitting at each step of a route, and in whose cost center. It needs
no new columns — the counters are already booked per operation, and a routing
is a sequence, so what arrived at a step is what the step before it finished
good.
"""

import pytest

from fsmes.domain import Equipment, EquipmentLevel, ProductionSource
from fsmes.services import execution, masterdata, workorders


@pytest.fixture()
def route(session):
    """Three stations on one line, so WIP has somewhere to sit between."""
    line = Equipment(code="L9", name="Line 9", level=EquipmentLevel.WORK_CENTER,
                     cost_center="CC-LINE")
    session.add(line)
    session.flush()
    cell = Equipment(code="CELL-9", name="Cell 9", level=EquipmentLevel.AREA,
                     parent_id=line.id, cost_center="CC-CELL")
    session.add(cell)
    session.flush()
    for code, parent in (("S1", line.id), ("S2", cell.id), ("S3", line.id)):
        session.add(Equipment(code=code, name=code, level=EquipmentLevel.WORK_UNIT,
                              parent_id=parent, ideal_cycle_seconds=2.0))
    session.flush()

    masterdata.create_material(session, code="FG-W", name="W", unit="ea", actor="t")
    masterdata.create_routing(
        session, code="RT-W", name="W", material_code="FG-W", actor="t",
        operations=[{"seq": 10, "name": "Cut", "equipment": "S1"},
                    {"seq": 20, "name": "Mill", "equipment": "S2"},
                    {"seq": 30, "name": "Pack", "equipment": "S3"}])
    workorders.create(session, code="WO-W", material_code="FG-W",
                      quantity=100, actor="t")
    workorders.release(session, "WO-W", actor="t")
    session.flush()
    return "WO-W"


def book(session, order, seq, good, scrap=0.0):
    workorders.start_operation(session, order, seq, actor="t")
    execution.report(session, order_code=order, seq=seq, good=good, scrap=scrap,
                     source=ProductionSource.MANUAL, actor="t")
    session.flush()


# --------------------------------------------------------------- the shape

def test_nothing_booked_means_everything_waits_at_the_first_stage(session, route):
    out = execution.wip(session, route)
    stages = out["stages"]

    assert [s["wip_qty"] for s in stages] == [100, 0, 0]
    assert out["wip_total"] == 100
    assert out["finished"] == 0


def test_units_move_down_the_route_as_stations_finish_them(session, route):
    book(session, route, 10, good=60)
    book(session, route, 20, good=25)

    stages = execution.wip(session, route)["stages"]
    # 40 still to cut; 35 cut and waiting to mill; 25 milled and waiting to pack.
    assert [s["wip_qty"] for s in stages] == [40, 35, 25]
    assert [s["input_qty"] for s in stages] == [100, 60, 25]


def test_scrap_leaves_the_route_rather_than_moving_on(session, route):
    book(session, route, 10, good=50, scrap=10)

    stages = execution.wip(session, route)["stages"]
    assert stages[0]["wip_qty"] == 40      # 100 - 50 good - 10 scrapped
    assert stages[1]["input_qty"] == 50    # scrap does not arrive downstream


def test_a_finished_order_holds_no_wip(session, route):
    book(session, route, 10, good=100)
    book(session, route, 20, good=100)
    book(session, route, 30, good=100)

    out = execution.wip(session, route)
    assert out["wip_total"] == 0
    assert out["finished"] == 100
    assert all(s["wip_qty"] == 0 for s in out["stages"])


def test_the_total_is_what_went_in_less_what_came_out_or_was_scrapped(session, route):
    """Two ways of computing one number is two ways of being wrong, so the
    per-stage sum and the order-level identity are held to each other."""
    book(session, route, 10, good=80, scrap=5)
    book(session, route, 20, good=60, scrap=3)
    book(session, route, 30, good=50, scrap=2)

    out = execution.wip(session, route)
    by_stage = sum(s["wip_qty"] for s in out["stages"])

    assert by_stage == pytest.approx(out["wip_total"])
    assert out["wip_total"] == pytest.approx(100 - 50 - 10)


# --------------------------------------------------------- cost centers

def test_each_stage_says_whose_account_it_sits_in(session, route):
    """What makes the number an ERP can post against rather than a
    curiosity. S2 is in a cell that is accounted for separately."""
    stages = {s["seq"]: s["cost_center"] for s in execution.wip(session, route)["stages"]}
    assert stages == {10: "CC-LINE", 20: "CC-CELL", 30: "CC-LINE"}


# ------------------------------------------------------------- honesty

def test_counters_that_disagree_with_the_route_are_reported_not_clamped(session, route):
    """A step cannot finish more than reached it. A negative number means a
    miscounted PLC, an unmodelled rework loop, or a booking against the
    wrong operation — exactly the thing worth looking at, so it is not
    rounded up to zero."""
    book(session, route, 10, good=10)
    book(session, route, 20, good=40)          # more than ever arrived

    out = execution.wip(session, route)
    assert out["consistent"] is False
    assert out["stages"][1]["wip_qty"] == -30


def test_a_healthy_order_says_so(session, route):
    book(session, route, 10, good=40)
    assert execution.wip(session, route)["consistent"] is True


# ------------------------------------------------------------ the endpoint

def test_the_endpoint_serves_it(client, session, route):
    book(session, route, 10, good=30)

    body = client.get(f"/workorders/{route}/wip").json()
    assert body["order"] == "WO-W"
    assert body["stages"][0]["wip_qty"] == 70
    assert body["stages"][0]["cost_center"] == "CC-LINE"


def test_an_unknown_order_is_a_404(client, route):
    assert client.get("/workorders/NOPE/wip").status_code == 404
