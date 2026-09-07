"""Recommended adjustments: propose, decide, and read what happened next."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from fsmes.api import paging
from fsmes.api.deps import ActorDep, DbDep, require
from fsmes.services import adjustments

router = APIRouter()


class ProposeIn(BaseModel):
    equipment: str
    tag: str
    value: float
    rationale: str
    evidence: dict | None = None
    verify_after_seconds: float | None = None


class DecisionIn(BaseModel):
    note: str | None = None


@router.get("")
def list_adjustments(
    db: DbDep,
    status: str | None = None,
    equipment: str | None = None,
    limit: int = paging.LimitQuery,
    offset: int = paging.OffsetQuery,
) -> dict:
    """The queue, newest first, one page at a time - the paging envelope,
    because recommendations accumulate for as long as an agent watches."""
    items, total = adjustments.listing(db, status, limit, equipment=equipment, offset=offset)
    return paging.page(items, total, limit, offset)


@router.post("", status_code=201, dependencies=[require("adjustments.propose")])
def propose(body: ProposeIn, db: DbDep, actor: ActorDep) -> dict:
    """Recommend a setpoint change. Nothing is written until a person
    approves; the tag must be declared writable and the value inside its
    bounds, or this refuses."""
    rec = adjustments.propose(
        db, equipment_code=body.equipment, tag=body.tag, value=body.value, rationale=body.rationale,
        evidence=body.evidence, verify_after_seconds=body.verify_after_seconds, actor=actor)
    return adjustments.out(rec)


@router.get("/{code}")
def get_adjustment(code: str, db: DbDep) -> dict:
    return adjustments.out(adjustments.get(db, code))


@router.post("/{code}/approve", dependencies=[require("adjustments.approve")])
def approve(code: str, body: DecisionIn, db: DbDep, actor: ActorDep) -> dict:
    """The human in the loop. The OPC agent writes within seconds."""
    return adjustments.out(adjustments.approve(db, code, note=body.note, actor=actor))


@router.post("/{code}/reject", dependencies=[require("adjustments.approve")])
def reject(code: str, body: DecisionIn, db: DbDep, actor: ActorDep) -> dict:
    return adjustments.out(adjustments.reject(db, code, note=body.note, actor=actor))
