"""Finite-capacity scheduling: one machine, one job at a time, inside shifts.

Forward scheduling from a start you name. Each operation waits for the one
before it on its own order, and for whatever else is already booked on its
machine, and only runs when the plant is actually running. Maintenance the
machine already owes is placed first, because a plan that schedules production
through a service is a prediction that something will go wrong.

Advisory on purpose. It says when work should run; the floor books what
happened. An MES that refuses production because it disagrees with a plan is
an MES people route around.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import (
    MaintenanceOrder,
    MaintenanceStatus,
    ScheduledSlot,
    SlotKind,
    WorkOrder,
)
from fsmes.domain.workorders import OrderStatus
from fsmes.services import Invalid, audit, calendar, masterdata, workorders

# What one unit costs at a station when nothing says. Real cycle times are
# seeded onto the machine from the tag map; this only keeps a plan possible
# for a plant that has not commissioned its machines yet - and a schedule
# built on it is a guess, which `uses_default_cycle` reports rather than
# hides.
DEFAULT_CYCLE_SECONDS = 3.0


def operation_minutes(operation, units: float) -> tuple[float, str]:
    """How long this operation takes for this quantity, and where that came
    from.

    A time-studied route wins: setup happens once and run time scales, which
    is the arithmetic every ERP and every planner already does. Falling back
    to the machine's rated cycle keeps a plant that has not time-studied its
    routes able to plan - and the second return value says which happened,
    because a plan built on a fallback is a guess and a planner is entitled
    to know that before promising a date.
    """
    run = operation.run_seconds_per_unit
    if run:
        setup = float(operation.setup_seconds or 0.0)
        return round((setup + run * units) / 60.0, 2), "routing"
    return round(units * _cycle_seconds(operation) / 60.0, 2), "machine"


def _cycle_seconds(operation) -> float:
    """How long one unit takes at this operation's machine.

    This read `cycle_seconds`, and the column is `ideal_cycle_seconds` - so
    `getattr` returned None every time and every slot in every schedule was
    sized at the three-second fallback regardless of the machine. A
    finite-capacity scheduler built on a constant is a calendar, not a plan,
    and it failed silently because a default is indistinguishable from an
    answer.

    Named directly rather than through `getattr` for exactly that reason: a
    renamed column should now break loudly instead of quietly planning
    fiction.
    """
    equipment = operation.equipment
    value = equipment.ideal_cycle_seconds if equipment is not None else None
    return float(value) if value else DEFAULT_CYCLE_SECONDS


def _busy_until(session: Session, equipment_id: int, after: datetime) -> datetime:
    """When this machine is next free, given what is already booked."""
    latest = after
    for slot in session.scalars(
        select(ScheduledSlot).where(
            ScheduledSlot.equipment_id == equipment_id,
            ScheduledSlot.planned_end > after)
    ):
        latest = max(latest, slot.planned_end)
    return latest


def _place_maintenance(session: Session, equipment_id: int,
                       earliest: datetime) -> datetime:
    """Book the service this machine already owes, before production.

    Returns when production may start. Doing this first is the difference
    between a schedule and a wish: the machine is going to stop for the
    service either way, and only one of the two ways is planned.
    """
    owed = list(session.scalars(select(MaintenanceOrder).where(
        MaintenanceOrder.equipment_id == equipment_id,
        MaintenanceOrder.status.in_(
            (MaintenanceStatus.DUE, MaintenanceStatus.IN_PROGRESS)))))
    cursor = earliest
    for order in owed:
        already = session.scalar(select(ScheduledSlot).where(
            ScheduledSlot.maintenance_order_id == order.id))
        if already is not None:
            cursor = max(cursor, already.planned_end)
            continue

        minutes = order.plan.expected_minutes if order.plan else 60.0
        start = calendar.next_working(session, cursor, equipment_id)
        end = calendar.add_working(session, start, minutes, equipment_id)
        session.add(ScheduledSlot(
            equipment_id=equipment_id, kind=SlotKind.MAINTENANCE,
            maintenance_order_id=order.id, planned_start=start, planned_end=end,
            minutes=minutes, note=order.summary))
        cursor = end
    return cursor


def plan_order(session: Session, code: str, *, start: datetime | None = None,
               actor: str = "system") -> dict:
    """Schedule every operation of one order, in route sequence.

    Re-planning replaces this order's slots rather than adding to them, so a
    planner can run it repeatedly without the board filling with ghosts.
    """
    order = workorders.get(session, code)
    if order.status in (OrderStatus.COMPLETED, OrderStatus.CLOSED,
                        OrderStatus.CANCELLED):
        raise Invalid(f"{code} is {order.status.value}; there is nothing to schedule")
    if order.status is OrderStatus.ON_HOLD:
        # Scheduling around a held order is the point of holding it; a plan
        # that includes it would promise dates the hold exists to suspend.
        raise Invalid(f"{code} is on hold; resume it before scheduling it")
    if not order.operations:
        raise Invalid(f"{code} has no operations to schedule")

    session.execute(delete(ScheduledSlot).where(ScheduledSlot.work_order_id == order.id))
    session.flush()

    cursor = start or utcnow()
    remaining = max(0.0, order.quantity - (order.good_qty or 0.0))
    slots = []

    for index, operation in enumerate(sorted(order.operations, key=lambda o: o.seq)):
        equipment_id = operation.equipment_id
        # The machine is free when its existing bookings end and its owed
        # maintenance is done, whichever is later.
        free = _busy_until(session, equipment_id, cursor)
        free = _place_maintenance(session, equipment_id, free)

        minutes, _basis = operation_minutes(operation, remaining)
        begin = calendar.next_working(session, free, equipment_id)
        finish = calendar.add_working(session, begin, minutes, equipment_id)

        slot = ScheduledSlot(
            equipment_id=equipment_id, kind=SlotKind.PRODUCTION,
            work_order_id=order.id, operation_id=operation.id,
            planned_start=begin, planned_end=finish, minutes=minutes,
            sequence=index, note=operation.name)
        session.add(slot)
        slots.append(slot)
        # The next operation cannot start before this one finishes: a routing
        # is a sequence, not a set.
        cursor = finish

    session.flush()
    audit.record(session, actor=actor, action="order.scheduled",
                 entity_type="workorder", entity_id=code,
                 after={"starts": str(slots[0].planned_start),
                        "finishes": str(slots[-1].planned_end)})
    return _plan_out(order, slots)


def _plan_out(order: WorkOrder, slots: list[ScheduledSlot]) -> dict:
    production = [s for s in slots if s.kind is SlotKind.PRODUCTION]
    return {
        "order": order.code,
        "quantity": order.quantity,
        "remaining": max(0.0, order.quantity - (order.good_qty or 0.0)),
        "starts": production[0].planned_start if production else None,
        "finishes": production[-1].planned_end if production else None,
        "operations": [
            {"seq": s.operation.seq, "operation": s.note,
             "equipment": s.equipment.code,
             "planned_start": s.planned_start, "planned_end": s.planned_end,
             "minutes": s.minutes}
            for s in production
        ],
    }


def plan_all(session: Session, *, start: datetime | None = None,
             actor: str = "system") -> list[dict]:
    """Schedule every open order, highest priority first.

    Priority then order code, so the sequence is stable: a planner running
    this twice must get the same board, or they will stop trusting it.
    """
    orders = session.scalars(
        select(WorkOrder)
        .where(WorkOrder.status.in_((OrderStatus.PLANNED, OrderStatus.RELEASED,
                                     OrderStatus.RUNNING)))
        .order_by(WorkOrder.priority, WorkOrder.code))
    cursor = start or utcnow()
    return [plan_order(session, o.code, start=cursor, actor=actor) for o in orders]


def board(session: Session, *, equipment_code: str | None = None,
          hours: float = 24.0) -> dict:
    """The schedule as a machine-by-machine board.

    What a supervisor looks at: each machine down the side, time across, and
    the maintenance blocks visible alongside the work so the day reads as one
    thing rather than two lists.
    """
    now = utcnow()
    until = now + timedelta(hours=hours)
    query = select(ScheduledSlot).where(
        ScheduledSlot.planned_end > now,
        ScheduledSlot.planned_start < until).order_by(ScheduledSlot.planned_start)
    if equipment_code:
        equipment = masterdata.get_equipment(session, equipment_code)
        query = query.where(ScheduledSlot.equipment_id == equipment.id)

    machines: dict[str, list[dict]] = {}
    for slot in session.scalars(query):
        machines.setdefault(slot.equipment.code, []).append({
            "kind": slot.kind.value,
            "order": slot.work_order.code if slot.work_order else None,
            "seq": slot.operation.seq if slot.operation else None,
            "what": slot.note,
            "planned_start": slot.planned_start,
            "planned_end": slot.planned_end,
            "minutes": slot.minutes,
        })

    booked = sum(s["minutes"] for rows in machines.values() for s in rows)
    return {
        "from": now, "to": until,
        "machines": [{"equipment": code, "slots": rows}
                     for code, rows in sorted(machines.items())],
        "booked_minutes": round(booked, 1),
        "maintenance_minutes": round(sum(
            s["minutes"] for rows in machines.values() for s in rows
            if s["kind"] == "maintenance"), 1),
    }


def promise(session: Session, code: str) -> dict:
    """When this order is expected to finish, and whether that is late.

    A due date the plan disagrees with is the most useful thing a scheduler
    produces, and the thing a spreadsheet never says out loud.
    """
    order = workorders.get(session, code)
    slots = list(session.scalars(select(ScheduledSlot).where(
        ScheduledSlot.work_order_id == order.id,
        ScheduledSlot.kind == SlotKind.PRODUCTION
    ).order_by(ScheduledSlot.planned_end.desc())))
    if not slots:
        return {"order": code, "scheduled": False,
                "note": "not scheduled - run the planner first"}

    finish = slots[0].planned_end
    late = order.due_date is not None and finish > order.due_date
    return {
        "order": code,
        "scheduled": True,
        "finishes": finish,
        "due": order.due_date,
        "late": late,
        "late_by_hours": round((finish - order.due_date).total_seconds() / 3600, 2)
        if late else None,
    }
