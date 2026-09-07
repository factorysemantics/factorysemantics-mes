"""Scheduling and calendar endpoints."""

from __future__ import annotations

from datetime import date, datetime, time

from fastapi import APIRouter
from pydantic import BaseModel

from fsmes.api.deps import ActorDep, DbDep, require
from fsmes.services import calendar, scheduling

router = APIRouter()


class ShiftIn(BaseModel):
    code: str
    name: str
    starts: time
    ends: time
    days: str = "1111100"
    equipment: str | None = None


class ExceptionIn(BaseModel):
    day: date
    kind: str
    reason: str
    equipment: str | None = None


class PlanIn(BaseModel):
    start: datetime | None = None


@router.get("/calendar")
def show_calendar(db: DbDep) -> dict:
    """When the plant runs. Every promised date rests on this."""
    return calendar.describe(db)


@router.post("/calendar/shifts", status_code=201,
             dependencies=[require("scheduling.plan")])
def create_shift(body: ShiftIn, db: DbDep, actor: ActorDep) -> dict:
    pattern = calendar.create_pattern(
        db, code=body.code, name=body.name, starts=body.starts, ends=body.ends,
        days=body.days, equipment_code=body.equipment, actor=actor)
    return {"code": pattern.code, "crosses_midnight": pattern.crosses_midnight}


@router.post("/calendar/exceptions", status_code=201,
             dependencies=[require("scheduling.plan")])
def add_exception(body: ExceptionIn, db: DbDep, actor: ActorDep) -> dict:
    """A shutdown day, or an overtime one.

    Both directions matter: a shutdown removes capacity a schedule would
    otherwise promise, an overtime Saturday adds capacity a planner counts on.
    """
    row = calendar.add_exception(db, day=body.day, kind=body.kind,
                                 reason=body.reason, equipment_code=body.equipment,
                                 actor=actor)
    return {"day": str(row.day), "kind": row.kind.value, "reason": row.reason}


@router.get("/board")
def board(db: DbDep, equipment: str | None = None, hours: float = 24.0) -> dict:
    """The schedule machine by machine, with maintenance blocks alongside the
    work so the day reads as one thing rather than two lists."""
    return scheduling.board(db, equipment_code=equipment, hours=hours)


@router.post("/plan/{code}", dependencies=[require("scheduling.plan")])
def plan_order(code: str, body: PlanIn, db: DbDep, actor: ActorDep) -> dict:
    """Schedule one order's operations in route sequence."""
    return scheduling.plan_order(db, code, start=body.start, actor=actor)


@router.post("/plan", dependencies=[require("scheduling.plan")])
def plan_all(body: PlanIn, db: DbDep, actor: ActorDep) -> dict:
    """Schedule every open order, highest priority first."""
    plans = scheduling.plan_all(db, start=body.start, actor=actor)
    return {"planned": len(plans), "orders": plans}


@router.get("/promise/{code}")
def promise(code: str, db: DbDep) -> dict:
    """When this order will finish, and whether that misses its due date.

    A due date the plan disagrees with is the most useful thing a scheduler
    produces, and the thing a spreadsheet never says out loud.
    """
    return scheduling.promise(db, code)
