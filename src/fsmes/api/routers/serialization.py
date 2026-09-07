"""Serialisation endpoints: trace one thing, not a batch."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from fsmes.api.deps import ActorDep, DbDep, require
from fsmes.services import serialization

router = APIRouter()


class ProduceIn(BaseModel):
    material: str
    order: str | None = None
    equipment: str | None = None
    serial: str | None = None
    count: int = 1


class PackIn(BaseModel):
    serial: str
    into: str


class BatchUnitIn(BaseModel):
    serial: str
    material: str | None = None
    order: str | None = None
    equipment: str | None = None


class BatchContainerIn(BaseModel):
    serial: str
    material: str
    order: str | None = None
    equipment: str | None = None


class BatchIn(BaseModel):
    """What a stacker's scanner sends when a stack closes: the pieces it
    holds, the stack's own serial, nothing else."""
    units: list[BatchUnitIn] = []
    material: str | None = None
    order: str | None = None
    equipment: str | None = None
    container: BatchContainerIn | None = None
    into: str | None = None
    contains: list[str] = []


class StatusIn(BaseModel):
    status: str
    note: str | None = None
    # Holding a pallet without holding the cases on it holds nothing.
    cascade: bool = True


@router.post("/units", status_code=201, dependencies=[require("production.book")])
def produce(body: ProduceIn, db: DbDep, actor: ActorDep) -> dict:
    """Bring identified units into existence."""
    made = [serialization.produce(
        db, material_code=body.material, order_code=body.order,
        equipment_code=body.equipment,
        serial=body.serial if body.count == 1 else None, actor=actor)
        for _ in range(max(1, body.count))]
    return {"produced": [u.serial for u in made], "count": len(made)}


@router.post("/units/batch", status_code=201, dependencies=[require("production.book")])
def produce_batch(body: BatchIn, db: DbDep, actor: ActorDep) -> dict:
    """Bring a batch of identified units into existence, packed - one call,
    one transaction, one audit entry per stack, pack or pallet. The shape a
    ten-million-piece day needs, and what a marker integration sends anyway."""
    return serialization.produce_batch(
        db, units=[u.model_dump(exclude_none=True) for u in body.units],
        material_code=body.material, order_code=body.order, equipment_code=body.equipment,
        container=body.container.model_dump(exclude_none=True) if body.container else None,
        into=body.into, contains=body.contains, actor=actor)


@router.post("/units/pack", dependencies=[require("production.book")])
def pack(body: PackIn, db: DbDep, actor: ActorDep) -> dict:
    """Put one unit inside another: bottle into case, case onto pallet."""
    unit = serialization.pack(db, serial=body.serial, into=body.into, actor=actor)
    return {"serial": unit.serial, "packed_into": body.into}


@router.post("/units/{serial}/status", dependencies=[require("quality.close_nc")])
def set_status(serial: str, body: StatusIn, db: DbDep, actor: ActorDep) -> dict:
    """Hold, release or scrap a unit and, by default, everything inside it."""
    touched = serialization.set_status(
        db, serial, body.status, note=body.note, cascade=body.cascade, actor=actor)
    return {"serial": serial, "status": body.status, "units_changed": len(touched)}


@router.get("/units/{serial}")
def contents(
    serial: str, db: DbDep,
    limit: int = Query(50, ge=1, le=500, description="Children drawn per node; every node states its count."),
) -> dict:
    """This unit and everything inside it, as the tree it is - counted in
    full, drawn `limit` children per node."""
    return serialization.contents(db, serial, limit=limit)


@router.get("/units/{serial}/trace")
def trace(serial: str, db: DbDep) -> dict:
    """What went into this unit - the question asked when a customer
    complains about one pack."""
    return serialization.trace_back(db, serial)


@router.get("/where-used/{lot_code}")
def where_used(lot_code: str, db: DbDep) -> dict:
    """Every package carrying a lot.

    The recall question, answered as packages rather than units: a warehouse
    holds pallets, and a list of ten thousand bottle serials is not an
    instruction anybody can act on.
    """
    return serialization.where_used(db, lot_code)
