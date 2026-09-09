"""ERP-facing logic: import production requests, queue confirmations, and
keep the outbox honest.

All exchanges go through the ErpMessage table (transactional outbox/inbox),
so the interface has a complete, replayable history independent of
transport. The contract is SAP-shaped: one confirmation per operation
carrying quantities, cost center, times and component consumption, and one
completion per order. Each is keyed, so the same event queued twice is the
same message; each failure backs off, and after MAX_ATTEMPTS the message is
dead and a person decides.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import (
    ErpMessage,
    LotConsumption,
    MessageDirection,
    MessageStatus,
    OrderStatus,
    WorkOrder,
    WorkOrderOperation,
)
from fsmes.integrations.erp.contract import (
    ComponentUse,
    Confirmation,
    OperationConfirmation,
    OrderCompletion,
    ProductionRequest,
    as_payload,
)
from fsmes.services import Invalid, MesError, NotFound, audit, masterdata, workorders

MAX_ATTEMPTS = 8
BASE_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 3600


# ----------------------------------------------------------------- inbound

def import_order(session: Session, request: ProductionRequest | dict, actor: str = "erp") -> WorkOrder:
    """Create (or update, while still planned) a work order from a request."""
    if isinstance(request, dict):
        try:
            request = ProductionRequest.from_payload(request)
        except ValueError as exc:
            raise Invalid(str(exc)) from exc

    existing = session.scalar(select(WorkOrder).where(WorkOrder.code == request.code))
    if existing is not None:
        if existing.status is OrderStatus.PLANNED:
            before = {"quantity": existing.quantity, "priority": existing.priority}
            existing.quantity = request.quantity or existing.quantity
            existing.priority = request.priority
            existing.due_date = request.due_date or existing.due_date
            audit.record(session, actor=actor, action="workorder.updated_from_erp",
                         entity_type="workorder", entity_id=request.code, before=before,
                         after={"quantity": existing.quantity, "priority": existing.priority})
        return existing

    return workorders.create(
        session, code=request.code, material_code=request.material, quantity=request.quantity,
        due_date=request.due_date, priority=request.priority,
        erp_reference=request.erp_reference or request.code, actor=actor)


def process_inbound(session: Session, request: ProductionRequest | dict,
                    kind: str = "production_schedule") -> ErpMessage:
    """Record an inbound request and act on it. Failures are captured on the
    message (status=error) instead of raised - one bad order must not stall the sync."""
    payload = request.model_dump(mode="json") if isinstance(request, ProductionRequest) else dict(request)
    message = ErpMessage(direction=MessageDirection.IN, kind=kind, payload=payload)
    session.add(message)
    session.flush()
    try:
        import_order(session, request)
        message.status = MessageStatus.PROCESSED
    except MesError as exc:
        message.status = MessageStatus.ERROR
        message.error = str(exc)[:400]
    message.processed_at = utcnow()
    return message


# ---------------------------------------------------------------- outbound

def _enqueue(session: Session, confirmation: Confirmation) -> ErpMessage:
    """Idempotent by message key: the same event queued twice is one message."""
    existing = session.scalar(select(ErpMessage).where(ErpMessage.message_key == confirmation.message_key))
    if existing is not None:
        return existing
    message = ErpMessage(direction=MessageDirection.OUT, kind=confirmation.kind,
                         payload=as_payload(confirmation), message_key=confirmation.message_key)
    session.add(message)
    session.flush()
    return message


def operation_confirmation(session: Session, op: WorkOrderOperation) -> OperationConfirmation:
    """What one operation did, in the terms an ERP posts against."""
    wo = op.order
    ordered = sorted(wo.operations, key=lambda o: o.seq)
    index = next(i for i, o in enumerate(ordered) if o.id == op.id)
    input_qty = wo.quantity if index == 0 else (ordered[index - 1].good_qty or 0.0)
    good, scrap = op.good_qty or 0.0, op.scrap_qty or 0.0
    machine = op.equipment
    line = masterdata.work_center_of(session, machine) if machine else None
    handled = good + scrap
    consumed = session.scalars(select(LotConsumption).where(LotConsumption.operation_id == op.id)).all()
    return OperationConfirmation(
        message_key=f"{wo.code}:op{op.seq}",
        order=wo.code, erp_reference=wo.erp_reference, material=wo.material.code,
        seq=op.seq, operation=op.name,
        equipment=machine.code if machine else None,
        work_center=line.code if line else None,
        cost_center=masterdata.cost_center(session, machine) if machine else None,
        input_qty=input_qty, good_qty=good, scrap_qty=scrap, wip_qty=input_qty - good - scrap,
        setup_seconds=op.setup_seconds,
        machine_seconds=((op.completed_at - op.started_at).total_seconds()
                         if op.completed_at and op.started_at else None),
        labour_seconds=(op.labour_seconds_per_unit * handled) if op.labour_seconds_per_unit else None,
        started_at=op.started_at, completed_at=op.completed_at,
        components=[ComponentUse(lot=c.lot.code, material=c.lot.material.code, quantity=c.quantity,
                                 equipment=machine.code if machine else None) for c in consumed],
    )


def enqueue_operation_confirmation(session: Session, op: WorkOrderOperation) -> ErpMessage:
    return _enqueue(session, operation_confirmation(session, op))


def enqueue_confirmation(session: Session, wo: WorkOrder) -> ErpMessage:
    """Queue the order-level completion for delivery to the ERP."""
    return _enqueue(session, OrderCompletion(
        message_key=f"{wo.code}:completion",
        order=wo.code, erp_reference=wo.erp_reference, material=wo.material.code,
        ordered_qty=wo.quantity, good_qty=wo.good_qty, scrap_qty=wo.scrap_qty,
        lot=f"{wo.code}-FG" if wo.good_qty > 0 else None,
        started_at=wo.started_at, completed_at=wo.completed_at))


def pending_outbound(session: Session, now: datetime | None = None) -> list[ErpMessage]:
    """What is due for delivery: pending, and not backing off."""
    now = now or utcnow()
    return list(session.scalars(
        select(ErpMessage)
        .where(ErpMessage.direction == MessageDirection.OUT,
               ErpMessage.status == MessageStatus.PENDING,
               (ErpMessage.next_attempt_at.is_(None)) | (ErpMessage.next_attempt_at <= now))
        .order_by(ErpMessage.id)))


def backoff_seconds(attempts: int) -> int:
    """5 s, 10 s, 20 s ... capped at an hour."""
    return min(BASE_BACKOFF_SECONDS * 2 ** max(0, attempts - 1), MAX_BACKOFF_SECONDS)


def mark_sent(message: ErpMessage) -> None:
    message.status = MessageStatus.SENT
    message.error = None
    message.next_attempt_at = None
    message.processed_at = utcnow()


def mark_error(message: ErpMessage, error: Exception, now: datetime | None = None) -> ErpMessage:
    """Record the failure, back off, and after enough attempts stop trying."""
    now = now or utcnow()
    message.attempts = (message.attempts or 0) + 1
    message.error = str(error)[:400]
    if message.attempts >= MAX_ATTEMPTS:
        message.status = MessageStatus.DEAD
        message.next_attempt_at = None
    else:
        message.next_attempt_at = now + timedelta(seconds=backoff_seconds(message.attempts))
    return message


def retry(session: Session, message_id: int, actor: str = "system") -> ErpMessage:
    """Put a dead or failing message back in the queue, now."""
    message = session.get(ErpMessage, message_id)
    if message is None:
        raise NotFound(f"no ERP message {message_id}")
    if message.direction is not MessageDirection.OUT:
        raise Invalid(f"ERP message {message_id} is inbound; there is nothing to resend")
    before = message.status.value
    message.status = MessageStatus.PENDING
    message.next_attempt_at = None
    audit.record(session, actor=actor, action="erp.retried", entity_type="erp_message",
                 entity_id=str(message_id), before={"status": before, "attempts": message.attempts},
                 after={"status": "pending"})
    return message


def outbox_summary(session: Session, limit: int = 30) -> dict:
    """The queue as a person or an agent needs to see it."""
    counts = {status.value: 0 for status in MessageStatus}
    for status, n in session.execute(
            select(ErpMessage.status, func.count()).where(ErpMessage.direction == MessageDirection.OUT)
            .group_by(ErpMessage.status)):
        counts[status.value] = n
    kinds = {kind: n for kind, n in session.execute(
        select(ErpMessage.kind, func.count()).group_by(ErpMessage.kind))}
    oldest = session.scalar(select(func.min(ErpMessage.created_at)).where(
        ErpMessage.direction == MessageDirection.OUT, ErpMessage.status == MessageStatus.PENDING))
    recent = session.scalars(select(ErpMessage).order_by(ErpMessage.id.desc()).limit(limit)).all()
    return {
        "counts": counts,
        "kinds": kinds,
        "oldest_pending_seconds": round((utcnow() - oldest).total_seconds(), 1) if oldest else None,
        "recent": [
            {"id": m.id, "direction": m.direction.value, "kind": m.kind, "status": m.status.value,
             "order": (m.payload or {}).get("order") or (m.payload or {}).get("code"),
             "seq": (m.payload or {}).get("seq"), "attempts": m.attempts, "error": m.error,
             "created_at": m.created_at, "processed_at": m.processed_at,
             "next_attempt_at": m.next_attempt_at}
            for m in recent
        ],
    }
