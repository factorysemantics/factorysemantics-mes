"""When maintenance is due, and what it costs when it happens.

Due-ness is computed from what the machine has actually done, not from a
calendar: runtime accumulated from its own state history, or units it has
produced. A plan written in days over-maintains the machine that sat idle and
under-maintains the one that ran two shifts, which is the whole reason a plant
buys an MES rather than keeping a spreadsheet.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import (
    EquipmentState,
    MaintenanceKind,
    MaintenanceOrder,
    MaintenancePlan,
    MaintenanceStatus,
    ProductionLog,
    TriggerKind,
)
from fsmes.domain.equipment import EquipmentStateName
from fsmes.services import Conflict, Invalid, NotFound, audit, masterdata


def runtime_hours(session: Session, equipment_id: int, since=None) -> float:
    """Hours this machine has actually run, from its own state history.

    Only RUNNING counts. Idle, starved, blocked and down are not use, and a
    maintenance interval that counted them would come due on a machine that
    has been sitting still.
    """
    query = select(EquipmentState).where(
        EquipmentState.equipment_id == equipment_id,
        EquipmentState.state == EquipmentStateName.RUNNING)
    if since is not None:
        query = query.where(EquipmentState.started_at >= since)

    now = utcnow()
    seconds = 0.0
    for state in session.scalars(query):
        ended = state.ended_at or now
        seconds += max(0.0, (ended - state.started_at).total_seconds())
    return round(seconds / 3600.0, 3)


def produced_qty(session: Session, equipment_id: int, since=None) -> float:
    query = select(func.coalesce(func.sum(ProductionLog.good_qty), 0.0)).where(
        ProductionLog.equipment_id == equipment_id)
    if since is not None:
        query = query.where(ProductionLog.ts >= since)
    return float(session.scalar(query) or 0.0)


def status_of(session: Session, plan: MaintenancePlan) -> dict:
    """How far through its interval a plan is, in its own units."""
    if plan.trigger is TriggerKind.RUNTIME_HOURS:
        used = runtime_hours(session, plan.equipment_id, plan.last_done_at)
        unit = "h run"
    elif plan.trigger is TriggerKind.PRODUCED_QTY:
        used = produced_qty(session, plan.equipment_id, plan.last_done_at)
        unit = "units made"
    else:
        # Calendar plans count from the last service. A plan on a machine
        # nobody has serviced yet is due now - which is correct, and better
        # than inventing a start date the plant never recorded. Reported as
        # exactly the interval so the reason reads honestly rather than as
        # some fabricated elapsed time.
        if plan.last_done_at is None:
            used, unit = plan.interval, "days (never serviced)"
        else:
            elapsed = (utcnow() - plan.last_done_at).total_seconds() / 86400.0
            used, unit = round(elapsed, 2), "days"

    fraction = used / plan.interval if plan.interval else 1.0
    return {
        "plan": plan.code,
        "name": plan.name,
        "equipment": plan.equipment.code,
        "trigger": plan.trigger.value,
        "interval": plan.interval,
        "used": round(used, 2),
        "unit": unit,
        "remaining": round(max(0.0, plan.interval - used), 2),
        "fraction": round(fraction, 3),
        "due": fraction >= 1.0,
        # A plant wants warning, not a surprise. Anything past 80% is worth
        # putting on a shift plan even though it is not due yet.
        "due_soon": 0.8 <= fraction < 1.0,
        "expected_minutes": plan.expected_minutes,
        "document": plan.document_code,
        "last_done_at": plan.last_done_at,
    }


def due(session: Session, include_soon: bool = True) -> list[dict]:
    """Every active plan that is due, worst first."""
    rows = [status_of(session, p) for p in session.scalars(
        select(MaintenancePlan).where(MaintenancePlan.active.is_(True)))]
    wanted = [r for r in rows if r["due"] or (include_soon and r["due_soon"])]
    return sorted(wanted, key=lambda r: r["fraction"], reverse=True)


def _next_code(session: Session, prefix: str) -> str:
    count = session.scalar(select(func.count()).select_from(MaintenanceOrder)) or 0
    return f"{prefix}-{count + 1:05d}"


def raise_due(session: Session, actor: str = "system") -> list[MaintenanceOrder]:
    """Raise a maintenance order for every plan that has come due.

    Idempotent: a plan with work already open does not get a second order.
    Otherwise a nightly sweep would bury the floor in duplicates of the same
    job, which is how people learn to ignore a maintenance list.
    """
    raised = []
    for row in due(session, include_soon=False):
        plan = session.scalar(select(MaintenancePlan).where(
            MaintenancePlan.code == row["plan"]))
        open_already = session.scalar(
            select(MaintenanceOrder).where(
                MaintenanceOrder.plan_id == plan.id,
                MaintenanceOrder.status.in_(
                    (MaintenanceStatus.DUE, MaintenanceStatus.IN_PROGRESS))))
        if open_already is not None:
            continue

        order = MaintenanceOrder(
            code=_next_code(session, "PM"),
            equipment_id=plan.equipment_id,
            plan_id=plan.id,
            kind=MaintenanceKind.PREVENTIVE,
            summary=plan.name,
            reason=f"{row['used']} {row['unit']} against a {plan.interval} "
                   f"{row['unit']} plan",
        )
        session.add(order)
        session.flush()
        audit.record(session, actor=actor, action="maintenance.raised",
                     entity_type="equipment", entity_id=plan.equipment.code,
                     after={"order": order.code, "plan": plan.code,
                            "reason": order.reason})
        raised.append(order)
    return raised


def raise_corrective(session: Session, *, equipment_code: str, summary: str,
                     reason: str | None = None, actor: str = "system") -> MaintenanceOrder:
    """Work raised because something broke, not because a plan came due."""
    equipment = masterdata.get_equipment(session, equipment_code)
    order = MaintenanceOrder(
        code=_next_code(session, "CM"),
        equipment_id=equipment.id,
        kind=MaintenanceKind.CORRECTIVE,
        summary=summary,
        reason=reason,
    )
    session.add(order)
    session.flush()
    audit.record(session, actor=actor, action="maintenance.raised",
                 entity_type="equipment", entity_id=equipment_code,
                 after={"order": order.code, "kind": "corrective", "summary": summary})
    return order


def get(session: Session, code: str) -> MaintenanceOrder:
    order = session.scalar(select(MaintenanceOrder).where(MaintenanceOrder.code == code))
    if order is None:
        raise NotFound(f"no maintenance order {code}")
    return order


def start(session: Session, code: str, actor: str = "system") -> MaintenanceOrder:
    order = get(session, code)
    if order.status is not MaintenanceStatus.DUE:
        raise Conflict(f"{code} is {order.status.value}")
    order.status = MaintenanceStatus.IN_PROGRESS
    order.started_at = utcnow()
    order.performed_by = actor
    session.flush()
    audit.record(session, actor=actor, action="maintenance.started",
                 entity_type="equipment", entity_id=order.equipment.code,
                 after={"order": code})
    return order


def complete(session: Session, code: str, *, findings: str | None = None,
             actor: str = "system") -> MaintenanceOrder:
    """Close the job and re-baseline its plan.

    Re-baselining is the point: the next interval counts from the work that
    was actually done, not from when it was scheduled. A plan that keeps
    counting from its original date drifts a little further out of step every
    cycle.
    """
    order = get(session, code)
    if order.status is MaintenanceStatus.DONE:
        raise Conflict(f"{code} is already done")

    now = utcnow()
    order.status = MaintenanceStatus.DONE
    order.completed_at = now
    order.performed_by = actor
    order.findings = findings
    if order.started_at:
        order.downtime_minutes = round(
            (now - order.started_at).total_seconds() / 60.0, 1)

    if order.plan is not None:
        plan = order.plan
        plan.last_done_at = now
        plan.last_done_runtime_hours = runtime_hours(session, plan.equipment_id)
        plan.last_done_qty = produced_qty(session, plan.equipment_id)

    session.flush()
    audit.record(session, actor=actor, action="maintenance.completed",
                 entity_type="equipment", entity_id=order.equipment.code,
                 after={"order": code, "findings": findings,
                        "downtime_minutes": order.downtime_minutes})
    return order


def create_plan(session: Session, *, code: str, name: str, equipment_code: str,
                trigger: str, interval: float, expected_minutes: float = 30.0,
                instructions: str | None = None, document_code: str | None = None,
                actor: str = "system") -> MaintenancePlan:
    if session.scalar(select(MaintenancePlan).where(MaintenancePlan.code == code)):
        raise Conflict(f"plan {code} already exists")
    if interval <= 0:
        raise Invalid("interval must be positive")
    try:
        kind = TriggerKind(trigger)
    except ValueError as exc:
        raise Invalid(
            f"unknown trigger {trigger!r}. Expected one of "
            f"{', '.join(t.value for t in TriggerKind)}") from exc

    equipment = masterdata.get_equipment(session, equipment_code)
    plan = MaintenancePlan(
        code=code, name=name, equipment_id=equipment.id, trigger=kind,
        interval=interval, expected_minutes=expected_minutes,
        instructions=instructions, document_code=document_code)
    session.add(plan)
    session.flush()
    audit.record(session, actor=actor, action="maintenance.plan_created",
                 entity_type="equipment", entity_id=equipment_code,
                 after={"plan": code, "trigger": kind.value, "interval": interval})
    return plan


def history(session: Session, equipment_code: str | None = None, limit: int = 50, *,
            status: list[str] | None = None, kind: str | None = None, q: str | None = None,
            offset: int = 0) -> tuple[list[MaintenanceOrder], int]:
    """One page of maintenance orders, newest first, and how many match."""
    query = select(MaintenanceOrder).order_by(MaintenanceOrder.id.desc())
    if equipment_code:
        equipment = masterdata.get_equipment(session, equipment_code)
        query = query.where(MaintenanceOrder.equipment_id == equipment.id)
    if status:
        query = query.where(MaintenanceOrder.status.in_([MaintenanceStatus(s) for s in status]))
    if kind:
        query = query.where(MaintenanceOrder.kind == MaintenanceKind(kind))
    if q:
        like = f"%{q}%"
        query = query.where(MaintenanceOrder.code.ilike(like) | MaintenanceOrder.summary.ilike(like)
                            | MaintenanceOrder.findings.ilike(like))
    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    return list(session.scalars(query.limit(limit).offset(offset))), total


def backlog(session: Session) -> dict:
    """What is open, and what it will cost to clear.

    The cost line matters: a maintenance list without its downtime is a wish,
    and a supervisor deciding whether tonight is the night needs the minutes.
    """
    open_orders = list(session.scalars(select(MaintenanceOrder).where(
        MaintenanceOrder.status.in_(
            (MaintenanceStatus.DUE, MaintenanceStatus.IN_PROGRESS)))))
    minutes = sum(
        (o.plan.expected_minutes if o.plan else 60.0) for o in open_orders)
    overdue = [o for o in open_orders if o.kind is MaintenanceKind.PREVENTIVE]
    return {
        "open": len(open_orders),
        "preventive": len(overdue),
        "corrective": len(open_orders) - len(overdue),
        "expected_downtime_minutes": round(minutes, 1),
        "expected_downtime_hours": round(minutes / 60.0, 2),
    }
