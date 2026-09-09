"""Maintenance endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from fsmes.api import paging
from fsmes.api.deps import ActorDep, DbDep, require
from fsmes.services import maintenance

router = APIRouter()


class PlanIn(BaseModel):
    code: str
    name: str
    equipment: str
    trigger: str = "runtime_hours"
    interval: float
    expected_minutes: float = 30.0
    instructions: str | None = None
    document_code: str | None = None


class CorrectiveIn(BaseModel):
    equipment: str
    summary: str
    reason: str | None = None


class CompleteIn(BaseModel):
    findings: str | None = None


def _order_out(o) -> dict:
    return {
        "code": o.code, "equipment": o.equipment.code, "kind": o.kind.value,
        "status": o.status.value, "summary": o.summary, "reason": o.reason,
        "plan": o.plan.code if o.plan else None,
        "raised_at": o.raised_at, "started_at": o.started_at,
        "completed_at": o.completed_at, "performed_by": o.performed_by,
        "findings": o.findings, "downtime_minutes": o.downtime_minutes,
        "document": o.plan.document_code if o.plan else None,
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
        query = query.where(MaintenancePlan.code.like(like) | MaintenancePlan.name.like(like))
    return [maintenance.status_of(db, p) for p in db.scalars(query)]


@router.post("/plans", status_code=201, dependencies=[require("maintenance.plan")])
def create_plan(body: PlanIn, db: DbDep, actor: ActorDep) -> dict:
    plan = maintenance.create_plan(
        db, code=body.code, name=body.name, equipment_code=body.equipment,
        trigger=body.trigger, interval=body.interval,
        expected_minutes=body.expected_minutes, instructions=body.instructions,
        document_code=body.document_code, actor=actor)
    return maintenance.status_of(db, plan)


@router.get("/orders")
def orders(
    db: DbDep,
    equipment: str | None = None,
    status: list[str] | None = Query(None, description="due, in_progress or done; repeatable."),
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
        reason=body.reason, actor=actor))


@router.post("/orders/{code}/start", dependencies=[require("maintenance.perform")])
def start(code: str, db: DbDep, actor: ActorDep) -> dict:
    return _order_out(maintenance.start(db, code, actor=actor))


@router.post("/orders/{code}/complete", dependencies=[require("maintenance.perform")])
def complete(code: str, body: CompleteIn, db: DbDep, actor: ActorDep) -> dict:
    """Close the job and re-baseline its plan from the work actually done."""
    return _order_out(maintenance.complete(db, code, findings=body.findings, actor=actor))
