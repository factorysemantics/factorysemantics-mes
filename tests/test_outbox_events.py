"""The outbox as a domain event log.

One table, one write per fact, two readers that do not tread on each other.
These pin the part that is easy to get wrong: that a plant event is written
in the same transaction as the fact it describes, that it says `null` where
the MES did not observe something, and that the ERP sync leaves it alone.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from fsmes.config import Settings
from fsmes.domain import (
    EquipmentStateName,
    ErpMessage,
    MessageDirection,
    MessageStatus,
    OrderStatus,
    ProductionSource,
)
from fsmes.integrations.erp.contract import CONFIRMATION_KINDS
from fsmes.integrations.erp.sync import cycle
from fsmes.services import equipment, erp, execution, masterdata, outbox, workorders


def outbound(session, kind: str | None = None) -> list[ErpMessage]:
    query = select(ErpMessage).where(ErpMessage.direction == MessageDirection.OUT)
    if kind is not None:
        query = query.where(ErpMessage.kind == kind)
    return list(session.scalars(query.order_by(ErpMessage.id)))


class RecordingAdapter:
    """An ERP that remembers everything it was asked to post."""

    def __init__(self) -> None:
        self.sent = []

    def fetch_orders(self):
        return []

    def acknowledge(self, code):
        pass

    def send_confirmation(self, confirmation):
        self.sent.append(confirmation)


@pytest.fixture()
def events_off(monkeypatch):
    """A plant that has turned domain events off."""
    monkeypatch.setattr(outbox, "get_settings",
                        lambda: Settings(outbox_domain_events=False))


# ------------------------------------------------------- equipment states

def test_a_machine_changing_state_writes_one_event_naming_what_it_left(session):
    """The state history and the event stream have to agree, so the event is
    written by the same call that closed the old interval."""
    equipment.set_state(session, equipment_code="MIX01",
                        state=EquipmentStateName.RUNNING, actor="opc")
    equipment.set_state(session, equipment_code="MIX01",
                        state=EquipmentStateName.DOWN, reason="jam", actor="opc")

    events = outbound(session, "equipment_state_change")
    assert len(events) == 2
    latest = events[-1].payload
    assert latest["equipment"] == "MIX01" and latest["work_center"] == "LINE1"
    assert latest["state"] == "down" and latest["reason"] == "jam"
    assert latest["previous_state"] == "running"
    assert latest["actor"] == "opc"


def test_the_first_state_a_machine_is_ever_seen_in_reports_no_previous_run_rather_than_a_zero_one(session):
    """Nothing was closed, so there is no duration to report. Zero would say
    the machine ran for no time; null says the MES was not watching yet."""
    equipment.set_state(session, equipment_code="MIX01",
                        state=EquipmentStateName.RUNNING, actor="opc")

    first = outbound(session, "equipment_state_change")[0].payload
    assert first["previous_state"] is None
    assert first["previous_seconds"] is None


def test_a_closed_run_reports_the_seconds_the_mes_actually_observed(session):
    """The duration is read off the interval the MES closed, never computed
    from anything it did not record."""
    opened = equipment.set_state(session, equipment_code="MIX01",
                                 state=EquipmentStateName.RUNNING, actor="opc")
    equipment.set_state(session, equipment_code="MIX01",
                        state=EquipmentStateName.IDLE, actor="opc")

    second = outbound(session, "equipment_state_change")[-1].payload
    observed = (opened.ended_at - opened.started_at).total_seconds()
    assert second["previous_seconds"] == pytest.approx(observed, abs=0.01)


def test_a_state_that_did_not_change_is_not_an_event(session):
    """The OPC agent re-asserts a machine's state every poll. One event per
    poll would fill the namespace with a plant that never moves."""
    equipment.set_state(session, equipment_code="MIX01",
                        state=EquipmentStateName.RUNNING, actor="opc")
    equipment.set_state(session, equipment_code="MIX01",
                        state=EquipmentStateName.RUNNING, actor="opc")

    assert len(outbound(session, "equipment_state_change")) == 1


def test_a_machine_that_belongs_to_no_line_says_so_instead_of_borrowing_one(session):
    """`work_center: null` is a real answer. Naming the level above would tell
    a consumer the machine sits on a line it does not sit on."""
    from fsmes.domain import Equipment, EquipmentLevel
    site = session.scalar(select(Equipment).where(Equipment.code == "KC1"))
    session.add(Equipment(code="LOOSE01", name="Unassigned machine",
                          level=EquipmentLevel.WORK_UNIT, parent=site))
    session.flush()

    equipment.set_state(session, equipment_code="LOOSE01",
                        state=EquipmentStateName.DOWN, actor="test")

    assert outbound(session, "equipment_state_change")[-1].payload["work_center"] is None


# ------------------------------------------------------------ order holds

def test_holding_an_order_publishes_the_reason_and_what_it_interrupted(session):
    """A stopped line and an unwatched line look identical in a namespace
    until somebody says which. The reason is the whole point of the event."""
    workorders.create(session, code="WO-EV-1", material_code="FG-COLA", quantity=5, actor="test")
    workorders.release(session, "WO-EV-1", actor="test")
    workorders.hold(session, "WO-EV-1", "awaiting a quality decision", actor="SUP")

    held = outbound(session, "order_hold")
    assert len(held) == 1
    assert held[0].payload["order"] == "WO-EV-1"
    assert held[0].payload["reason"] == "awaiting a quality decision"
    assert held[0].payload["previous_status"] == "released"
    assert held[0].payload["material"] == "FG-COLA"


def test_resuming_says_where_the_order_landed_not_a_fixed_status(session):
    """An order that had started resumes to running; one that had not resumes
    to released. Publishing one for the other would claim or erase progress."""
    workorders.create(session, code="WO-EV-2", material_code="FG-COLA", quantity=5, actor="test")
    workorders.release(session, "WO-EV-2", actor="test")
    execution.report(session, equipment_code="MIX01", good=1, source=ProductionSource.OPC)
    workorders.hold(session, "WO-EV-2", "awaiting a quality decision", actor="SUP")
    workorders.resume(session, "WO-EV-2", actor="SUP")

    resumed = outbound(session, "order_resume")
    assert len(resumed) == 1
    assert resumed[0].payload["previous_status"] == "on_hold"
    assert resumed[0].payload["status"] == OrderStatus.RUNNING.value


def test_the_same_order_held_twice_is_two_events(session):
    """A hold is a moment, not a state. Collapsing the second one would hide
    a stoppage that really happened."""
    workorders.create(session, code="WO-EV-3", material_code="FG-COLA", quantity=5, actor="test")
    workorders.release(session, "WO-EV-3", actor="test")
    workorders.hold(session, "WO-EV-3", "first concern", actor="SUP")
    workorders.resume(session, "WO-EV-3", actor="SUP")
    workorders.hold(session, "WO-EV-3", "second concern", actor="SUP")

    reasons = [m.payload["reason"] for m in outbound(session, "order_hold")]
    assert reasons == ["first concern", "second concern"]


# ------------------------------------------------- the two readers, apart

def test_a_kind_the_erp_cannot_parse_is_left_where_it_is_and_never_attempted(session, scope):
    """The ERP sync used to read every pending row as a confirmation, which is
    why no other kind could live in the outbox. A plant event must not be
    posted to the ERP, must not fail, and must not spend a retry."""
    _order_through_one_operation(session, "WO-EV-4")
    equipment.set_state(session, equipment_code="PACK01",
                        state=EquipmentStateName.DOWN, reason="jam", actor="opc")
    session.flush()
    adapter = RecordingAdapter()

    cycle(adapter, scope)
    cycle(adapter, scope)

    assert {type(c).__name__ for c in adapter.sent} <= {"OperationConfirmation", "OrderCompletion"}
    state_event = outbound(session, "equipment_state_change")[0]
    assert state_event.status is MessageStatus.PENDING
    assert state_event.attempts == 0 and state_event.error is None
    assert state_event.id not in [m.id for m in erp.pending_outbound(session)]


def test_the_erp_still_gets_every_confirmation_with_plant_events_beside_them(session, scope):
    """The kind filter must not narrow what the ERP is owed."""
    _order_through_one_operation(session, "WO-EV-5")
    equipment.set_state(session, equipment_code="MIX01",
                        state=EquipmentStateName.RUNNING, actor="opc")
    session.flush()
    adapter = RecordingAdapter()

    cycle(adapter, scope)

    confirmations = [m for m in outbound(session) if m.kind in CONFIRMATION_KINDS]
    assert confirmations and all(m.status is MessageStatus.SENT for m in confirmations)
    assert len(adapter.sent) == len(confirmations)


def test_the_erp_queue_counts_the_erp_s_own_work_and_states_the_rest(session):
    """"pending: 400" when the ERP is owed nothing is exactly the convincing
    wrong number the house rules exist to stop - and hiding the other rows
    would be the opposite mistake."""
    _order_through_one_operation(session, "WO-EV-6")
    for state in (EquipmentStateName.RUNNING, EquipmentStateName.DOWN, EquipmentStateName.IDLE):
        equipment.set_state(session, equipment_code="MIX01", state=state, actor="opc")
    session.flush()

    summary = erp.outbox_summary(session)
    owed = len([m for m in outbound(session) if m.kind in CONFIRMATION_KINDS])
    assert summary["counts"]["pending"] == owed
    assert summary["other_outbound"] == 3
    assert summary["kinds"]["equipment_state_change"] == 3


# ------------------------------------------------------- the way to say no

def test_a_plant_that_wants_only_erp_traffic_can_have_only_erp_traffic(session, events_off):
    """Off is a setting, not a code change - and it is off, not quietly
    half-on: no state change, no hold, nothing."""
    _order_through_one_operation(session, "WO-EV-7")
    equipment.set_state(session, equipment_code="MIX01",
                        state=EquipmentStateName.RUNNING, actor="opc")
    workorders.hold(session, "WO-EV-7", "a concern", actor="SUP")

    assert outbound(session, "equipment_state_change") == []
    assert outbound(session, "order_hold") == []
    assert [m for m in outbound(session) if m.kind in CONFIRMATION_KINDS]


def test_the_state_history_is_kept_whether_or_not_the_event_is(session, events_off):
    """Turning the event log down must never turn the MES's own record down."""
    equipment.set_state(session, equipment_code="MIX01",
                        state=EquipmentStateName.RUNNING, actor="opc")

    assert [s.state for s in equipment.current_states(session)
            if s.equipment_id == masterdata.get_equipment(session, "MIX01").id] == \
        [EquipmentStateName.RUNNING]


def _order_through_one_operation(session, code: str) -> None:
    """An order far enough along that the ERP is owed a confirmation."""
    workorders.create(session, code=code, material_code="FG-COLA", quantity=3, actor="test")
    workorders.release(session, code, actor="test")
    execution.report(session, equipment_code="MIX01", good=3, source=ProductionSource.OPC)
    session.flush()
