"""Execution endpoints: lots, consumption, production reporting, genealogy."""

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from fsmes.api import paging
from fsmes.api.deps import ActorDep, DbDep, require
from fsmes.domain import LotStatus, Material, MaterialLot, ProductionSource
from fsmes.services import execution

router = APIRouter()


class LotIn(BaseModel):
    code: str
    material: str
    quantity: float


class LotOut(BaseModel):
    code: str
    material: str
    quantity: float
    original_quantity: float
    status: LotStatus
    produced_by_order: str | None = None


def _lot_out(db, lot: MaterialLot) -> LotOut:
    produced_by = None
    if lot.produced_by_order_id:
        from fsmes.domain import WorkOrder

        order = db.get(WorkOrder, lot.produced_by_order_id)
        produced_by = order.code if order else None
    return LotOut(
        code=lot.code,
        material=lot.material.code,
        quantity=lot.quantity,
        original_quantity=lot.original_quantity,
        status=lot.status,
        produced_by_order=produced_by,
    )


@router.get("/lots")
def list_lots(
    db: DbDep,
    material: str | None = None,
    status: LotStatus | None = None,
    q: str | None = Query(None, description="Match a lot code."),
    limit: int = paging.LimitQuery,
    offset: int = paging.OffsetQuery,
) -> dict:
    query = select(MaterialLot).order_by(MaterialLot.code)
    if material:
        query = query.join(Material).where(Material.code == material)
    if status:
        query = query.where(MaterialLot.status == status)
    if q:
        query = query.where(MaterialLot.code.like(f"%{q}%"))

    rows, total = paging.paginate(db, query, limit, offset)
    return paging.page([_lot_out(db, lot) for lot in rows], total, limit, offset)


@router.post("/lots", status_code=201, dependencies=[require("production.consume")])
def create_lot(body: LotIn, db: DbDep, actor: ActorDep) -> LotOut:
    lot = execution.create_lot(db, code=body.code, material_code=body.material, quantity=body.quantity, actor=actor)
    return _lot_out(db, lot)


class ConsumeIn(BaseModel):
    order: str
    lot: str
    quantity: float
    # Where it went in. One or the other, or neither for a consumable nobody
    # tracks to a station.
    seq: int | None = None
    equipment: str | None = None


@router.post("/consume", dependencies=[require("production.consume")])
def consume(body: ConsumeIn, db: DbDep, actor: ActorDep) -> LotOut:
    lot = execution.consume(
        db, order_code=body.order, lot_code=body.lot, quantity=body.quantity,
        seq=body.seq, equipment_code=body.equipment, actor=actor,
    )
    return _lot_out(db, lot)


class ReportIn(BaseModel):
    order: str | None = None
    seq: int | None = None
    equipment: str | None = None
    good: float = 0
    scrap: float = 0


@router.post("/report", dependencies=[require("production.book")])
def report(body: ReportIn, db: DbDep, actor: ActorDep) -> dict:
    op = execution.report(
        db,
        order_code=body.order,
        seq=body.seq,
        equipment_code=body.equipment,
        good=body.good,
        scrap=body.scrap,
        source=ProductionSource.MANUAL,
        actor=actor,
    )
    return {"order": op.order.code, "seq": op.seq, "good_qty": op.good_qty, "scrap_qty": op.scrap_qty} if op else {}


@router.get("/unassigned")
def unassigned(
    db: DbDep,
    equipment: str | None = Query(None, description="Only this machine."),
    limit: int = paging.LimitQuery,
    offset: int = paging.OffsetQuery,
) -> dict:
    """Units a machine counted with no order open to book them against.

    A counter that runs past its order, or between orders, is still counting
    real units. They are kept here rather than dropped to a log line, with
    the one fact that is certain — which machine, and when — and no guess
    about which order they belonged to. `good_total` and `scrap_total` are
    for the whole selection, not the page.
    """
    result = execution.unassigned_production(db, equipment_code=equipment, limit=limit, offset=offset)
    body = paging.page(result["items"], result["total"], limit, offset)
    body["good_total"] = result["good_total"]
    body["scrap_total"] = result["scrap_total"]
    return body


@router.get("/genealogy/{order_code}")
def genealogy(order_code: str, db: DbDep) -> dict:
    return execution.genealogy(db, order_code)


@router.get("/staging/{order_code}")
def staging(order_code: str, db: DbDep) -> dict:
    """What each station needs for the rest of this order, and where it will
    run short.

    The question a bill of materials by operation exists to answer: not "what
    does this order need" but "what does the washer need, and will it last".
    """
    from fsmes.services import staging as staging_service

    return staging_service.staging(db, order_code)


@router.get("/bom/{material_code}")
def bom(material_code: str, db: DbDep) -> dict:
    """A material's components and the station each goes in at."""
    from fsmes.services import staging as staging_service

    return {"material": material_code,
            "components": staging_service.bom_for(db, material_code)}
