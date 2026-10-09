"""Maintenance endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from fsmes.api import paging
from fsmes.api.deps import ActorDep, DbDep, UserDep, require
from fsmes.domain import DispatchRule
from fsmes.services import auth, dispatch, maintenance

router = APIRouter()


class PlanIn(BaseModel):
    code: str
    name: str
    equipment: str
    trigger: str = "runtime_hours"
    interval: float
    # Left out, this plant's own house default stands - `[process]
    # maintenance_plan_default_minutes`, read at the moment the plan is
    # created. A number here would be a second copy of it, and a screen that
    # sent the product's thirty would overrule the plant that had chosen
    # forty-five without anybody meaning to.
    expected_minutes: float | None = None
    instructions: str | None = None
    document_code: str | None = None
    # Which trade the job needs and what it is worth interrupting the day for
    # (1 safety, 2 production-critical, 3 routine). Both optional: a plan that
    # says neither is work anybody on shift can take, at routine, which is the
    # honest reading of a plan nobody has classified rather than an invented
    # trade on a job somebody would then be sent to wrongly.
    skill: str | None = None
    priority: int | None = None
    # What the job needs of the *line*, as against of a person. A plan that
    # says neither leaves both unsaid, which the floor reads as "can be done
    # while the machine runs, whenever" - the behaviour of every plan written
    # before these two fields existed.
    needs_stop: bool = False
    window: str | None = None


class CorrectiveIn(BaseModel):
    equipment: str
    summary: str
    reason: str | None = None
    # A corrective order says its own, because the thing that broke says which
    # trade and how badly it matters - there is no plan behind it to copy from.
    skill: str | None = None
    priority: int | None = None
    needs_stop: bool = False
    window: str | None = None


class StartIn(BaseModel):
    # Whose job this is, when the account making the request is not that
    # person: a shop-floor terminal beside the machine, or a supervisor
    # booking his crew's work. The order has to have been given to them -
    # `services.maintenance.whose_work` says exactly what is and is not
    # allowed, and why.
    performed_by: str | None = None


class CompleteIn(BaseModel):
    findings: str | None = None
    # How long the *machine* was down for the job, which is not how long the
    # job took: greasing a bearing on a running palletiser stops nothing, and
    # booking the job's twenty minutes against the line would invent downtime
    # the plant never had. A caller that measured it says so; one that does
    # not gets the elapsed time of the job, as every release before this.
    downtime_minutes: float | None = None
    performed_by: str | None = None


class AssignIn(BaseModel):
    person: str
    # When it should happen. Now, unless the supervisor is planning ahead.
    scheduled_for: str | None = None


def _order_out(o) -> dict:
    return {
        "code": o.code, "equipment": o.equipment.code, "kind": o.kind.value,
        "status": o.status.value, "summary": o.summary, "reason": o.reason,
        "plan": o.plan.code if o.plan else None,
        "raised_at": o.raised_at, "started_at": o.started_at,
        "completed_at": o.completed_at, "performed_by": o.performed_by,
        "findings": o.findings, "downtime_minutes": o.downtime_minutes,
        "document": o.plan.document_code if o.plan else None,
        "skill": o.skill_code, "priority": o.priority,
        # What the job needs of the line. `needs_stop` is why an order can sit
        # at `assigned` for a whole shift without anybody having ignored it,
        # and a reader with only the status cannot tell that from a list
        # nobody worked. `window` is null when nobody has said when.
        "needs_stop": bool(o.needs_stop),
        "window": o.window.value if o.window else None,
        "assigned_to": o.assigned_to, "assigned_at": o.assigned_at,
        "assigned_by": o.assigned_by, "scheduled_for": o.scheduled_for,
        # Null means it has not been through dispatch - a different fact from
        # "nobody could take it", and a screen shows them differently.
        "unassigned_reason": o.unassigned_reason,
    }


@router.get("/due")
def due(db: DbDep, include_soon: bool = True) -> dict:
    """Plans that have come due, worst first.

    Due-ness is computed from what the machine actually did - hours it ran,
    units it made - not from a calendar, so an idle machine is not serviced on
    schedule and a busy one is not missed.
    """
    rows = maintenance.due(db, include_soon)
    return {
        "due": [r for r in rows if r["due"]],
        "due_soon": [r for r in rows if r["due_soon"]],
        "backlog": maintenance.backlog(db),
    }


@router.get("/plans")
def plans(
    db: DbDep,
    equipment: str | None = None,
    trigger: str | None = Query(None, description="runtime_hours, produced_qty or calendar_days."),
    q: str | None = Query(None, description="Match a plan code or name."),
) -> list[dict]:
    """Every plan with how far through its interval it is, or the ones that
    match. A plan per machine is the norm, so this is the plant's machine
    count and a screen pages it."""
    from sqlalchemy import select

    from fsmes.domain import Equipment, MaintenancePlan

    query = select(MaintenancePlan).order_by(MaintenancePlan.code)
    if equipment:
        query = query.join(Equipment, MaintenancePlan.equipment_id == Equipment.id).where(Equipment.code == equipment)
    if trigger:
        query = query.where(MaintenancePlan.trigger == trigger)
    if q:
        like = f"%{q}%"
        query = query.where(MaintenancePlan.code.ilike(like) | MaintenancePlan.name.ilike(like))
    return [maintenance.status_of(db, p) for p in db.scalars(query)]


@router.post("/plans", status_code=201, dependencies=[require("maintenance.plan")])
def create_plan(body: PlanIn, db: DbDep, actor: ActorDep) -> dict:
    plan = maintenance.create_plan(
        db, code=body.code, name=body.name, equipment_code=body.equipment,
        trigger=body.trigger, interval=body.interval,
        expected_minutes=body.expected_minutes, instructions=body.instructions,
        document_code=body.document_code, skill_code=body.skill,
        priority=body.priority, needs_stop=body.needs_stop, window=body.window,
        actor=actor)
    return maintenance.status_of(db, plan)


@router.get("/orders")
def orders(
    db: DbDep,
    equipment: str | None = None,
    status: list[str] | None = Query(
        None, description="due, assigned, in_progress, done or skipped; repeatable."),
    kind: str | None = Query(None, description="preventive or corrective."),
    q: str | None = Query(None, description="Match an order code, its summary or findings."),
    limit: int = paging.LimitQuery,
    offset: int = paging.OffsetQuery,
) -> dict:
    """Maintenance orders, newest first, one page at a time.

    The paging envelope, because this list grows with time: after a year
    the open work is buried under the done work, and a screen that read
    the last two hundred and filtered for "open" showed a plant with no
    open work the day the two hundred were all done.
    """
    rows, total = maintenance.history(db, equipment, limit, status=status, kind=kind, q=q, offset=offset)
    return paging.page([_order_out(o) for o in rows], total, limit, offset)


@router.post("/raise", dependencies=[require("maintenance.perform")])
def raise_due(db: DbDep, actor: ActorDep) -> dict:
    """Raise work for every plan that has come due.

    Idempotent - a plan with work already open does not get a second order,
    because a list full of duplicates is a list people learn to ignore.
    """
    raised = maintenance.raise_due(db, actor=actor)
    return {"raised": [_order_out(o) for o in raised], "count": len(raised)}


@router.post("/corrective", status_code=201, dependencies=[require("maintenance.perform")])
def corrective(body: CorrectiveIn, db: DbDep, actor: ActorDep) -> dict:
    return _order_out(maintenance.raise_corrective(
        db, equipment_code=body.equipment, summary=body.summary,
        reason=body.reason, skill_code=body.skill, priority=body.priority,
        needs_stop=body.needs_stop, window=body.window,
        actor=actor))


@router.post("/orders/{code}/start", dependencies=[require("maintenance.perform")])
def start(code: str, db: DbDep, actor: ActorDep, user: UserDep,
          body: StartIn | None = None) -> dict:
    """Pick up the spanner.

    An order that was given to somebody is started by them, or recorded as
    *their* work by naming them in `performed_by` - the terminal beside the
    machine, or the supervisor booking the shift. Taking somebody else's work
    for yourself is refused 403 with a sentence naming who can, unless you
    hold `maintenance.plan`, which is a supervisor, who may reassign it and
    then start it anyway.
    """
    held = auth.capabilities_for(db, user["role"])
    return _order_out(maintenance.start(
        db, code, actor=actor, capabilities=held,
        performed_by=body.performed_by if body else None))


@router.post("/orders/{code}/complete", dependencies=[require("maintenance.perform")])
def complete(code: str, body: CompleteIn, db: DbDep, actor: ActorDep,
             user: UserDep) -> dict:
    """Close the job and re-baseline its plan from the work actually done."""
    held = auth.capabilities_for(db, user["role"])
    return _order_out(maintenance.complete(
        db, code, findings=body.findings, downtime_minutes=body.downtime_minutes,
        performed_by=body.performed_by, capabilities=held, actor=actor))


# ------------------------------------------------- the crew, and who gets what


@router.post("/dispatch", dependencies=[require("maintenance.perform")])
def dispatch_due(db: DbDep, actor: ActorDep) -> dict:
    """Hand every due order to a free person who holds the trade.

    Runs on the plant's own tick as well, so this is the same pass a
    supervisor can ask for by hand. Idempotent: it looks only at orders at
    `due`, and never takes back one a person assigned.
    """
    return dispatch.dispatch(db, actor=actor)


@router.get("/dispatch/{code}/explain")
def explain(code: str, db: DbDep) -> dict:
    """Why this order went where it went, rule by rule and person by person.

    Written as the walk the dispatcher would make *now*, with what was actually
    recorded beside it - because what was decided an hour ago and what the same
    rules would decide this minute are two different answers, and conflating
    them is how a supervisor ends up mistrusting both.
    """
    return dispatch.explain(db, code)


@router.post("/orders/{code}/assign", dependencies=[require("maintenance.plan")])
def assign(code: str, body: AssignIn, db: DbDep, actor: ActorDep) -> dict:
    """Give the work to a named person, by hand.

    The rules never undo this: a supervisor who reaches in and is overruled by
    the machine stops reaching in, and then the plant has a dispatcher nobody
    corrects. A person who does not hold the order's trade is allowed and the
    audit row says so - a supervisor on the floor at two in the morning knows
    something the skills table does not.
    """
    when = datetime.fromisoformat(body.scheduled_for) if body.scheduled_for else None
    return _order_out(dispatch.assign(db, code, body.person, actor=actor,
                                      scheduled_for=when))


@router.get("/roster")
def roster(
    db: DbDep,
    shift: str | None = Query(
        None, description="A shift key (2026-10-09/DAY), `current` or `previous`. "
                          "Left out: the shift running now."),
) -> dict:
    """Who is on shift, what they can do, and what they already have on.

    The total is always stated, and so is the fact that there is no shift at
    all: a plant that has not told this MES its shift patterns has nobody
    rostered, which is a finding about the calendar and not an empty crew.
    """
    return dispatch.roster(db, shift)


@router.get("/rules")
def rules(db: DbDep) -> dict:
    """The supervisor's dispatch rules, in the order they are tried.

    A plant with none is answered with the one house default it is actually
    running on, marked as not being a row of its own - which is the difference
    between "nobody has configured this" and "this plant dispatches nothing".
    """
    written = list(db.scalars(select(DispatchRule).order_by(
        DispatchRule.sequence, DispatchRule.code)))
    running = dispatch.rules(db)
    return {
        "rules": [dispatch.rule_as_json(r) for r in written],
        "total": len(written),
        "tried_in_order": [r.code for r in running],
        "house_default": (dispatch.rule_as_json(dispatch.default_rule())
                          if not written else None),
        "skills": dispatch.skills(db),
    }
