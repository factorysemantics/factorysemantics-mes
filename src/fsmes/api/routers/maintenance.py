"""Maintenance endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select

from fsmes.api import paging
from fsmes.api.deps import ActorDep, DbDep, UserDep, require
from fsmes.domain import DispatchRule, MaintenanceOrder, Person
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


class RuleIn(BaseModel):
    """The blanks in the supervisor's sentence, and nothing else.

    *Work on [machine] [needing TRADE] at priority [n] goes to [how].* Every
    blank may be left out, and left out means "anything" - a body with nothing
    in it at all is the sentence *work anywhere in the plant, whatever the
    skill, goes to somebody on this shift, whoever has least on*, which is a
    rule a plant might really want and the one it falls back to anyway.

    `code` and `name` are here for a pack or a script that has its own naming.
    A screen sends neither: both are made from the sentence, because a
    supervisor filling in blanks has not been asked to invent an identifier.
    """

    equipment: str | None = None
    skill: str | None = None
    priority_at_least: int | None = None
    strategy: str = "least_loaded"
    sequence: int | None = None
    active: bool = True
    code: str | None = None
    name: str | None = None
    supervisor: str | None = None


class RuleChange(BaseModel):
    """One change to a written rule: a blank, its order, or its on/off.

    Only what is sent is changed - `exclude_unset` - and a blank sent as JSON
    `null` is cleared, which is how *needing ELEC* becomes *whatever the skill*
    without deleting a rule and losing what it has handed out.

    `move` is the up and down arrows: one call, because the order is the thing
    being changed and a page that sent two sequence numbers could leave two
    rules on the same one.
    """

    equipment: str | None = None
    skill: str | None = None
    priority_at_least: int | None = None
    strategy: str | None = None
    sequence: int | None = None
    active: bool | None = None
    name: str | None = None
    supervisor: str | None = None
    move: str | None = None


def _names(db) -> dict[str, str]:
    """The register of people, code to name, read once for a whole response.

    A supervisor reads names; the plant stores codes. Both travel on every
    order so a screen can show the name and keep the code beside it, and so
    that no screen has to fetch the register per row.
    """
    return dict(db.execute(select(Person.code, Person.name)).all())


def _order_out(o, names: dict[str, str] | None = None) -> dict:
    names = names if names is not None else {}
    return {
        "code": o.code, "equipment": o.equipment.code, "kind": o.kind.value,
        "status": o.status.value, "summary": o.summary, "reason": o.reason,
        "plan": o.plan.code if o.plan else None,
        "raised_at": o.raised_at, "started_at": o.started_at,
        "completed_at": o.completed_at, "performed_by": o.performed_by,
        # The name beside the code, null when the plant holds nobody by that
        # code - which is a finding and not a blank.
        "performed_by_name": names.get(o.performed_by) if o.performed_by else None,
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
        "assigned_to_name": names.get(o.assigned_to) if o.assigned_to else None,
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
    names = _names(db)
    return paging.page([_order_out(o, names) for o in rows], total, limit, offset)


@router.post("/raise", dependencies=[require("maintenance.perform")])
def raise_due(db: DbDep, actor: ActorDep) -> dict:
    """Raise work for every plan that has come due.

    Idempotent - a plan with work already open does not get a second order,
    because a list full of duplicates is a list people learn to ignore.
    """
    raised = maintenance.raise_due(db, actor=actor)
    names = _names(db)
    return {"raised": [_order_out(o, names) for o in raised], "count": len(raised)}


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
        performed_by=body.performed_by if body else None), _names(db))


@router.post("/orders/{code}/complete", dependencies=[require("maintenance.perform")])
def complete(code: str, body: CompleteIn, db: DbDep, actor: ActorDep,
             user: UserDep) -> dict:
    """Close the job and re-baseline its plan from the work actually done."""
    held = auth.capabilities_for(db, user["role"])
    return _order_out(maintenance.complete(
        db, code, findings=body.findings, downtime_minutes=body.downtime_minutes,
        performed_by=body.performed_by, capabilities=held, actor=actor), _names(db))


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
                                      scheduled_for=when), _names(db))


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
    # How much work each rule has actually handed out, in one grouped query.
    # It is the difference between a rule that can be removed and one that can
    # only be switched off - every order a rule sent still names it - so the
    # screen can offer the right control instead of offering Remove and
    # letting the server refuse it.
    handed = dict(db.execute(
        select(MaintenanceOrder.assigned_by, func.count())
        .where(MaintenanceOrder.assigned_by.is_not(None))
        .group_by(MaintenanceOrder.assigned_by)).all())
    return {
        "rules": [dispatch.rule_as_json(r) | {"handed_out": handed.get(r.code, 0)}
                  for r in written],
        "total": len(written),
        "tried_in_order": [r.code for r in running],
        "house_default": (dispatch.rule_as_json(dispatch.default_rule())
                          if not written else None),
        "skills": dispatch.skills(db),
        # The words the blanks may be filled with, from this plant. Sent with
        # the rules so a screen offering the sentence offers exactly the
        # vocabulary the dispatcher understands and keeps no copy of its own.
        "vocabulary": dispatch.vocabulary(db),
    }


@router.post("/rules", dependencies=[require("maintenance.plan")])
def write_rule(body: RuleIn, db: DbDep, actor: ActorDep, response: Response,
               dry_run: bool = Query(
                   False, description="Say what the sentence would read as and "
                                      "write nothing. For the preview line on a "
                                      "screen while the blanks are being chosen.")) -> dict:
    """Write one of the supervisor's sentences down, and read it back.

    The response always carries `says` - the rule as the dispatcher itself
    renders it, which is the same string `fsmes` prints and the same string the
    audit row keeps. A screen shows that and never its own rendering, so the
    words on the page and the words in the decision cannot drift apart.

    With `dry_run` nothing is written and no code is taken: the answer is the
    rule this sentence *would* become, for the preview under the blanks.
    """
    out = dispatch.write_rule(
        db, equipment_code=body.equipment, skill_code=body.skill,
        priority_at_least=body.priority_at_least, strategy=body.strategy,
        code=body.code, name=body.name, supervisor_code=body.supervisor,
        sequence=body.sequence, active=body.active, actor=actor, dry_run=dry_run)
    response.status_code = 200 if dry_run else 201
    return out


@router.patch("/rules/{code}", dependencies=[require("maintenance.plan")])
def change_rule(code: str, body: RuleChange, db: DbDep, actor: ActorDep) -> dict:
    """Change a blank, switch a rule off, or move it up or down the order.

    Only what the body names is touched; a blank sent as null is cleared. The
    audit row carries the sentence before and the sentence after, because six
    weeks later what a supervisor wants to read is not that `skill_code` went
    to null but that the rule stopped being about electricians.
    """
    changes = body.model_dump(exclude_unset=True)
    move = changes.pop("move", None)
    if not move:
        # An empty body reaches `update_rule` on purpose: it is the one place
        # that says what a rule's blanks are called, and a screen that sent
        # nothing should be told that rather than answered 200 with no change.
        return dispatch.update_rule(db, code, changes, actor=actor)
    if changes:
        dispatch.update_rule(db, code, changes, actor=actor)
    return dispatch.move_rule(db, code, move, actor=actor)


@router.delete("/rules/{code}", dependencies=[require("maintenance.plan")])
def remove_rule(code: str, db: DbDep, actor: ActorDep) -> dict:
    """Remove a rule that never handed anything out.

    One that has is refused with the count and with what was meant instead:
    switch it off. Every order it sent still names it in `assigned_by`, and an
    audit trail pointing at a rule nobody can look up has stopped being one.
    """
    return dispatch.delete_rule(db, code, actor=actor)


@router.get("/shift")
def shift(
    db: DbDep,
    shift: str | None = Query(
        None, description="A shift key (2026-10-09/DAY), `current` or `previous`. "
                          "Left out: the shift running now."),
) -> dict:
    """This shift in one read: the line at the top, the work, and what is stuck.

    One line a supervisor can say out loud - how many orders came due, how many
    the rules handed out, how many are waiting and why the first one is - then
    the shift's orders with who has each and which rule sent it, then what is
    waiting grouped by the dispatcher's own reasons.

    Four queries whatever the plant's size, never one per order. A plant with
    no shift pattern is answered with no shift and the reason, because an empty
    shift would read as a quiet night.
    """
    return dispatch.shift_view(db, shift)
