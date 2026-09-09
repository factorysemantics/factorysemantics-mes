"""Work order lifecycle rules."""

import pytest
from sqlalchemy import select

from fsmes.domain import ErpMessage, MaterialLot, MessageDirection, OrderStatus
from fsmes.services import Conflict, Invalid, workorders


def _create(session, code="WO-1", qty=10):
    return workorders.create(session, code=code, material_code="FG-COLA", quantity=qty, actor="test")


def test_create_copies_routing_operations(session):
    wo = _create(session)
    assert wo.status is OrderStatus.PLANNED
    assert [(op.seq, op.name) for op in wo.operations] == [(10, "Mix"), (20, "Pack")]


def test_create_requires_routing(session):
    with pytest.raises(Invalid):
        workorders.create(session, code="WO-X", material_code="RAW-SUGAR", quantity=5)


def test_full_lifecycle_completes_order_and_books_lot(session):
    wo = _create(session, qty=5)
    workorders.release(session, "WO-1", "test")
    for seq in (10, 20):
        workorders.start_operation(session, "WO-1", seq, "test")
        op = next(o for o in wo.operations if o.seq == seq)
        op.good_qty = 5
        workorders.complete_operation(session, "WO-1", seq, "test")

    assert wo.status is OrderStatus.COMPLETED
    fg_lot = session.scalar(select(MaterialLot).where(MaterialLot.code == "WO-1-FG"))
    assert fg_lot is not None and fg_lot.quantity == 5
    confirmation = session.scalar(select(ErpMessage).where(ErpMessage.direction == MessageDirection.OUT))
    assert confirmation is not None and confirmation.payload["good_qty"] == 5

    workorders.close(session, "WO-1", "test")
    assert wo.status is OrderStatus.CLOSED


def test_invalid_transitions_rejected(session):
    _create(session)
    with pytest.raises(Conflict):
        workorders.close(session, "WO-1")  # planned -> closed is not a thing
    workorders.release(session, "WO-1")
    with pytest.raises(Conflict):
        workorders.release(session, "WO-1")  # already released


def test_operations_cannot_start_on_planned_order(session):
    _create(session)
    with pytest.raises(Conflict):
        workorders.start_operation(session, "WO-1", 10)


def test_dispatch_list_filters_by_equipment(session):
    _create(session)
    workorders.release(session, "WO-1")
    ops = workorders.dispatch_list(session, "MIX01")
    assert [(op.order.code, op.seq) for op in ops] == [("WO-1", 10)]
