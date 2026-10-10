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
    OPEN_STATUSES,
    EquipmentState,
    MaintenanceKind,
    MaintenanceOrder,
    MaintenancePlan,
    MaintenanceStatus,
    MaintenanceWindow,
    ProductionLog,
    TriggerKind,
)
from fsmes.domain.equipment import EquipmentStateName
from fsmes.services import (
    Conflict,
    Forbidden,
    Invalid,
    NotFound,
    audit,
    masterdata,
    plant_settings,
)

# What this plant assumes when nothing has told it otherwise. These stay as
# the literals the product ships, and every one of them is still a judgment
# rather than arithmetic - what changed on 2026-09-25 is whose judgment.
# `plant_settings.value` reads the row Engineering's Configuration page wrote
# first, the setting this plant's pack compiled second, and one of these third,
# so a plant that has configured nothing behaves exactly as it did before any
# of them was a key.

# A plant wants warning, not a surprise. Anything past 80% of a plan's own
# interval is worth putting on a shift plan even though it is not due yet -
# and how much warning is worth having depends on how long a spare takes to
# arrive, which is why it is the plant's number and not this module's.
DUE_SOON_FRACTION = 0.8
# What a corrective job with no plan behind it is assumed to take. It sizes the
# backlog's downtime figure, so a supervisor deciding whether tonight is the
# night is reading it.
DEFAULT_JOB_MINUTES = 60.0
# The house default a new plan inherits. Each plan's own figure is the
# engineer's and is untouched by this.
PLAN_DEFAULT_MINUTES = 30.0


def due_soon_fraction(session: Session) -> float:
    """How far through a plan's own interval this plant calls coming due.

    A fraction rather than a number of hours, so it already scales from a
    weekly filter change to an annual overhaul; what differs between plants is
    how long a spare part takes to arrive.
    """
    return float(plant_settings.value(
        session, "process", "maintenance_due_soon_fraction", DUE_SOON_FRACTION))


def default_job_minutes(session: Session) -> float:
    """How long this plant assumes a job with no plan behind it takes.

    It sizes the backlog's downtime figure and the block the scheduler
    reserves, so a supervisor deciding whether tonight is the night is reading
    it.
    """
    return float(plant_settings.value(
        session, "process", "default_job_minutes", DEFAULT_JOB_MINUTES))


def plan_default_minutes(session: Session) -> float:
    """The expected duration a new plan inherits when nobody says. Each plan's
    own figure is the engineer's and is untouched by this."""
    return float(plant_settings.value(
        session, "process", "maintenance_plan_default_minutes", PLAN_DEFAULT_MINUTES))


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
        unit = "days"
        if plan.last_done_at is None:
            used = plan.interval
        else:
            elapsed = (utcnow() - plan.last_done_at).total_seconds() / 86400.0
            used = round(elapsed, 2)

    fraction = used / plan.interval if plan.interval else 1.0
    soon = due_soon_fraction(session)
    return {
        "plan": plan.code,
        "name": plan.name,
        "equipment": plan.equipment.code,
        "trigger": plan.trigger.value,
        "interval": plan.interval,
        "used": round(used, 2),
        "unit": unit,
        # Whether `used` is something the plant measured or the honest reading
        # of a plan nobody has ever serviced. It used to be folded into the
        # unit itself - "14.0 days (never serviced)" - and the due sentence,
        # which says the unit twice, then read "14.0 days (never serviced)
        # against a 14.0 days (never serviced) plan". Seen on the lab,
        # 2026-10-09: the same clause twice, and a supervisor reading it twice
        # to check it was not two different numbers.
        "never_serviced": plan.trigger is TriggerKind.CALENDAR_DAYS
                          and plan.last_done_at is None,
        "remaining": round(max(0.0, plan.interval - used), 2),
        "fraction": round(fraction, 3),
        "due": fraction >= 1.0,
        # A plant wants warning, not a surprise. Anything past this plant's own
        # fraction of the plan's interval is worth putting on a shift plan even
        # though it is not due yet - and the fraction is sent with the row, so
        # the screen that colours the bar amber reads this plant's number
        # rather than keeping a copy of the product's.
        "due_soon": soon <= fraction < 1.0,
        "due_soon_fraction": soon,
        "expected_minutes": plan.expected_minutes,
        # What the job needs of the line. A planner reading a due list wants
        # both halves of the cost: how long the job takes, and whether the
        # line has to stand still for it.
        "needs_stop": bool(plan.needs_stop),
        "window": plan.window.value if plan.window else None,
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


def _window(given: str | None) -> MaintenanceWindow | None:
    """The window a caller asked for, or nothing if they did not say.

    An empty string is treated as not said rather than as a bad value: the
    pack reader and a form both hand over "" for a field nobody filled in, and
    refusing that would make "I have no opinion about when" impossible to
    express through either.
    """
    if given in (None, ""):
        return None
    try:
        return MaintenanceWindow(str(given).strip().lower())
    except ValueError as exc:
        raise Invalid(
            f"unknown window {given!r}. Expected one of "
            f"{', '.join(w.value for w in MaintenanceWindow)}") from exc


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
                MaintenanceOrder.status.in_(OPEN_STATUSES)))
        if open_already is not None:
            continue

        order = MaintenanceOrder(
            code=_next_code(session, "PM"),
            equipment_id=plan.equipment_id,
            plan_id=plan.id,
            kind=MaintenanceKind.PREVENTIVE,
            summary=plan.name,
            reason=(f"{row['used']} {row['unit']} against a {plan.interval} "
                    f"{row['unit']} plan"
                    + (" — never serviced" if row["never_serviced"] else "")),
            # Copied, not read through the relationship: the plan may be
            # re-written tomorrow and this order is a record of what was asked
            # for today. A plan that never said which trade leaves both null,
            # which is work anybody on shift can take.
            skill_code=plan.skill_code,
            priority=plan.priority,
            # And what the job needs of the line, for the same reason: an
            # order is judged on what was asked for when it was raised. A
            # plan that never said when its job may be done leaves `window`
            # null on the order too, which the floor reads as open - the
            # behaviour every plant had before the column existed.
            needs_stop=bool(plan.needs_stop),
            window=plan.window,
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
                     reason: str | None = None, skill_code: str | None = None,
                     priority: int | None = None, needs_stop: bool = False,
                     window: str | None = None,
                     actor: str = "system") -> MaintenanceOrder:
    """Work raised because something broke, not because a plan came due.

    It says its own trade and its own priority, because there is no plan behind
    it to copy from: the thing that broke is what says which trade and how
    badly it matters. Saying neither is allowed and means what it says - work
    anybody on shift can take, treated as routine.
    """
    equipment = masterdata.get_equipment(session, equipment_code)
    if skill_code:
        # Imported here and not at the top: `dispatch` reads this module for
        # how long a job takes, so one of the two has to ask for the other
        # late. A 404 rather than an order naming a trade this plant has never
        # heard of, which would then dispatch to nobody for ever.
        from fsmes.services import dispatch
        dispatch.get_skill(session, skill_code)
    if priority is not None and not 1 <= int(priority) <= 3:
        raise Invalid("priority is 1 (safety), 2 (production-critical) or 3 (routine)")
    order = MaintenanceOrder(
        code=_next_code(session, "CM"),
        equipment_id=equipment.id,
        kind=MaintenanceKind.CORRECTIVE,
        summary=summary,
        reason=reason,
        skill_code=skill_code,
        priority=int(priority) if priority is not None else None,
        needs_stop=bool(needs_stop),
        window=_window(window),
    )
    session.add(order)
    session.flush()
    audit.record(session, actor=actor, action="maintenance.raised",
                 entity_type="equipment", entity_id=equipment_code,
                 after={"order": order.code, "kind": "corrective", "summary": summary,
                        "skill": skill_code, "priority": order.priority,
                        "needs_stop": order.needs_stop,
                        "window": order.window.value if order.window else None})
    return order


def get(session: Session, code: str) -> MaintenanceOrder:
    order = session.scalar(select(MaintenanceOrder).where(MaintenanceOrder.code == code))
    if order is None:
        raise NotFound(f"no maintenance order {code}")
    return order


def whose_work(session: Session, order: MaintenanceOrder, actor: str,
               performed_by: str | None,
               capabilities: set[str] | None = None) -> str:
    """Whose work this is, and whether this caller may record it.

    An order that has been given to somebody is *their* work. Anyone else is
    refused, and told who can - because an order worked by somebody it was not
    given to is an order the supervisor's board is lying about, and the board
    is the whole reason the dispatcher exists.

    There are two honest ways past that, and they are different from each
    other. Somebody who holds `maintenance.plan` is not refused at all: a
    supervisor can reassign the order and then start it, and making them do it
    in two steps for no reason is how a plant ends up with a supervisor account
    nobody uses. And *anybody* who holds `maintenance.perform` may record the
    work **against the person it was given to**, by naming them in
    `performed_by` - which is the shop-floor terminal beside the machine, the
    supervisor booking his crew's jobs at the end of the shift, and the only
    way a plant whose mechanics do not each have a login can have its records
    name the mechanic. What is still refused is taking somebody else's work
    *for yourself*: naming a third person, or naming nobody while not being the
    assignee. You may book Mary's job as Mary's; you may not book it as yours.

    Returns the person's code. The caller keeps its own identity for the audit
    row: `MT-05 started PM-…` is the record of the work, and who typed it in
    is a separate fact that the audit trail keeps separately.
    """
    whose = str(performed_by or actor).strip().upper()
    if performed_by:
        # 404 rather than a board showing a name that is nobody. A person
        # handed somebody else's plant's code would otherwise sit on the
        # supervisor's page for ever with no way to reach them.
        whose = masterdata.get_person(session, whose).code
    if (order.assigned_to
            and whose.upper() != order.assigned_to.upper()
            and "maintenance.plan" not in (capabilities or set())):
        raise Forbidden(
            f"{order.code} is assigned to {order.assigned_to}. "
            f"{order.assigned_to} can start it, anybody can record it as "
            f"{order.assigned_to}'s work with performed_by, or somebody who "
            "holds 'maintenance.plan' can reassign it first.")
    return whose


def start(session: Session, code: str, actor: str = "system",
          capabilities: set[str] | None = None,
          performed_by: str | None = None) -> MaintenanceOrder:
    """Somebody picks up the spanner.

    Whose spanner it is, and who may say so, is `whose_work` above.
    """
    order = get(session, code)
    if order.status not in (MaintenanceStatus.DUE, MaintenanceStatus.ASSIGNED):
        raise Conflict(f"{code} is {order.status.value}")
    whose = whose_work(session, order, actor, performed_by, capabilities)
    order.status = MaintenanceStatus.IN_PROGRESS
    order.started_at = utcnow()
    order.performed_by = whose
    session.flush()
    audit.record(session, actor=actor, action="maintenance.started",
                 entity_type="equipment", entity_id=order.equipment.code,
                 # Who the work belongs to, when that is not the account that
                 # typed it. The same column an agent's row uses for the person
                 # it acts for, and the same meaning: this request was made for
                 # somebody, and both of them are on the row.
                 on_behalf_of=None if whose == str(actor).upper() else whose,
                 after={"order": code, "performed_by": whose})
    return order


def complete(session: Session, code: str, *, findings: str | None = None,
             downtime_minutes: float | None = None,
             performed_by: str | None = None,
             capabilities: set[str] | None = None,
             actor: str = "system") -> MaintenanceOrder:
    """Close the job and re-baseline its plan.

    Re-baselining is the point: the next interval counts from the work that
    was actually done, not from when it was scheduled. A plan that keeps
    counting from its original date drifts a little further out of step every
    cycle.

    `downtime_minutes` is how long the *machine* was down for the job, which
    is not how long the job took. Greasing a palletiser bearing takes twenty
    minutes and stops nothing; booking twenty minutes of downtime against the
    line for it would invent downtime the plant never had, and the
    availability figure is built out of these numbers. So a caller that
    measured it says so, and the elapsed time of the job is the fallback for a
    caller that did not - which is what every release before this recorded,
    and is right for the jobs that do stop the machine.
    """
    order = get(session, code)
    if order.status is MaintenanceStatus.DONE:
        raise Conflict(f"{code} is already done")
    whose = whose_work(session, order, actor, performed_by, capabilities)

    now = utcnow()
    order.status = MaintenanceStatus.DONE
    order.completed_at = now
    order.performed_by = whose
    order.findings = findings
    if downtime_minutes is not None:
        if float(downtime_minutes) < 0:
            raise Invalid("downtime_minutes cannot be negative")
        order.downtime_minutes = round(float(downtime_minutes), 1)
    elif order.started_at:
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
                 on_behalf_of=None if whose == str(actor).upper() else whose,
                 after={"order": code, "findings": findings,
                        "performed_by": whose,
                        "downtime_minutes": order.downtime_minutes})
    return order


def create_plan(session: Session, *, code: str, name: str, equipment_code: str,
                trigger: str, interval: float, expected_minutes: float | None = None,
                instructions: str | None = None, document_code: str | None = None,
                skill_code: str | None = None, priority: int | None = None,
                needs_stop: bool = False, window: str | None = None,
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
    # None means *this plant's house default*, and it is resolved here rather
    # than in the signature so that the default a caller gets is the one this
    # plant is running on now, not the one the module was imported with.
    if expected_minutes is None:
        expected_minutes = plan_default_minutes(session)
    if skill_code:
        from fsmes.services import dispatch
        dispatch.get_skill(session, skill_code)   # 404 rather than a plan nobody can take
    if priority is not None and not 1 <= int(priority) <= 3:
        raise Invalid("priority is 1 (safety), 2 (production-critical) or 3 (routine)")
    plan = MaintenancePlan(
        code=code, name=name, equipment_id=equipment.id, trigger=kind,
        interval=interval, expected_minutes=expected_minutes,
        instructions=instructions, document_code=document_code,
        skill_code=skill_code,
        priority=int(priority) if priority is not None else None,
        needs_stop=bool(needs_stop),
        window=_window(window))
    session.add(plan)
    session.flush()
    audit.record(session, actor=actor, action="maintenance.plan_created",
                 entity_type="equipment", entity_id=equipment_code,
                 after={"plan": code, "trigger": kind.value, "interval": interval,
                        "skill": skill_code, "priority": plan.priority,
                        "needs_stop": plan.needs_stop,
                        "window": plan.window.value if plan.window else None})
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
        MaintenanceOrder.status.in_(OPEN_STATUSES))))
    assumed = default_job_minutes(session)
    minutes = sum(
        (o.plan.expected_minutes if o.plan else assumed) for o in open_orders)
    overdue = [o for o in open_orders if o.kind is MaintenanceKind.PREVENTIVE]
    return {
        "open": len(open_orders),
        "preventive": len(overdue),
        "corrective": len(open_orders) - len(overdue),
        "expected_downtime_minutes": round(minutes, 1),
        "expected_downtime_hours": round(minutes / 60.0, 2),
    }
