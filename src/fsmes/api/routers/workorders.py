"""Work order endpoints: create, lifecycle actions, dispatch list."""

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from fsmes.api import paging
from fsmes.api.deps import ActorDep, DbDep, require
from fsmes.domain import OperationStatus, OrderStatus, WorkOrder, WorkOrderOperation
from fsmes.services import masterdata, workorders

router = APIRouter()


class WorkOrderIn(BaseModel):
    code: str
    material: str
    quantity: float
    due_date: datetime | None = None
    priority: int = 50


class OperationOut(BaseModel):
    seq: int
    name: str
    equipment: str
    status: OperationStatus
    good_qty: float
    scrap_qty: float


class WorkOrderOut(BaseModel):
    code: str
    material: str
    quantity: float
    status: OrderStatus
    priority: int
    due_date: datetime | None
    erp_reference: str | None
    good_qty: float
    scrap_qty: float
    operations: list[OperationOut]


def _out(wo: WorkOrder) -> WorkOrderOut:
    return WorkOrderOut(
        code=wo.code,
        material=wo.material.code,
        quantity=wo.quantity,
        status=wo.status,
        priority=wo.priority,
        due_date=wo.due_date,
        erp_reference=wo.erp_reference,
        good_qty=wo.good_qty,
        scrap_qty=wo.scrap_qty,
        operations=[
            OperationOut(
                seq=op.seq,
                name=op.name,
                equipment=op.equipment.code,
                status=op.status,
                good_qty=op.good_qty,
                scrap_qty=op.scrap_qty,
            )
            for op in wo.operations
        ],
    )


@router.get("")
def list_orders(
    db: DbDep,
    status: list[OrderStatus] | None = Query(
        None, description="Repeatable. An order matching any of these is returned."),
    material: str | None = None,
    q: str | None = Query(None, description="Match an order code."),
    due_after: datetime | None = Query(None, description="Due on or after this."),
    due_before: datetime | None = Query(None, description="Due on or before this."),
    line: str | None = Query(None, description="Only orders running on this line."),
    limit: int = paging.LimitQuery,
    offset: int = paging.OffsetQuery,
) -> dict:
    """Work orders, newest first, one page at a time.

    A year of a busy plant is tens of thousands of orders; this endpoint used
    to return all of them - 5.5 MB and two and a half seconds - on every screen
    load. Filters exist because at that size scrolling is not a search
    strategy.
    """
    query = select(WorkOrder).order_by(WorkOrder.id.desc())
    if status:
        query = query.where(WorkOrder.status.in_(status))
    if material:
        query = query.where(WorkOrder.material.has(code=material))
    if q:
        query = query.where(WorkOrder.code.like(f"%{q}%"))
    # An order with no due date is not due before anything, so a date filter
    # excludes it rather than guessing a date for it.
    if due_after:
        query = query.where(WorkOrder.due_date >= due_after)
    if due_before:
        query = query.where(WorkOrder.due_date <= due_before)
    if line:
        centre = masterdata.get_equipment(db, line)
        query = query.where(WorkOrder.work_center_id == centre.id)

    rows, total = paging.paginate(db, query, limit, offset)
    return paging.page([_out(wo) for wo in rows], total, limit, offset)


@router.post("", status_code=201, dependencies=[require("orders.create")])
def create_order(body: WorkOrderIn, db: DbDep, actor: ActorDep) -> WorkOrderOut:
    wo = workorders.create(
        db,
        code=body.code,
        material_code=body.material,
        quantity=body.quantity,
        due_date=body.due_date,
        priority=body.priority,
        actor=actor,
    )
    return _out(wo)


@router.get("/dispatch")
def dispatch(db: DbDep, equipment: str | None = None) -> list[dict]:
    return [
        {
            "order": op.order.code,
            "seq": op.seq,
            "operation": op.name,
            "equipment": op.equipment.code,
            "status": op.status,
            "quantity": op.order.quantity,
            "good_qty": op.good_qty,
            "priority": op.order.priority,
            "due_date": op.order.due_date,
        }
        for op in workorders.dispatch_list(db, equipment)
    ]


@router.get("/summary")
def order_summary(db: DbDep) -> dict:
    """How many orders sit in each status, and what the plant has made.

    The orders screen counted its own page, which is fine at fifty orders and
    a lie at eighteen thousand. It became an obvious lie the moment a tile
    turned into a filter you could click: a tile reading 7 must not open a
    list reading 340.

    Good is the quantity off the *end* of each route, not the sum of every
    operation - a part is not made three times because it passed three
    stations. That is the same rule `WorkOrder.good_qty` follows, and a test
    holds the two to the same answer.
    """
    counts = dict(db.execute(
        select(WorkOrder.status, func.count()).group_by(WorkOrder.status)).all())

    last = (
        select(WorkOrderOperation.work_order_id.label("order_id"),
               func.max(WorkOrderOperation.seq).label("seq"))
        .group_by(WorkOrderOperation.work_order_id)
        .subquery())
    good = db.scalar(
        select(func.sum(WorkOrderOperation.good_qty)).join(
            last,
            (WorkOrderOperation.work_order_id == last.c.order_id)
            & (WorkOrderOperation.seq == last.c.seq))) or 0.0
    scrap = db.scalar(select(func.sum(WorkOrderOperation.scrap_qty))) or 0.0

    made = good + scrap
    return {
        "by_status": {s.value: counts.get(s, 0) for s in OrderStatus},
        "total": sum(counts.values()),
        "good_qty": good,
        "scrap_qty": scrap,
        # Unknown, not zero, when the plant has booked nothing at all.
        "yield": (good / made) if made > 0 else None,
    }


@router.get("/{code}/wip")
def order_wip(code: str, db: DbDep) -> dict:
    """Where this order's units are, stage by stage, and in whose cost
    center. Derived from the counters the plant already books."""
    from fsmes.services import execution

    return execution.wip(db, code)


@router.get("/{code}")
def get_order(code: str, db: DbDep) -> WorkOrderOut:
    return _out(workorders.get(db, code))


@router.post("/{code}/release", dependencies=[require("orders.release")])
def release(code: str, db: DbDep, actor: ActorDep) -> WorkOrderOut:
    return _out(workorders.release(db, code, actor))


class HoldIn(BaseModel):
    reason: str


@router.post("/{code}/hold", dependencies=[require("orders.close")])
def hold(code: str, body: HoldIn, db: DbDep, actor: ActorDep) -> WorkOrderOut:
    """Stop an order over a concern. The reason is mandatory - the person
    resuming it has to know what was wrong."""
    return _out(workorders.hold(db, code, body.reason, actor=actor))


@router.post("/{code}/resume", dependencies=[require("orders.close")])
def resume(code: str, db: DbDep, actor: ActorDep) -> WorkOrderOut:
    """Back to work, to wherever the order truly was."""
    return _out(workorders.resume(db, code, actor=actor))


@router.post("/{code}/close", dependencies=[require("orders.close")])
def close(code: str, db: DbDep, actor: ActorDep) -> WorkOrderOut:
    return _out(workorders.close(db, code, actor))


@router.post("/{code}/cancel", dependencies=[require("orders.close")])
def cancel(code: str, db: DbDep, actor: ActorDep) -> WorkOrderOut:
    return _out(workorders.cancel(db, code, actor))


@router.post("/{code}/operations/{seq}/start", dependencies=[require("production.book")])
def start_operation(code: str, seq: int, db: DbDep, actor: ActorDep) -> WorkOrderOut:
    workorders.start_operation(db, code, seq, actor)
    return _out(workorders.get(db, code))


@router.post("/{code}/operations/{seq}/complete", dependencies=[require("production.book")])
def complete_operation(code: str, seq: int, db: DbDep, actor: ActorDep) -> WorkOrderOut:
    workorders.complete_operation(db, code, seq, actor)
    return _out(workorders.get(db, code))
