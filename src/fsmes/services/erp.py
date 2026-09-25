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

from dataclasses import dataclass
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
    CONFIRMATION_KINDS,
    ComponentUse,
    Confirmation,
    OperationConfirmation,
    OrderCompletion,
    ProductionRequest,
    as_payload,
)
from fsmes.services import Invalid, MesError, NotFound, audit, masterdata, outbox, workorders

# The literals this product ships, and the third of the three layers every
# reader below goes through. They are not the whole answer to what a plant is
# running on - `fsmes.services.plant_settings` is - and they are kept named
# because a default worth reading is a default worth being able to point at.
#
# `fsmes.services.uns` holds three constants with these same three values and
# they are deliberately not shared: the namespace broker on this site and the
# ERP across a VPN are two systems with two outages, and a plant that widened
# one because its ERP has a weekly maintenance window did not mean to widen
# the other. Audit rows C8 and S1, same shape, two answers.
MAX_ATTEMPTS = 8
BASE_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 3600
DEFAULT_ORDER_PRIORITY = 50

#: The statuses ERPNext ships. The words are the ERP's, never this product's:
#: a site that renamed them, or added one, is describing its own release
#: process and is not wrong.
OPEN_STATUSES = ("Not Started", "In Process")

#: When a number the ERP hands back is the number that was sent. Frappe
#: returns a Float rounded to the site's own float precision.
FLOAT_REL_TOL = 1e-3
FLOAT_ABS_TOL = 0.01

#: How long one request to a system this plant does not own may take.
HTTP_TIMEOUT = 30.0
REST_TIMEOUT = 10.0

#: The pack table this plant's ERP settings live in.
SECTION = "erp"


def _number(session: Session, name: str, fallback):
    """One of this plant's ERP settings, read at the moment it is needed.

    Read through `fsmes.services.plant_settings`: the row this plant's own
    administrator edited on Supply chain's Configuration page, then the
    setting its pack compiled, then the literal above. The session is the
    caller's own, so the reading is one memoised query on a unit of work
    already open and a number saved on the screen is in force on the next
    reading, with no restart.
    """
    from fsmes.services import plant_settings

    return plant_settings.value(session, SECTION, name, fallback)


@dataclass(frozen=True)
class Policy:
    """What this plant asks of the link to its ERP, as the transport needs it.

    Four of this plant's own settings that a **connector** applies rather than
    a service: which statuses an order may be taken in, when a number the ERP
    read back counts as the number sent, and how long to wait on one request.

    They are carried rather than read where they are used because a connector
    is handed no unit of work on purpose - `integrations/erp/sync.py` keeps
    every database transaction short and never lets one span an HTTP call, and
    an adapter that opened a session of its own to find out its timeout would
    be the first thing to break that. So the sync worker reads them once per
    cycle, in a transaction that is closed before anything is sent, and hands
    the answer over. A number saved on the Configuration page is in force on
    the next cycle: seconds, with no restart and nothing to re-apply.

    Every field defaults to the literal the product ships, so a connector
    nobody configures - a test, a conformance run, `fsmes erp check` - behaves
    exactly as it did before this existed.
    """

    open_statuses: tuple[str, ...] = OPEN_STATUSES
    float_rel_tol: float = FLOAT_REL_TOL
    float_abs_tol: float = FLOAT_ABS_TOL
    http_timeout: float = HTTP_TIMEOUT
    rest_timeout: float = REST_TIMEOUT

    @classmethod
    def from_settings(cls, settings) -> Policy:
        """What the pack compiled, with no database in it.

        The second and third layers only. It is what an adapter starts on the
        moment it is built - before any sync cycle has run, and for the
        commands that build one without a plant behind them (`fsmes erp
        check`, the conformance suite) - so a connector is never briefly
        running on the product's defaults while the plant's own pack said
        otherwise.
        """
        written = str(getattr(settings, "erp_open_statuses", "") or "")
        statuses = tuple(part.strip() for part in written.split(",") if part.strip())
        return cls(
            open_statuses=statuses or OPEN_STATUSES,
            float_rel_tol=float(getattr(settings, "erp_float_rel_tol", FLOAT_REL_TOL)),
            float_abs_tol=float(getattr(settings, "erp_float_abs_tol", FLOAT_ABS_TOL)),
            http_timeout=float(getattr(settings, "erp_http_timeout", HTTP_TIMEOUT)),
            rest_timeout=float(getattr(settings, "erp_rest_timeout", REST_TIMEOUT)),
        )


def policy(session: Session) -> Policy:
    """This plant's ERP policy as it stands right now, in one read."""
    statuses = _number(session, "open_statuses", list(OPEN_STATUSES))
    return Policy(
        open_statuses=tuple(statuses),
        float_rel_tol=float(_number(session, "float_rel_tol", FLOAT_REL_TOL)),
        float_abs_tol=float(_number(session, "float_abs_tol", FLOAT_ABS_TOL)),
        http_timeout=float(_number(session, "http_timeout", HTTP_TIMEOUT)),
        rest_timeout=float(_number(session, "rest_timeout", REST_TIMEOUT)),
    )


def max_attempts(session: Session) -> int:
    """How many times this plant offers one confirmation before it is dead."""
    return int(_number(session, "max_attempts", MAX_ATTEMPTS))


def default_priority(session: Session) -> int:
    """The priority an ERP order that carries none inherits on this plant.

    The ERPNext connector sends no priority at all and says why: inventing one
    at the edge would outrank this plant's own dispatch ordering with a number
    nobody set. This is where the number it gets instead is chosen, and it is
    the plant's.
    """
    return int(_number(session, "default_order_priority", DEFAULT_ORDER_PRIORITY))


# ----------------------------------------------------------------- inbound

def import_order(session: Session, request: ProductionRequest | dict, actor: str = "erp") -> WorkOrder:
    """Create (or update, while still planned) a work order from a request."""
    if isinstance(request, dict):
        try:
            request = ProductionRequest.from_payload(request)
        except ValueError as exc:
            raise Invalid(str(exc)) from exc

    # What the ERP said, or what this plant gives an order the ERP said
    # nothing about. Decided here rather than at the border, because the
    # border's job is to report what the ERP sent and 50 was never the ERP's
    # number - `[erp] default_order_priority` is the plant's.
    priority = (default_priority(session) if request.priority is None
                else request.priority)

    existing = session.scalar(select(WorkOrder).where(WorkOrder.code == request.code))
    if existing is not None:
        if existing.status is OrderStatus.PLANNED:
            before = {"quantity": existing.quantity, "priority": existing.priority}
            existing.quantity = request.quantity or existing.quantity
            existing.priority = priority
            existing.due_date = request.due_date or existing.due_date
            audit.record(session, actor=actor, action="workorder.updated_from_erp",
                         entity_type="workorder", entity_id=request.code, before=before,
                         after={"quantity": existing.quantity, "priority": existing.priority})
        return existing

    return workorders.create(
        session, code=request.code, material_code=request.material, quantity=request.quantity,
        due_date=request.due_date, priority=priority,
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
    return outbox.record(session, kind=confirmation.kind, payload=as_payload(confirmation),
                         message_key=confirmation.message_key)


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
        over_qty=wo.over_qty,
        lot=f"{wo.code}-FG" if wo.good_qty > 0 else None,
        started_at=wo.started_at, completed_at=wo.completed_at))


def pending_outbound(session: Session, now: datetime | None = None) -> list[ErpMessage]:
    """What is due for delivery to the ERP: a kind this contract can parse,
    pending, and not backing off.

    The kind filter is what lets the outbox be a domain event log. Equipment
    state changes, holds and resumes sit in the same table for the namespace
    publisher; they are not addressed to an ERP, so they are never selected
    here, never attempted, and never counted against anyone's retries.
    """
    now = now or utcnow()
    return list(session.scalars(
        select(ErpMessage)
        .where(ErpMessage.direction == MessageDirection.OUT,
               ErpMessage.kind.in_(CONFIRMATION_KINDS),
               ErpMessage.status == MessageStatus.PENDING,
               (ErpMessage.next_attempt_at.is_(None)) | (ErpMessage.next_attempt_at <= now))
        .order_by(ErpMessage.id)))


def backoff_seconds(session: Session, attempts: int) -> int:
    """5 s, 10 s, 20 s ... capped at an hour, unless this plant said otherwise.

    Both numbers are the plant's, read live. An ERP with a four-hour weekly
    maintenance window is why: the doubling reaches the ceiling long before
    the window closes, and a plant that raised the ceiling to six hours wants
    that in force on the confirmation queued a minute later, not at the next
    restart of the sync worker.
    """
    base = int(_number(session, "base_backoff_s", BASE_BACKOFF_SECONDS))
    ceiling = int(_number(session, "max_backoff_s", MAX_BACKOFF_SECONDS))
    return min(base * 2 ** max(0, attempts - 1), ceiling)


def mark_sent(message: ErpMessage) -> None:
    message.status = MessageStatus.SENT
    message.error = None
    message.next_attempt_at = None
    message.processed_at = utcnow()


def mark_error(session: Session, message: ErpMessage, error: Exception,
               now: datetime | None = None) -> ErpMessage:
    """Record the failure, back off, and after enough attempts stop trying.

    Takes the session its caller already has, because how many attempts are
    enough and how long each wait is are this plant's answers and are read
    from its own settings rather than from a constant compiled into the
    product. The sync worker is inside a unit of work at this point already -
    it is writing the message - so this costs no transaction of its own.
    """
    now = now or utcnow()
    message.attempts = (message.attempts or 0) + 1
    message.error = str(error)[:400]
    if message.attempts >= max_attempts(session):
        message.status = MessageStatus.DEAD
        message.next_attempt_at = None
    else:
        message.next_attempt_at = now + timedelta(
            seconds=backoff_seconds(session, message.attempts))
    return message


def mark_refused(message: ErpMessage, error: Exception) -> ErpMessage:
    """The ERP refused this on its own rules. Stop, rather than back off.

    A refusal is not a failure that time repairs. The ERP read the message,
    understood it and said no — measured against ERPNext v15.120.0, where a
    Manufacture entry beyond the site's over-production allowance is refused
    whole and nothing is booked. Retrying sends the identical message and
    gets the identical answer, so eight attempts over an hour only delay the
    moment a person hears about it, and bury the ERP's own words under seven
    copies of themselves.

    Dead is what the outbox already means by "a person decides": the message
    is kept, its error is the ERP's sentence, nothing is delivered, and
    `retry` puts it back once whoever owns the decision has made it.
    """
    message.attempts = (message.attempts or 0) + 1
    message.error = str(error)[:400]
    message.status = MessageStatus.DEAD
    message.next_attempt_at = None
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
    """The queue as a person or an agent needs to see it.

    `counts` is about the ERP and only the ERP. The same table also holds the
    plant events the namespace publisher reads, and counting those as pending
    ERP work would report a backlog that does not exist - "pending: 4000"
    when the ERP is owed nothing is exactly the kind of convincing wrong
    number the house rules exist to stop. They are still stated, as
    `other_outbound` and in `kinds`, so nothing is hidden either.
    """
    erp_rows = (ErpMessage.direction == MessageDirection.OUT,
                ErpMessage.kind.in_(CONFIRMATION_KINDS))
    counts = {status.value: 0 for status in MessageStatus}
    for status, n in session.execute(
            select(ErpMessage.status, func.count()).where(*erp_rows)
            .group_by(ErpMessage.status)):
        counts[status.value] = n
    kinds = {kind: n for kind, n in session.execute(
        select(ErpMessage.kind, func.count()).group_by(ErpMessage.kind))}
    other_outbound = session.scalar(
        select(func.count()).select_from(ErpMessage)
        .where(ErpMessage.direction == MessageDirection.OUT,
               ErpMessage.kind.not_in(CONFIRMATION_KINDS))) or 0
    oldest = session.scalar(select(func.min(ErpMessage.created_at)).where(
        *erp_rows, ErpMessage.status == MessageStatus.PENDING))
    recent = session.scalars(select(ErpMessage).order_by(ErpMessage.id.desc()).limit(limit)).all()
    return {
        "counts": counts,
        # What the ERP sync will never take: the domain events in the same
        # log, waiting for a different reader.
        "other_outbound": other_outbound,
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
