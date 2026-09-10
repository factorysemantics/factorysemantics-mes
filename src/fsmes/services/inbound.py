"""Writing what another system told us, without ever pretending we saw it.

One function per event in the inbound contract. Each of them:

1. refuses an event whose supplier or key is missing — without those there
   is no way to tell the second delivery from the first;
2. returns early if `(source, kind, external_key)` has already been
   applied, so the same file dropped twice changes nothing;
3. calls the service that already owns the rule — `equipment.label_stop`,
   `quality.record_check`, `execution.report` — rather than writing rows of
   its own, so an inbound event is held to exactly the rules a typed one is;
4. records the supplying system's name on whatever it wrote.

What it will not do is fill a gap. A label for a stop this MES never saw is
not turned into a stop; a reading with no spec here is not measured against
a spec invented for it; a count is booked, but never against an order
guessed from context. Every one of those comes back as a refusal with the
reason in words, and the driver puts it in the rejects report.

Nothing here reaches outside this database. Inbound is not gated by shadow
mode, and must not be: shadow mode closes the paths by which this MES could
change something *outside* itself, and being told things is the opposite
direction. A shadow that stopped listening would be comparing itself with
the incumbent on half the evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.domain import (
    CheckResult,
    Gauge,
    InboundEvent,
    InboundKind,
    OperationStatus,
    OrderStatus,
    ProductionSource,
    WorkOrder,
    WorkOrderOperation,
)
from fsmes.integrations.inbound.contract import DowntimeLabel, ManualCount, QualityResult
from fsmes.services import Conflict, MesError, equipment, execution, masterdata, quality, workorders


@dataclass(frozen=True)
class Outcome:
    """What became of one inbound event, in words a rejects report can print."""

    kind: InboundKind
    source: str
    external_key: str
    #: True when this delivery changed something. False for a duplicate.
    applied: bool
    #: True when the event was already applied by an earlier delivery.
    duplicate: bool
    detail: str
    entity_type: str | None = None
    entity_id: str | None = None


class Refused(MesError):
    """This MES will not record the event, and says why in one sentence.

    Not an error in the driver and not a bug in the supplier — usually it is
    the honest answer that the two systems disagree about what exists. The
    row goes to the rejects report and the file stays on disk, so the same
    event can be delivered again once whatever is missing is there.
    """


# ------------------------------------------------------------------ ledger


def already_applied(session: Session, *, source: str, kind: InboundKind,
                    external_key: str) -> InboundEvent | None:
    return session.scalar(
        select(InboundEvent).where(
            InboundEvent.source == source,
            InboundEvent.kind == kind,
            InboundEvent.external_key == external_key,
        )
    )


def _remember(session: Session, *, event, kind: InboundKind, detail: str,
              entity_type: str | None = None, entity_id: str | None = None) -> Outcome:
    session.add(InboundEvent(
        source=event.source,
        kind=kind,
        external_key=event.external_key,
        recorded_at=event.recorded_at,
        entity_type=entity_type,
        entity_id=entity_id,
        detail=detail[:300],
    ))
    session.flush()
    return Outcome(kind=kind, source=event.source, external_key=event.external_key,
                   applied=True, duplicate=False, detail=detail,
                   entity_type=entity_type, entity_id=entity_id)


def _seen(existing: InboundEvent) -> Outcome:
    return Outcome(kind=existing.kind, source=existing.source, external_key=existing.external_key,
                   applied=False, duplicate=True,
                   detail=existing.detail or "already recorded",
                   entity_type=existing.entity_type, entity_id=existing.entity_id)


# ------------------------------------------------------------------ writers


def record_downtime_label(session: Session, label: DowntimeLabel) -> Outcome:
    """Put a supplied reason code on the stops this MES already observed."""
    seen = already_applied(session, source=label.source, kind=InboundKind.DOWNTIME_LABEL,
                           external_key=label.external_key)
    if seen is not None:
        return _seen(seen)

    try:
        machine = masterdata.get_equipment(session, label.equipment)
    except MesError as exc:
        raise Refused(str(exc)) from exc

    labelled = equipment.label_stop(
        session,
        equipment_code=machine.code,
        start=label.started_at,
        end=label.ended_at,
        reason=label.reason,
        source=label.source,
        actor=label.source,
    )
    if not labelled:
        window = label.started_at.isoformat()
        window += f" to {label.ended_at.isoformat()}" if label.ended_at else " (still open)"
        raise Refused(
            f"this MES observed no unlabelled stop on {machine.code} in {window}; the label was "
            "not attached and no stop was created for it"
        )
    seconds = sum(
        ((interval.ended_at or label.ended_at or interval.started_at) - interval.started_at).total_seconds()
        for interval in labelled
    )
    detail = (f"labelled {len(labelled)} observed stop"
              f"{'' if len(labelled) == 1 else 's'} on {machine.code} "
              f"({round(seconds)}s) as {label.reason!r}")
    return _remember(session, event=label, kind=InboundKind.DOWNTIME_LABEL, detail=detail,
                     entity_type="equipment", entity_id=machine.code)


def record_quality_result(session: Session, result: QualityResult) -> Outcome:
    """Record a measurement taken elsewhere, against this MES's own spec.

    The verdict stored in `result` is always this MES's, computed from this
    MES's limits. When the supplier sent a verdict of its own it is kept
    beside ours in `supplied_result`, because the two disagreeing is a
    finding about the two systems and throwing one away would hide it.
    """
    seen = already_applied(session, source=result.source, kind=InboundKind.QUALITY_RESULT,
                           external_key=result.external_key)
    if seen is not None:
        return _seen(seen)

    order_code, material_code = _what_was_measured(session, result)
    try:
        check, nc = quality.record_check(
            session,
            material_code=material_code,
            characteristic=result.characteristic,
            value=result.value,
            work_order_code=order_code,
            actor=result.inspector or result.source,
        )
    except MesError as exc:
        raise Refused(str(exc)) from exc

    check.source_system = result.source[:80]
    if result.passed is not None:
        check.supplied_result = CheckResult.PASS if result.passed else CheckResult.FAIL
    notes = []
    if result.gauge:
        gauge = session.scalar(select(Gauge).where(Gauge.code == result.gauge))
        if gauge is None:
            # Not an error and not silently dropped: this MES does not know
            # that instrument, and inventing a gauge record for it would make
            # an uncalibrated reading look traceable.
            notes.append(f"gauge {result.gauge!r} is not one this MES knows, so the reading is untraceable here")
        else:
            check.gauge_id = gauge.id
    if check.supplied_result is not None and check.supplied_result is not check.result:
        notes.append(f"{result.source} called it {check.supplied_result.value}, this MES calls it "
                     f"{check.result.value} against its own spec")
    session.flush()

    detail = f"{result.characteristic}={result.value} on {material_code} recorded as {check.result.value}"
    if nc is not None:
        detail += f", opening {nc.code}"
    if notes:
        detail += " — " + "; ".join(notes)
    return _remember(session, event=result, kind=InboundKind.QUALITY_RESULT, detail=detail,
                     entity_type="qualitycheck", entity_id=str(check.id))


def _what_was_measured(session: Session, result: QualityResult) -> tuple[str | None, str]:
    """Which order and material a supplied reading belongs to.

    An order named outright settles it. A machine named on its own is
    resolved through the operation running there, because that is the only
    thing in this MES that says what that machine is making. When nothing
    is running there, the reading is refused rather than attached to the
    last order that happened to use the machine.
    """
    if result.order:
        try:
            wo = workorders.get(session, result.order)
        except MesError as exc:
            raise Refused(str(exc)) from exc
        return wo.code, wo.material.code

    try:
        machine = masterdata.get_equipment(session, result.equipment)
    except MesError as exc:
        raise Refused(str(exc)) from exc
    op = _open_operation_on(session, machine.id)
    if op is None:
        raise Refused(
            f"the result names {machine.code} and no order, and this MES has no operation open "
            "there; which material was measured is unknown and is not guessed"
        )
    return op.order.code, op.order.material.code


def _open_operation_on(session: Session, equipment_id: int) -> WorkOrderOperation | None:
    """The operation this MES believes is running on a machine, if any."""
    return session.scalars(
        select(WorkOrderOperation)
        .join(WorkOrder)
        .where(
            WorkOrderOperation.equipment_id == equipment_id,
            WorkOrderOperation.status != OperationStatus.DONE,
            WorkOrder.status.in_((OrderStatus.RELEASED, OrderStatus.RUNNING)),
        )
        .order_by(WorkOrder.priority, WorkOrder.id, WorkOrderOperation.seq)
    ).first()


def record_manual_count(session: Session, count: ManualCount) -> Outcome:
    """Book units somebody counted into another system.

    Booked against an open operation when there is one, and kept as
    unassigned production against the machine when there is not — the one
    case where a count with no operation open is not an error, because the
    system that took the count had the order and this one does not.
    """
    seen = already_applied(session, source=count.source, kind=InboundKind.MANUAL_COUNT,
                           external_key=count.external_key)
    if seen is not None:
        return _seen(seen)

    try:
        machine = masterdata.get_equipment(session, count.equipment)
    except MesError as exc:
        raise Refused(str(exc)) from exc

    if count.good == count.scrap == 0:
        # Nothing to book, and still worth remembering: a supplier that sent
        # a zero row sent it, and the next delivery of the same row must not
        # be treated as new.
        detail = f"a count of nothing on {machine.code}; recorded as told, nothing booked"
        return _remember(session, event=count, kind=InboundKind.MANUAL_COUNT, detail=detail,
                         entity_type="equipment", entity_id=machine.code)

    booked = dict(good=count.good, scrap=count.scrap, source=ProductionSource.EXTERNAL,
                  source_system=count.source, actor=count.source, ts=count.made_at)
    note = ""
    op = None
    if count.order:
        try:
            workorders.get(session, count.order)
            op = execution.report(session, order_code=count.order, **booked)
        except Conflict as exc:
            # The supplier had that order open; this MES does not, because
            # its own copy is finished, held, or was never released. The
            # units were still made, so they are kept against the machine
            # rather than refused - and the disagreement is written down.
            note = f" — {count.order} is not open here ({exc}), so the order was not used"
        except MesError as exc:
            raise Refused(str(exc)) from exc
    if op is None and count.order and not note:
        note = f" — {count.order} had no operation to book against"
    if op is None:
        try:
            op = execution.report(session, equipment_code=machine.code, **booked)
        except MesError as exc:
            raise Refused(str(exc)) from exc
    if op is None:
        detail = (f"{count.good} good, {count.scrap} scrap on {machine.code} with no operation open "
                  f"here; kept as unassigned production{note}")
        return _remember(session, event=count, kind=InboundKind.MANUAL_COUNT, detail=detail,
                         entity_type="equipment", entity_id=machine.code)
    detail = (f"{count.good} good, {count.scrap} scrap booked to {op.order.code} "
              f"operation {op.seq}{note}")
    return _remember(session, event=count, kind=InboundKind.MANUAL_COUNT, detail=detail,
                     entity_type="workorder", entity_id=op.order.code)


#: The writer for each stream name the drivers configure.
WRITERS = {
    "downtime": record_downtime_label,
    "quality": record_quality_result,
    "counts": record_manual_count,
}
