"""Trigger endpoints: define, approve, withdraw, and read what fired."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from fsmes.api import paging
from fsmes.api.deps import ActorDep, DbDep, require
from fsmes.services import triggers

router = APIRouter()


class TriggerIn(BaseModel):
    code: str
    name: str
    tag: str
    condition: str
    threshold: float
    equipment: str | None = None
    sustained_seconds: float = 0.0
    cooldown_seconds: float = 300.0
    action: str = "log_event"
    action_params: dict | None = None
    note: str | None = None


@router.get("/catalog")
def catalog() -> dict:
    """The actions a trigger may take, and the conditions it may watch for.
    Adding one is product code with tests, never a plant-specific script."""
    from fsmes.domain import TriggerCondition

    return {"actions": triggers.ACTION_HELP,
            "conditions": [c.value for c in TriggerCondition]}


@router.get("")
def list_triggers(
    db: DbDep,
    status: str | None = None,
    equipment: str | None = Query(None, description="Triggers on this machine, or on any machine."),
    q: str | None = Query(None, description="Match a code, a name or a tag."),
) -> list[dict]:
    return triggers.listing(db, status, equipment=equipment, q=q)


@router.post("", status_code=201, dependencies=[require("triggers.write")])
def create_trigger(body: TriggerIn, db: DbDep, actor: ActorDep) -> dict:
    """A draft. It watches nothing until a person approves it."""
    t = triggers.create(
        db, code=body.code, name=body.name, tag=body.tag, condition=body.condition,
        threshold=body.threshold, equipment_code=body.equipment,
        sustained_seconds=body.sustained_seconds, cooldown_seconds=body.cooldown_seconds,
        action=body.action, action_params=body.action_params, note=body.note, actor=actor)
    return triggers.out(t)


@router.get("/firings")
def recent_firings(
    db: DbDep,
    hours: float = 24.0,
    trigger: str | None = None,
    equipment: str | None = None,
    ok: bool | None = Query(None, description="Only firings whose action succeeded (true) or failed (false)."),
    limit: int = paging.LimitQuery,
    offset: int = paging.OffsetQuery,
) -> dict:
    """Firings in the window, newest first, one page at a time - a trigger
    on the planned-stop bit fires at every changeover on every machine, so
    a day of a real plant is thousands."""
    items, total = triggers.since(db, hours, trigger=trigger, equipment=equipment, ok=ok,
                                  limit=limit, offset=offset)
    return paging.page(items, total, limit, offset)


@router.get("/{code}")
def get_trigger(code: str, db: DbDep) -> dict:
    return triggers.out(triggers.get(db, code))


@router.get("/{code}/firings")
def trigger_firings(code: str, db: DbDep, limit: int = 50) -> list[dict]:
    return triggers.firings(db, code, limit)


@router.post("/{code}/approve", dependencies=[require("triggers.approve")])
def approve_trigger(code: str, db: DbDep, actor: ActorDep) -> dict:
    """Put it in force. The agent picks it up within its reload interval."""
    return triggers.out(triggers.approve(db, code, actor=actor))


@router.post("/{code}/withdraw", dependencies=[require("triggers.approve")])
def withdraw_trigger(code: str, db: DbDep, actor: ActorDep) -> dict:
    return triggers.out(triggers.withdraw(db, code, actor=actor))
