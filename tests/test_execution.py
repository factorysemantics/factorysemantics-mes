"""Execution: lot consumption, machine-driven reporting, genealogy."""

import pytest

from fsmes.domain import OrderStatus, ProductionSource
from fsmes.services import Conflict, execution, workorders


@pytest.fixture()
def released_order(session):
    wo = workorders.create(session, code="WO-1", material_code="FG-COLA", quantity=4, actor="test")
    workorders.release(session, "WO-1", "test")
    return wo


def test_consume_decrements_lot_and_blocks_overdraw(session, released_order):
    lot = execution.consume(session, order_code="WO-1", lot_code="LOT-SUGAR-001", quantity=2, actor="test")
    assert lot.quantity == 498
    with pytest.raises(Conflict):
        execution.consume(session, order_code="WO-1", lot_code="LOT-SUGAR-001", quantity=9999)


def test_opc_counts_drive_order_to_completion(session, released_order):
    # Machine counters arrive as deltas; ops auto-start and auto-complete.
    for _ in range(4):
        execution.report(session, equipment_code="MIX01", good=1, source=ProductionSource.OPC, actor="opc-agent")
    execution.report(session, equipment_code="PACK01", good=4, source=ProductionSource.OPC, actor="opc-agent")

    assert released_order.status is OrderStatus.COMPLETED
    assert released_order.good_qty == 4


def test_opc_counts_without_an_active_order_return_no_operation_but_are_kept(session):
    # There is no operation to hand back, and the units are still real: they
    # go to the unassigned production list. tests/test_overproduction.py
    # pins what the list says about them.
    result = execution.report(session, equipment_code="MIX01", good=3, source=ProductionSource.OPC)
    assert result is None
    assert execution.unassigned_production(session)["good_total"] == 3


def test_manual_report_requires_started_operation(session, released_order):
    with pytest.raises(Conflict):
        execution.report(session, order_code="WO-1", seq=10, good=1)


def test_genealogy_traces_consumed_and_produced(session, released_order):
    execution.consume(session, order_code="WO-1", lot_code="LOT-SUGAR-001", quantity=2, actor="test")
    execution.report(session, equipment_code="MIX01", good=4, source=ProductionSource.OPC)
    execution.report(session, equipment_code="PACK01", good=4, source=ProductionSource.OPC)

    trace = execution.genealogy(session, "WO-1")
    assert trace["consumed"][0]["lot"] == "LOT-SUGAR-001"
    assert trace["produced"][0]["lot"] == "WO-1-FG"
