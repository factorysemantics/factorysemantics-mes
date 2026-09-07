"""Maintenance, and material at a station.

The two things that made this plant read as a job shop rather than a line:
nothing entered anywhere in particular, and nothing was ever serviced.
"""

from datetime import timedelta

import pytest

from fsmes.db import utcnow
from fsmes.domain import EquipmentState, MaintenanceStatus
from fsmes.domain.equipment import EquipmentStateName
from fsmes.services import Conflict, Invalid, maintenance, masterdata


@pytest.fixture()
def mixer(session):
    return masterdata.get_equipment(session, "MIX01")


def _ran_for(session, equipment, hours: float, state=EquipmentStateName.RUNNING):
    end = utcnow()
    session.add(EquipmentState(
        equipment_id=equipment.id, state=state,
        started_at=end - timedelta(hours=hours), ended_at=end))
    session.flush()


# ------------------------------------------------------- due on use, not date

def test_a_plan_comes_due_on_hours_the_machine_actually_ran(session, mixer):
    """A plan written in days over-maintains the machine that sat idle and
    under-maintains the one that ran two shifts."""
    maintenance.create_plan(session, code="PM-1", name="Seals", equipment_code="MIX01",
                            trigger="runtime_hours", interval=8.0)
    _ran_for(session, mixer, hours=3.0)
    assert maintenance.due(session, include_soon=False) == []

    _ran_for(session, mixer, hours=6.0)
    due = maintenance.due(session, include_soon=False)
    assert [d["plan"] for d in due] == ["PM-1"]
    assert due[0]["used"] == pytest.approx(9.0, abs=0.1)


def test_idle_time_is_not_use(session, mixer):
    """Only RUNNING counts. An interval that counted idle would come due on a
    machine that has been sitting still."""
    maintenance.create_plan(session, code="PM-2", name="Seals", equipment_code="MIX01",
                            trigger="runtime_hours", interval=4.0)
    _ran_for(session, mixer, hours=20.0, state=EquipmentStateName.IDLE)
    _ran_for(session, mixer, hours=20.0, state=EquipmentStateName.DOWN)
    assert maintenance.due(session, include_soon=False) == []


def test_a_plan_warns_before_it_is_due(session, mixer):
    """A plant wants warning, not a surprise."""
    maintenance.create_plan(session, code="PM-3", name="Seals", equipment_code="MIX01",
                            trigger="runtime_hours", interval=10.0)
    _ran_for(session, mixer, hours=8.5)
    soon = maintenance.due(session, include_soon=True)
    assert soon[0]["due_soon"] is True and soon[0]["due"] is False


def test_a_never_serviced_calendar_plan_is_due_and_says_why(session):
    maintenance.create_plan(session, code="PM-4", name="Grease", equipment_code="MIX01",
                            trigger="calendar_days", interval=7.0)
    row = maintenance.due(session, include_soon=False)[0]
    assert row["due"] is True
    assert "never serviced" in row["unit"]


def test_an_unknown_trigger_is_refused(session):
    with pytest.raises(Invalid, match="unknown trigger"):
        maintenance.create_plan(session, code="PM-X", name="X", equipment_code="MIX01",
                                trigger="phases_of_the_moon", interval=1.0)


# ------------------------------------------------------------- raising work

def test_due_plans_raise_work_with_the_reason_on_it(session, mixer):
    """A due date with no reason is a due date nobody trusts."""
    maintenance.create_plan(session, code="PM-5", name="Seals", equipment_code="MIX01",
                            trigger="runtime_hours", interval=4.0)
    _ran_for(session, mixer, hours=5.0)
    raised = maintenance.raise_due(session)
    assert len(raised) == 1
    assert "against a 4.0" in raised[0].reason
    assert raised[0].kind.value == "preventive"


def test_raising_twice_does_not_duplicate_the_job(session, mixer):
    """A list full of duplicates is a list people learn to ignore."""
    maintenance.create_plan(session, code="PM-6", name="Seals", equipment_code="MIX01",
                            trigger="runtime_hours", interval=4.0)
    _ran_for(session, mixer, hours=5.0)
    assert len(maintenance.raise_due(session)) == 1
    assert maintenance.raise_due(session) == []


def test_corrective_work_can_be_raised_without_a_plan(session):
    """Things break without asking a schedule first."""
    order = maintenance.raise_corrective(
        session, equipment_code="MIX01", summary="Bearing seized",
        reason="operator reported grinding")
    assert order.kind.value == "corrective" and order.plan_id is None


# ------------------------------------------------------------ doing the work

def test_completing_rebaselines_the_plan_from_the_work_actually_done(session, mixer):
    """A plan that keeps counting from its original date drifts a little
    further out of step every cycle."""
    maintenance.create_plan(session, code="PM-7", name="Seals", equipment_code="MIX01",
                            trigger="runtime_hours", interval=4.0)
    _ran_for(session, mixer, hours=5.0)
    order = maintenance.raise_due(session)[0]

    maintenance.start(session, order.code, actor="TECH")
    done = maintenance.complete(session, order.code, findings="Seals replaced.",
                                actor="TECH")
    assert done.status is MaintenanceStatus.DONE
    assert done.findings == "Seals replaced."
    assert done.downtime_minutes is not None

    # The interval now counts from the service, so nothing is due.
    assert maintenance.due(session, include_soon=False) == []


def test_work_cannot_be_completed_twice(session):
    order = maintenance.raise_corrective(session, equipment_code="MIX01", summary="x")
    maintenance.complete(session, order.code)
    with pytest.raises(Conflict, match="already done"):
        maintenance.complete(session, order.code)


def test_the_backlog_carries_what_it_will_cost(session, mixer):
    """A maintenance list without its downtime is a wish."""
    maintenance.create_plan(session, code="PM-8", name="Seals", equipment_code="MIX01",
                            trigger="runtime_hours", interval=1.0, expected_minutes=45.0)
    _ran_for(session, mixer, hours=2.0)
    maintenance.raise_due(session)
    backlog = maintenance.backlog(session)
    assert backlog["open"] == 1
    assert backlog["expected_downtime_minutes"] == 45.0


# --------------------------------------------------------- who may do what

def test_an_inspector_may_not_service_a_machine(sign_in):
    """Inspecting and servicing are different jobs and different competences,
    whatever the org chart says about seniority."""
    inspector = sign_in("QI9", role="quality_inspector")
    r = inspector.post("/maintenance/corrective",
                       json={"equipment": "MIX01", "summary": "x"})
    assert r.status_code == 403


def test_an_operator_may_service_but_not_write_the_plan(client):
    assert client.post("/maintenance/corrective",
                       json={"equipment": "MIX01", "summary": "seized"}).status_code == 201
    assert client.post("/maintenance/plans", json={
        "code": "PM-N", "name": "N", "equipment": "MIX01",
        "trigger": "runtime_hours", "interval": 4.0}).status_code == 403


# ------------------------------------------------- material at a station

def test_a_component_can_enter_at_two_stations(session):
    """Water goes into a bottling line at the washer and again at the filler.
    The old unique constraint said a material appears once in a bill."""
    masterdata.add_bom_item(session, parent_code="FG-COLA", component_code="RAW-SUGAR",
                            quantity=1.0, operation_seq=10)
    masterdata.add_bom_item(session, parent_code="FG-COLA", component_code="RAW-SUGAR",
                            quantity=0.5, operation_seq=30)
    session.flush()
    from fsmes.services import staging
    seqs = {line["seq"] for line in staging.bom_for(session, "FG-COLA")
            if line["component"] == "RAW-SUGAR"}
    # The demo plant already has an unassigned sugar line; what matters is
    # that the same component now also exists at two named stations.
    assert {10, 30} <= seqs


def test_consumption_records_where_it_went_in(session, client):
    """'Which lot of caps is in this pallet' needs the station. 'Which lot is
    in this order' is a much weaker claim."""
    from fsmes.services import execution, workorders

    workorders.create(session, code="WO-M1", material_code="FG-COLA", quantity=10,
                      actor="test")
    workorders.release(session, "WO-M1", actor="test")
    execution.create_lot(session, code="LOT-M1", material_code="RAW-SUGAR",
                         quantity=100, actor="test")
    session.flush()

    execution.consume(session, order_code="WO-M1", lot_code="LOT-M1", quantity=5,
                      seq=10, actor="test")
    session.flush()

    trace = execution.genealogy(session, "WO-M1")
    consumed = trace["consumed"][0]
    assert consumed["seq"] == 10
    assert consumed["equipment"] is not None
    assert consumed["operation"] is not None


def test_consuming_at_a_station_the_order_does_not_have_is_refused(session):
    from fsmes.services import execution, workorders

    workorders.create(session, code="WO-M2", material_code="FG-COLA", quantity=10,
                      actor="test")
    workorders.release(session, "WO-M2", actor="test")
    execution.create_lot(session, code="LOT-M2", material_code="RAW-SUGAR",
                         quantity=100, actor="test")
    session.flush()
    with pytest.raises(Invalid, match="no operation 999"):
        execution.consume(session, order_code="WO-M2", lot_code="LOT-M2",
                          quantity=1, seq=999, actor="test")


def test_staging_counts_against_what_is_left_to_make(session):
    """A station that has already run most of the order does not need the
    whole bill again - telling a supervisor otherwise sends them chasing
    material they do not need."""
    from fsmes.services import staging, workorders

    masterdata.add_bom_item(session, parent_code="FG-COLA",
                            component_code="RAW-SUGAR", quantity=2.0, operation_seq=10)
    from fsmes.services import execution as execution_service

    workorders.create(session, code="WO-M3", material_code="FG-COLA", quantity=100,
                      actor="test")
    workorders.release(session, "WO-M3", actor="test")
    session.flush()
    # good_qty is derived from what was booked, not settable - so book it,
    # which is also the only way it happens on a real line. The operation has
    # to be started first, which is the MES refusing to book against a step
    # nobody began.
    # Booked on the LAST operation: an order's good quantity is what came off
    # the end of its route, not what the first station touched. Booking on
    # step 10 moves nothing, which is the model being right.
    order = workorders.get(session, "WO-M3")
    last = order.operations[-1].seq
    workorders.start_operation(session, "WO-M3", last, actor="test")
    execution_service.report(session, order_code="WO-M3", seq=last, good=90, actor="test")
    session.flush()

    view = staging.staging(session, "WO-M3")
    line = view["stations"][0]["components"][0]
    assert view["remaining"] == 10.0
    assert line["needed_for_remaining"] == 20.0   # not 200
