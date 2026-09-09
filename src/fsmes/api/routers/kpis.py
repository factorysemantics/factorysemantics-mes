"""KPI endpoints: OEE per machine, order progress across the plant."""

from fastapi import APIRouter
from sqlalchemy import select

from fsmes.api.deps import DbDep
from fsmes.domain import OrderStatus, WorkOrder
from fsmes.services import equipment

router = APIRouter()


@router.get("/oee/{equipment_code}")
def oee(equipment_code: str, db: DbDep, hours: float = 8.0) -> dict:
    return equipment.oee(db, equipment_code=equipment_code, hours=hours)


@router.get("/orders")
def order_progress(db: DbDep) -> list[dict]:
    active = db.scalars(
        select(WorkOrder)
        .where(WorkOrder.status.in_((OrderStatus.RELEASED, OrderStatus.RUNNING, OrderStatus.COMPLETED)))
        .order_by(WorkOrder.priority, WorkOrder.code)
    )
    return [
        {
            "order": wo.code,
            "material": wo.material.code,
            "status": wo.status,
            "ordered": wo.quantity,
            "good": wo.good_qty,
            "scrap": wo.scrap_qty,
            "progress": round(min(1.0, wo.good_qty / wo.quantity), 3) if wo.quantity else None,
            "due_date": wo.due_date,
        }
        for wo in active
    ]
