"""OEE for every machine in the plant on one list, worst first.

The supervisor's question is "which machine lost the most this shift", and a
plant of sixty lines answered it one line at a time. `line=*` is the whole
plant: every machine under any line, each row carrying the line it is on,
ranked by OEE with the machines that have no figure after every rated one -
unknown is not zero, so it is not ranked as one.
"""

from datetime import timedelta

import pytest
from sqlalchemy import event, select

from fsmes.db import utcnow
from fsmes.domain import (
    Equipment,
    EquipmentLevel,
    EquipmentState,
    EquipmentStateName,
    ProductionLog,
    ProductionSource,
    WorkOrder,
)
from fsmes.services import analysis, workorders


@pytest.fixture()
def plant(session):
    """The demo line (MIX01, PACK01) and a second line beside it: an extruder
    running far below its rating, and a winder nobody rated."""
    workorders.create(session, code="WO-ALL-1", material_code="FG-COLA", quantity=1000)
    workorders.release(session, "WO-ALL-1")
    second = Equipment(code="LINE2", name="Second line", level=EquipmentLevel.WORK_CENTER)
    session.add(second)
    session.flush()
    session.add_all([
        Equipment(code="EXT01", name="Extruder", level=EquipmentLevel.WORK_UNIT,
                  parent_id=second.id, ideal_cycle_seconds=1.0),
        Equipment(code="WND01", name="Winder", level=EquipmentLevel.WORK_UNIT,
                  parent_id=second.id, ideal_cycle_seconds=None),
    ])
    session.flush()

    for code in ("MIX01", "PACK01", "EXT01", "WND01"):
        _state(session, code, EquipmentStateName.RUNNING, minutes_ago=60, minutes=30)
    _state(session, "MIX01", EquipmentStateName.RUNNING, minutes_ago=30, minutes=30)
    _state(session, "PACK01", EquipmentStateName.DOWN, minutes_ago=30, minutes=30)
    _state(session, "EXT01", EquipmentStateName.RUNNING, minutes_ago=30, minutes=30)
    _state(session, "WND01", EquipmentStateName.RUNNING, minutes_ago=30, minutes=30)
    _book(session, "MIX01", good=600)
    _book(session, "PACK01", good=200)
    _book(session, "EXT01", good=60)       # one a minute at a one-second rating
    _book(session, "WND01", good=500)
    return session


def _equipment(session, code):
    return session.scalar(select(Equipment).where(Equipment.code == code))


def _state(session, code, state, *, minutes_ago, minutes):
    now = utcnow()
    session.add(EquipmentState(
        equipment_id=_equipment(session, code).id, state=state,
        started_at=now - timedelta(minutes=minutes_ago),
        ended_at=now - timedelta(minutes=minutes_ago - minutes)))
    session.flush()


def _book(session, code, *, good):
    order = session.scalar(select(WorkOrder).where(WorkOrder.code == "WO-ALL-1"))
    session.add(ProductionLog(
        work_order_id=order.id, operation_id=order.operations[0].id,
        equipment_id=_equipment(session, code).id, good_qty=good, scrap_qty=0.0,
        source=ProductionSource.OPC, ts=utcnow() - timedelta(minutes=15)))
    session.flush()


def test_the_plant_wide_call_lists_both_lines_machines_worst_first_with_unrated_last(
        client, plant):
    body = client.get("/analysis/oee", params={"line": "*", "hours": 8}).json()

    assert body["line"] == {"code": "*", "name": "All machines"}
    rows = body["stations"]
    assert {r["code"] for r in rows} == {"MIX01", "PACK01", "EXT01", "WND01"}
    assert body["machines_total"] == 4, "every list states its total"

    rated = [r for r in rows if r["oee"] is not None]
    unrated = [r for r in rows if r["oee"] is None]
    assert [r["code"] for r in unrated] == ["WND01"], "the winder has no rating"
    assert rows[-len(unrated):] == unrated, "no figure is listed after every figure"
    assert [r["oee"] for r in rated] == sorted(r["oee"] for r in rated), "worst first"
    assert rows[0]["code"] == "EXT01", "the extruder ran at a tenth of its rating"
    assert body["constraint"] == "EXT01"

    lines = {r["code"]: (r["line_code"], r["line_name"]) for r in rows}
    assert lines["MIX01"] == ("LINE1", _equipment(plant, "LINE1").name)
    assert lines["EXT01"] == ("LINE2", "Second line")


def test_one_line_at_a_time_still_answers_as_before(client, plant):
    one = client.get("/analysis/oee", params={"line": "LINE2", "hours": 8}).json()
    assert one["line"]["code"] == "LINE2"
    assert {r["code"] for r in one["stations"]} == {"EXT01", "WND01"}
    assert {r["line_code"] for r in one["stations"]} == {"LINE2"}

    biggest = client.get("/analysis/oee", params={"hours": 8}).json()
    assert biggest["line"]["code"] == "LINE1", "no line is still the biggest line, not the plant"


def test_the_plant_wide_spreadsheet_has_the_same_rows_in_the_same_order_with_their_line(
        client, plant):
    from tests.test_a_supervisor_exports_the_quality_checks_of_a_date_range_as_xlsx import read_sheet

    listed = client.get("/analysis/oee", params={"line": "*", "hours": 8}).json()["stations"]
    reply = client.get("/analysis/oee/export.xlsx", params={"line": "*", "hours": 8})
    assert reply.status_code == 200
    assert "*" not in reply.headers["content-disposition"], "not a file name Windows can save"
    assert "all-lines" in reply.headers["content-disposition"]
    header, *body = read_sheet(reply.content)
    station, line = header.index("Station"), header.index("Line")
    assert [(r[station], r[line]) for r in body] == [(s["code"], s["line_code"]) for s in listed]


def test_the_plant_wide_call_costs_no_more_queries_for_more_lines(session, plant):
    """One pass over the plant's states and production, not one per line:
    the same number of statements for two lines as for seven."""
    def statements():
        seen = []
        hook = lambda *a, **k: seen.append(1)  # noqa: E731
        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", hook)
        try:
            analysis.oee_breakdown(session, line_code=analysis.ALL_LINES, hours=8)
        finally:
            event.remove(engine, "before_cursor_execute", hook)
        return len(seen)

    statements()                  # the first call also warms the plant's settings
    two = statements()
    for n in range(3, 8):
        centre = Equipment(code=f"LINE{n}", name=f"Line {n}", level=EquipmentLevel.WORK_CENTER)
        session.add(centre)
        session.flush()
        session.add(Equipment(code=f"M{n}", name=f"Machine {n}", level=EquipmentLevel.WORK_UNIT,
                              parent_id=centre.id, ideal_cycle_seconds=1.0))
        session.flush()
        _state(session, f"M{n}", EquipmentStateName.RUNNING, minutes_ago=60, minutes=60)
    seven = statements()
    assert seven == two, f"{two} statements for two lines, {seven} for seven"
