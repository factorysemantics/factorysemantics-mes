"""When a machine counts past the order: what gets booked, and what happens
to the units that arrive after the order has closed.

The failure these pin was found on 2026-09-09 by the release check, running
`fsmes demo` from a built wheel: `Pack on PACK01: 16/15 good`, then
`machine counted with no active order equipment=PACK01 good=1`, then a crash
reading the last ERP confirmation. Sixteen units were made; the MES kept a
record of fifteen of them and a warning about the sixteenth.
"""

import pytest
from sqlalchemy import select

from fsmes.domain import (
    ErpMessage,
    MessageDirection,
    OperationStatus,
    OrderStatus,
    ProductionSource,
)
from fsmes.services import execution, workorders


@pytest.fixture()
def released_order(session):
    """Four units of cola: mix, then pack."""
    wo = workorders.create(session, code="WO-1", material_code="FG-COLA", quantity=4, actor="test")
    workorders.release(session, "WO-1", "test")
    return wo


def _completions(session):
    return [m for m in session.scalars(
        select(ErpMessage).where(ErpMessage.direction == MessageDirection.OUT).order_by(ErpMessage.id))
        if m.kind == "order_completion"]


def test_a_delta_that_straddles_the_ordered_quantity_books_every_unit_it_carried(session, released_order):
    # Counter readings coalesce to the latest value per machine, so one
    # booking can carry more than one unit. Three are booked, then a delta
    # of two arrives against an order for four.
    execution.report(session, equipment_code="MIX01", good=3, source=ProductionSource.OPC)
    execution.report(session, equipment_code="MIX01", good=2, source=ProductionSource.OPC)

    mix = released_order.operations[0]
    assert mix.good_qty == 5, "the machine counted five; five is what the MES has to say"
    assert mix.status is OperationStatus.DONE


def test_an_order_that_ran_past_its_quantity_says_by_how_much(session, released_order):
    execution.report(session, equipment_code="MIX01", good=5, source=ProductionSource.OPC)
    execution.report(session, equipment_code="PACK01", good=5, source=ProductionSource.OPC)

    assert released_order.status is OrderStatus.COMPLETED
    assert released_order.good_qty == 5
    assert released_order.over_qty == 1


def test_an_order_that_stopped_on_its_quantity_is_not_over_produced(session, released_order):
    execution.report(session, equipment_code="MIX01", good=4, source=ProductionSource.OPC)
    execution.report(session, equipment_code="PACK01", good=4, source=ProductionSource.OPC)

    assert released_order.over_qty == 0


def test_the_units_a_machine_counts_after_its_order_closed_are_recorded_not_dropped(session, released_order):
    execution.report(session, equipment_code="MIX01", good=4, source=ProductionSource.OPC)
    execution.report(session, equipment_code="PACK01", good=4, source=ProductionSource.OPC)
    assert released_order.status is OrderStatus.COMPLETED

    # The belt has not stopped. The next unit has no operation to book against.
    assert execution.report(session, equipment_code="PACK01", good=1,
                            source=ProductionSource.OPC) is None

    unassigned = execution.unassigned_production(session)
    assert unassigned["total"] == 1
    assert unassigned["good_total"] == 1
    assert unassigned["items"][0]["equipment"] == "PACK01"
    assert unassigned["items"][0]["order"] is None


def test_unassigned_production_can_be_asked_about_one_machine(session, released_order):
    execution.report(session, equipment_code="MIX01", good=9, source=ProductionSource.OPC)
    execution.report(session, equipment_code="PACK01", good=9, source=ProductionSource.OPC)
    execution.report(session, equipment_code="MIX01", good=2, source=ProductionSource.OPC)
    execution.report(session, equipment_code="PACK01", good=3, source=ProductionSource.OPC)

    assert execution.unassigned_production(session)["good_total"] == 5
    assert execution.unassigned_production(session, equipment_code="MIX01")["good_total"] == 2
    assert execution.unassigned_production(session, equipment_code="PACK01")["good_total"] == 3


def test_scrap_counted_with_no_order_open_is_recorded_too(session, released_order):
    execution.report(session, equipment_code="MIX01", good=4, source=ProductionSource.OPC)
    execution.report(session, equipment_code="PACK01", good=4, source=ProductionSource.OPC)
    execution.report(session, equipment_code="PACK01", scrap=2, source=ProductionSource.OPC)

    unassigned = execution.unassigned_production(session)
    assert unassigned["scrap_total"] == 2
    assert unassigned["good_total"] == 0


def test_a_person_reporting_against_an_idle_machine_is_still_told_no(session):
    """Unassigned production is what a counter is allowed to do, not a person:
    a typed count with no order open is a mistake worth stopping."""
    from fsmes.services import Invalid

    with pytest.raises(Invalid):
        execution.report(session, equipment_code="MIX01", good=1, source=ProductionSource.MANUAL)
    assert execution.unassigned_production(session)["total"] == 0


def test_the_order_completion_tells_the_erp_how_far_past_the_order_the_line_ran(session, released_order):
    execution.report(session, equipment_code="MIX01", good=5, source=ProductionSource.OPC)
    execution.report(session, equipment_code="PACK01", good=5, source=ProductionSource.OPC)

    payload = _completions(session)[-1].payload
    assert payload["ordered_qty"] == 4
    assert payload["good_qty"] == 5
    assert payload["over_qty"] == 1


def test_the_unassigned_production_list_states_its_total_over_the_api(client, session, released_order):
    execution.report(session, equipment_code="MIX01", good=4, source=ProductionSource.OPC)
    execution.report(session, equipment_code="PACK01", good=4, source=ProductionSource.OPC)
    for _ in range(3):
        execution.report(session, equipment_code="PACK01", good=1, source=ProductionSource.OPC)
    session.flush()

    body = client.get("/execution/unassigned", params={"limit": 2}).json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["has_more"] is True
    assert body["good_total"] == 3


def test_the_demo_reads_the_order_completion_not_whichever_message_landed_last():
    """The release check's crash: the demo printed `confirmations[-1]['lot']`,
    and the last message to arrive was an operation confirmation, which has
    no finished-goods lot because an operation does not make one."""
    from fsmes.cli import _order_completion

    messages = [
        {"kind": "operation_confirmation", "order": "WO-1", "seq": 10},
        {"kind": "order_completion", "order": "WO-1", "lot": "WO-1-FG", "good_qty": 4},
        {"kind": "operation_confirmation", "order": "WO-1", "seq": 20},
    ]
    assert _order_completion(messages, "WO-1")["lot"] == "WO-1-FG"
    assert _order_completion(messages[:1], "WO-1") is None
    assert _order_completion(messages, "WO-2") is None


def test_the_b2mml_confirmation_carries_the_over_run_too(session, released_order):
    """A file-based ERP reads the same fact as a REST one, or the two
    transports disagree about what the plant made."""
    from fsmes.integrations.erp.b2mml import render_production_performance

    execution.report(session, equipment_code="MIX01", good=5, source=ProductionSource.OPC)
    execution.report(session, equipment_code="PACK01", good=5, source=ProductionSource.OPC)

    xml = render_production_performance(_completions(session)[-1].payload)
    assert "<OverQuantity>1</OverQuantity>" in xml
