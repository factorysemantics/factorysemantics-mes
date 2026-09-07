"""Work order lifecycle: planned → released → running → completed → closed.

Operations are copied from the material's routing at creation. Completing the
last operation completes the order, which books the finished-good lot and
queues the production confirmation for the ERP.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import (
    Equipment,
    OperationStatus,
    OrderStatus,
    Routing,
    WorkOrder,
    WorkOrderOperation,
)
from fsmes.services import Conflict, Invalid, NotFound, audit, masterdata

_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PLANNED: {OrderStatus.RELEASED, OrderStatus.CANCELLED},
    OrderStatus.RELEASED: {OrderStatus.RUNNING, OrderStatus.ON_HOLD,
                           OrderStatus.CANCELLED},
    OrderStatus.RUNNING: {OrderStatus.COMPLETED, OrderStatus.ON_HOLD},
    # Held work is expected back: it resumes to wherever it truly was, or is
    # cancelled if the concern turns out to be fatal.
    OrderStatus.ON_HOLD: {OrderStatus.RELEASED, OrderStatus.RUNNING,
                          OrderStatus.CANCELLED},
    OrderStatus.COMPLETED: {OrderStatus.CLOSED},
}


def get(session: Session, code: str) -> WorkOrder:
    wo = session.scalar(select(WorkOrder).where(WorkOrder.code == code))
    if wo is None:
        raise NotFound(f"work order {code!r} not found")
    return wo


def _transition(session: Session, wo: WorkOrder, new_status: OrderStatus, actor: str) -> None:
    if new_status not in _TRANSITIONS.get(wo.status, set()):
        raise Conflict(f"work order {wo.code}: cannot go from {wo.status.value} to {new_status.value}")
    before = wo.status
    wo.status = new_status
    audit.record(
        session,
        actor=actor,
        action=f"workorder.{new_status.value}",
        entity_type="workorder",
        entity_id=wo.code,
        before={"status": before.value},
        after={"status": new_status.value},
    )


def create(
    session: Session,
    *,
    code: str,
    material_code: str,
    quantity: float,
    due_date: datetime | None = None,
    priority: int = 50,
    erp_reference: str | None = None,
    actor: str = "system",
) -> WorkOrder:
    if quantity <= 0:
        raise Invalid("order quantity must be positive")
    if session.scalar(select(WorkOrder).where(WorkOrder.code == code)):
        raise Conflict(f"work order {code!r} already exists")
    material = masterdata.get_material(session, material_code)
    routing = session.scalar(select(Routing).where(Routing.material_id == material.id))
    if routing is None:
        raise Invalid(f"material {material_code!r} has no routing — cannot build order operations")

    wo = WorkOrder(
        code=code,
        material=material,
        quantity=quantity,
        due_date=due_date,
        priority=priority,
        erp_reference=erp_reference,
    )
    for op in routing.operations:
        wo.operations.append(WorkOrderOperation(
            seq=op.seq, name=op.name,
            # A step that names a cell is resolved to a member now. Late
            # binding at dispatch is better and is not built; choosing here
            # at least means an order always knows where it runs, and the
            # choice is deterministic rather than whatever the database
            # returned first.
            equipment_id=op.equipment_id or masterdata.pick_machine(session, op),
            setup_seconds=op.setup_seconds,
            run_seconds_per_unit=op.run_seconds_per_unit,
            labour_seconds_per_unit=op.labour_seconds_per_unit,
        ))

    # The line is where the route starts. Derived rather than asked for: a
    # routing already says which machines run the work, and making the caller
    # repeat it is how the two come to disagree.
    #
    # Read from the *order's* first operation, not the routing's: a routing
    # step may name a cell rather than a machine, and the order is where that
    # got resolved. Asking the routing would hand session.get a null id.
    first = min(wo.operations, key=lambda o: o.seq, default=None)
    if first is not None and first.equipment_id is not None:
        machine = session.get(Equipment, first.equipment_id)
        centre = masterdata.work_center_of(session, machine) if machine else None
        wo.work_center_id = centre.id if centre else None

    session.add(wo)
    session.flush()
    audit.record(
        session,
        actor=actor,
        action="workorder.created",
        entity_type="workorder",
        entity_id=code,
        after={"material": material_code, "quantity": quantity, "routing": routing.code},
    )
    return wo


def release(session: Session, code: str, actor: str = "system") -> WorkOrder:
    wo = get(session, code)
    _transition(session, wo, OrderStatus.RELEASED, actor)
    wo.released_at = utcnow()
    return wo


def cancel(session: Session, code: str, actor: str = "system") -> WorkOrder:
    wo = get(session, code)
    _transition(session, wo, OrderStatus.CANCELLED, actor)
    return wo


def hold(session: Session, code: str, reason: str, actor: str = "system") -> WorkOrder:
    """Stop an order without pretending it is finished.

    The reason is mandatory: a hold with no reason is indistinguishable from
    an accident, and the person resuming it has to know what was wrong.
    """
    if not (reason or "").strip():
        raise Invalid("a hold needs a reason - the person resuming it has to know why")
    wo = get(session, code)
    _transition(session, wo, OrderStatus.ON_HOLD, actor)
    # The transition audit carries only the status pair; the reason is the
    # part someone will actually search for.
    audit.record(session, actor=actor, action="workorder.hold_reason",
                 entity_type="workorder", entity_id=code,
                 after={"reason": reason.strip()})
    return wo


def resume(session: Session, code: str, actor: str = "system") -> WorkOrder:
    """Back to work - to RUNNING if any operation had started, else RELEASED.

    Resuming to the wrong one would either claim progress that never happened
    or erase progress that did.
    """
    wo = get(session, code)
    if wo.status is not OrderStatus.ON_HOLD:
        raise Conflict(f"work order {code} is {wo.status.value}, not on hold")
    started = any(op.status is not OperationStatus.PENDING for op in wo.operations)
    _transition(session, wo,
                OrderStatus.RUNNING if started else OrderStatus.RELEASED, actor)
    return wo


def close(session: Session, code: str, actor: str = "system") -> WorkOrder:
    wo = get(session, code)
    _transition(session, wo, OrderStatus.CLOSED, actor)
    wo.closed_at = utcnow()
    return wo


def _op(wo: WorkOrder, seq: int) -> WorkOrderOperation:
    for op in wo.operations:
        if op.seq == seq:
            return op
    raise NotFound(f"work order {wo.code} has no operation {seq}")


def start_operation(session: Session, code: str, seq: int, actor: str = "system") -> WorkOrderOperation:
    wo = get(session, code)
    if wo.status not in (OrderStatus.RELEASED, OrderStatus.RUNNING):
        raise Conflict(f"work order {wo.code} is {wo.status.value}; release it before starting operations")
    op = _op(wo, seq)
    if op.status is not OperationStatus.PENDING:
        raise Conflict(f"operation {seq} of {wo.code} is already {op.status.value}")
    op.status = OperationStatus.RUNNING
    op.started_at = utcnow()
    if wo.status is OrderStatus.RELEASED:
        _transition(session, wo, OrderStatus.RUNNING, actor)
        wo.started_at = utcnow()
    audit.record(
        session,
        actor=actor,
        action="operation.started",
        entity_type="workorder",
        entity_id=wo.code,
        after={"seq": seq, "name": op.name},
    )
    return op


def complete_operation(session: Session, code: str, seq: int, actor: str = "system") -> WorkOrderOperation:
    wo = get(session, code)
    op = _op(wo, seq)
    if op.status is not OperationStatus.RUNNING:
        raise Conflict(f"operation {seq} of {wo.code} is {op.status.value}, not running")
    op.status = OperationStatus.DONE
    op.completed_at = utcnow()
    audit.record(
        session,
        actor=actor,
        action="operation.completed",
        entity_type="workorder",
        entity_id=wo.code,
        after={"seq": seq, "good_qty": op.good_qty, "scrap_qty": op.scrap_qty},
    )
    # The per-operation confirmation the ERP posts labour, machine time and
    # consumption against - queued the moment the step is done, keyed so a
    # second completion of the same step is the same message.
    from fsmes.services import erp  # local import to avoid a cycle

    erp.enqueue_operation_confirmation(session, op)
    if all(o.status is OperationStatus.DONE for o in wo.operations):
        _complete_order(session, wo, actor)
    return op


def _complete_order(session: Session, wo: WorkOrder, actor: str) -> None:
    from fsmes.services import erp, execution  # local import to avoid a cycle

    _transition(session, wo, OrderStatus.COMPLETED, actor)
    wo.completed_at = utcnow()
    if wo.good_qty > 0:
        execution.create_lot(
            session,
            code=f"{wo.code}-FG",
            material_code=wo.material.code,
            quantity=wo.good_qty,
            produced_by_order=wo,
            actor=actor,
        )
    erp.enqueue_confirmation(session, wo)
    # The certificate of analysis is the end-of-line document: issued the
    # moment the order completes, from records that already exist, and never
    # edited after. A failure to render it must not fail the completion.
    from fsmes.services import coa

    try:
        coa.issue(session, wo.code, actor=actor)
    except Exception:  # pragma: no cover - a defensive seam, exercised live
        import structlog

        structlog.get_logger("coa").exception("certificate not issued", order=wo.code)


def dispatch_list(session: Session, equipment_code: str | None = None) -> list[WorkOrderOperation]:
    """What should run next, per machine — the operator's to-do list."""
    query = (
        select(WorkOrderOperation)
        .join(WorkOrder)
        .where(
            WorkOrder.status.in_((OrderStatus.RELEASED, OrderStatus.RUNNING)),
            WorkOrderOperation.status != OperationStatus.DONE,
        )
        .order_by(WorkOrder.priority, WorkOrder.code, WorkOrderOperation.seq)
    )
    if equipment_code:
        equipment = masterdata.get_equipment(session, equipment_code)
        query = query.where(WorkOrderOperation.equipment_id == equipment.id)
    return list(session.scalars(query))
