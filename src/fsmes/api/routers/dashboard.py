"""The dashboard's data feed.

One endpoint returns everything the operator screen shows, so the page makes a
single request per refresh instead of a dozen — the difference between a
dashboard that scales to a wall panel and one that hammers the API.
"""

import threading
import time

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.api.deps import DbDep
from fsmes.domain import (
    AuditLog,
    Equipment,
    EquipmentLevel,
    EquipmentState,
    ErpMessage,
    MessageStatus,
    NcStatus,
    NonConformance,
    OrderStatus,
    ProductionLog,
    WorkOrder,
)
from fsmes.services import equipment as equipment_service
from fsmes.services import line as line_service
from fsmes.services import masterdata, workorders

router = APIRouter()

_ACTIVE = (OrderStatus.RELEASED, OrderStatus.RUNNING)


# Every floor screen asks for the same plant, every two seconds. Measured on
# sixty machines that is ~490 ms of database work each, and ten people watching
# put the server five seconds behind a two-second poll - it never catches up.
# Nothing about the answer is per-viewer, so it is computed once per tick and
# shared. One second is shorter than the poll interval, so the screen is never
# showing something older than its own refresh.
_CACHE_TTL_SECONDS = 1.0
_cache: dict[float, tuple[float, tuple, dict]] = {}
_cache_lock = threading.Lock()


def _plant_version(db: Session) -> tuple:
    """A cheap stamp that changes whenever the plant does.

    Time alone is the wrong key here: an operator who books production and
    watches the screen not change has been shown something false, and "only
    for a second" is not a defence on a shop floor. Two indexed max(id) reads
    cost microseconds and make the cache exact - it serves repeated identical
    questions and nothing else.
    """
    return (
        db.scalar(select(func.max(EquipmentState.id))),
        db.scalar(select(func.max(ProductionLog.id))),
        db.scalar(select(func.max(WorkOrder.id))),
    )


@router.get("/summary")
def summary(db: DbDep, oee_hours: float = 8.0, line: str | None = None) -> dict:
    """Everything the plant-floor screen needs, in one payload.

    Shared across viewers for a second at a time. A dozen people watching the
    same line is the normal case on a plant floor, not an edge one.
    """
    now = time.monotonic()
    version = _plant_version(db)
    with _cache_lock:
        cached = _cache.get((oee_hours, line))
        if cached and cached[1] == version and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[2]

    computed = _build_summary(db, oee_hours, line)
    with _cache_lock:
        _cache[(oee_hours, line)] = (time.monotonic(), version, computed)
    return computed


def _machines(db: Session, line: str | None) -> list[Equipment]:
    """The machines this screen is about.

    Unfiltered, that is every work unit in the database — right for a plant
    with one line and a wall of other people's machines for a plant with six.
    Naming a line narrows it to that line's machines at any depth beneath it,
    through cells and groups.
    """
    if line:
        centre = masterdata.get_equipment(db, line)
        return masterdata.work_units_under(db, centre)
    return list(db.scalars(
        select(Equipment)
        .where(Equipment.level == EquipmentLevel.WORK_UNIT)
        .order_by(Equipment.code)))


def _build_summary(db: Session, oee_hours: float, line: str | None = None) -> dict:
    wanted = _machines(db, line)
    ids = [eq.id for eq in wanted]
    # Whole-plant reads, once each: OEE in three grouped queries, the open
    # state of every machine in one, the dispatch list in one. Per-machine
    # versions of these were fine for six machines and a minute for 108.
    oees = equipment_service.oee_many(db, wanted, oee_hours)
    open_states = {
        s.equipment_id: s for s in db.scalars(
            select(EquipmentState).where(EquipmentState.equipment_id.in_(ids), EquipmentState.ended_at.is_(None)))
    } if ids else {}
    next_up: dict[int, object] = {}
    for op in workorders.dispatch_list(db):
        next_up.setdefault(op.equipment_id, op)

    machines = []
    for eq in wanted:
        state = open_states.get(eq.id)
        current = next_up.get(eq.id)
        machines.append(
            {
                "code": eq.code,
                "name": eq.name,
                "state": state.state if state else "unknown",
                "reason": state.reason if state else None,
                "since": state.started_at if state else None,
                # The machine's process value under its own name. Not always a
                # temperature: a washer reports WashTemp, a filler FillWeight, a
                # loader FeedRate. Asking every machine for ".Temperature" is why
                # this used to read blank on any line but the cola one.
                "analog": line_service.analog_reading(db, eq),
                "current_order": current.order.code if current else None,
                "current_operation": current.name if current else None,
                "oee": oees[eq.code],
            }
        )

    orders = [
        {
            "code": wo.code,
            "material": wo.material.code,
            "status": wo.status,
            "quantity": wo.quantity,
            "good": wo.good_qty,
            "scrap": wo.scrap_qty,
            "progress": round(min(1.0, wo.good_qty / wo.quantity), 3) if wo.quantity else 0,
            "priority": wo.priority,
            "due_date": wo.due_date,
            "erp_reference": wo.erp_reference,
            "operations": [
                {
                    "seq": op.seq,
                    "name": op.name,
                    "equipment": op.equipment.code,
                    "status": op.status,
                    "good": op.good_qty,
                    "scrap": op.scrap_qty,
                }
                for op in wo.operations
            ],
        }
        for wo in db.scalars(
            select(WorkOrder)
            .where(WorkOrder.status.notin_((OrderStatus.CLOSED, OrderStatus.CANCELLED)))
            .order_by(WorkOrder.priority, WorkOrder.code)
            .limit(25)
        )
    ]

    audit = [
        {
            "ts": entry.ts,
            "actor": entry.actor,
            "on_behalf_of": entry.on_behalf_of,
            "action": entry.action,
            "entity_type": entry.entity_type,
            "entity_id": entry.entity_id,
        }
        for entry in db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(12))
    ]

    open_ncs = [
        {"code": nc.code, "description": nc.description, "severity": nc.severity, "created_at": nc.created_at}
        for nc in db.scalars(
            select(NonConformance)
            .where(NonConformance.status == NcStatus.OPEN)
            .order_by(NonConformance.id.desc())
            .limit(5)
        )
    ]

    running = sum(1 for m in machines if m["state"] == "running")
    plant_oee = [m["oee"]["oee"] for m in machines if m["oee"]["oee"] is not None]

    return {
        "plant": {
            "machines_total": len(machines),
            "machines_running": running,
            "active_orders": sum(1 for o in orders if o["status"] in _ACTIVE),
            "open_ncs": db.scalar(
                select(NonConformance.id).where(NonConformance.status == NcStatus.OPEN).limit(1)
            )
            is not None,
            "erp_pending": db.scalar(select(ErpMessage.id).where(ErpMessage.status == MessageStatus.PENDING).limit(1))
            is not None,
            "oee": round(sum(plant_oee) / len(plant_oee), 4) if plant_oee else None,
        },
        "machines": machines,
        "orders": orders,
        "audit": audit,
        "non_conformances": open_ncs,
    }
