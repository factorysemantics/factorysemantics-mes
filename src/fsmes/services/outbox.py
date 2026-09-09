"""The outbox, writing side: one event per fact, in the fact's own transaction.

`erp_messages` is the one event stream this MES produces. Two readers take
from it and neither owns it: the ERP sync delivers the confirmation kinds
its contract can parse, and the unified-namespace publisher relays every
outbound kind to MQTT. That is why the sync now selects by kind - a kind it
cannot parse is not its message - and why this module is the single place a
new kind is written.

The rule that makes the log trustworthy: **one write, in the transaction
that wrote the fact.** An event that can exist without its fact, or a fact
without its event, is a discrepancy a consumer has no way to detect. So
these functions take the caller's session and add to it; they never commit,
never open a session of their own, and never talk to a broker.

The table keeps its ERP-facing name. Renaming it would churn every caller
and every migration for a word, and the direction and kind columns already
say what a row is.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.config import Settings, get_settings
from fsmes.domain import (
    Equipment,
    EquipmentState,
    ErpMessage,
    MessageDirection,
    OrderStatus,
    WorkOrder,
)
from fsmes.integrations.events import (
    DomainEvent,
    EquipmentStateChange,
    OrderHold,
    OrderResume,
    as_payload,
)
from fsmes.services import masterdata


def record(session: Session, *, kind: str, payload: dict,
           message_key: str | None = None) -> ErpMessage:
    """Put one outbound event in the log. Idempotent by message key: the same
    event queued twice is one message."""
    if message_key is not None:
        existing = session.scalar(select(ErpMessage).where(ErpMessage.message_key == message_key))
        if existing is not None:
            return existing
    message = ErpMessage(direction=MessageDirection.OUT, kind=kind, payload=payload,
                         message_key=message_key)
    session.add(message)
    session.flush()
    return message


def _record_event(session: Session, event: DomainEvent) -> ErpMessage:
    return record(session, kind=event.kind, payload=as_payload(event),
                  message_key=event.message_key)


def domain_events_enabled(settings: Settings | None = None) -> bool:
    """Whether plant events other than ERP confirmations are logged.

    On by default, because an MES that says it publishes the plant and
    publishes two kinds of message is not telling the truth. A plant with no
    broker and no other reader can turn it off and keep the log to what the
    ERP is owed; what it gives up is written down in `docs/operate/uns.md`.
    """
    return (settings or get_settings()).outbox_domain_events


# ------------------------------------------------------------------- events

def equipment_state_changed(session: Session, *, equipment: Equipment,
                            opened: EquipmentState, closed: EquipmentState | None,
                            actor: str, settings: Settings | None = None) -> ErpMessage | None:
    """A machine changed state. `closed` is the interval this one ended, if
    the MES had one - the first state a machine is ever seen in closes
    nothing, and that is reported as null rather than as a zero-length run.

    Returns None when domain events are switched off.
    """
    if not domain_events_enabled(settings):
        return None
    line = masterdata.work_center_of(session, equipment)
    previous_seconds = None
    if closed is not None and closed.ended_at is not None:
        previous_seconds = round((closed.ended_at - closed.started_at).total_seconds(), 3)
    return _record_event(session, EquipmentStateChange(
        # The state interval's own id: the same change can never be logged
        # twice, and the row it came from can be found again.
        message_key=f"equipment:{equipment.code}:state:{opened.id}",
        equipment=equipment.code,
        work_center=line.code if line is not None else None,
        state=opened.state.value,
        reason=opened.reason,
        previous_state=closed.state.value if closed is not None else None,
        previous_seconds=previous_seconds,
        started_at=opened.started_at,
        actor=str(actor),
    ))


def order_held(session: Session, *, order: WorkOrder, reason: str, previous_status: OrderStatus,
               at: datetime, actor: str, settings: Settings | None = None) -> ErpMessage | None:
    if not domain_events_enabled(settings):
        return None
    return _record_event(session, OrderHold(
        message_key=_moment_key(order.code, "hold", at),
        order=order.code, erp_reference=order.erp_reference, material=order.material.code,
        reason=reason, previous_status=previous_status.value, at=at, actor=str(actor)))


def order_resumed(session: Session, *, order: WorkOrder, previous_status: OrderStatus,
                  at: datetime, actor: str, settings: Settings | None = None) -> ErpMessage | None:
    if not domain_events_enabled(settings):
        return None
    return _record_event(session, OrderResume(
        message_key=_moment_key(order.code, "resume", at),
        order=order.code, erp_reference=order.erp_reference, material=order.material.code,
        previous_status=previous_status.value, status=order.status.value, at=at, actor=str(actor)))


def _moment_key(code: str, event: str, at: datetime) -> str:
    """A hold is a moment, not a state: the same order can be held again next
    week and that is a second event. The moment is the key."""
    return f"{code}:{event}:{at:%Y%m%dT%H%M%S.%f}"
