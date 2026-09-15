"""The dashboard's data feed.

One endpoint returns everything the operator screen shows, so the page makes a
single request per refresh instead of a dozen — the difference between a
dashboard that scales to a wall panel and one that hammers the API.
"""

import threading
import time

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.api.deps import DbDep
from fsmes.domain import (
    AuditLog,
    Equipment,
    EquipmentLevel,
    EquipmentState,
    EquipmentStateName,
    ErpMessage,
    MessageStatus,
    NcStatus,
    NonConformance,
    OrderStatus,
    ProductionLog,
    WorkOrder,
)
from fsmes.services import connection as connection_service
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
# Filters are part of the cache key, so a wall of screens on different filters
# must not grow it without end. Nothing here is precious - it is one second of
# a repeated question.
_CACHE_MAX_ENTRIES = 64
# A page of machines is a screen's worth, not a plant's. The ceiling matches
# fsmes.api.paging.MAX_LIMIT so no list in this API has a different one.
MACHINE_MAX_LIMIT = 500
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
def summary(
    db: DbDep,
    oee_hours: float = 8.0,
    line: str | None = Query(None, description="Only this line's machines, at any depth beneath it."),
    machine_q: str | None = Query(None, description="Match a machine code or name."),
    machine_state: str | None = Query(
        None, description="running, idle, down, setup - or unknown, for a machine that has never reported one."),
    machine_limit: int | None = Query(
        None, ge=1, le=MACHINE_MAX_LIMIT,
        description="How many machines to return. Left out, the answer is every machine in scope, "
                    "as it always was."),
    machine_offset: int = Query(0, ge=0, description="How many machines to skip."),
) -> dict:
    """Everything the plant-floor screen needs, in one payload.

    Shared across viewers for a second at a time. A dozen people watching the
    same line is the normal case on a plant floor, not an edge one.

    `line` is a *scope*: it changes which plant the tiles are about, which is
    what the line screen wants. `machine_q` and `machine_state` are a *filter*
    on the machine list only - the tiles keep counting the whole scope, so
    narrowing the grid to the four machines that are down never makes the
    plant look like a four-machine plant.
    """
    now = time.monotonic()
    version = _plant_version(db)
    key = (oee_hours, line, machine_q, machine_state, machine_limit, machine_offset)
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[1] == version and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[2]

    computed = _build_summary(db, oee_hours, line, machine_q, machine_state,
                              machine_limit, machine_offset)
    with _cache_lock:
        # One screen, one filter, one entry. Without this a wall of tablets on
        # different filters would grow the cache without limit.
        if len(_cache) > _CACHE_MAX_ENTRIES:
            _cache.clear()
        _cache[key] = (time.monotonic(), version, computed)
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


def _open_states(db: Session, ids: list[int]) -> dict[int, EquipmentState]:
    """The state every machine in scope is in right now, in one query."""
    if not ids:
        return {}
    return {
        s.equipment_id: s
        for s in db.scalars(
            select(EquipmentState).where(
                EquipmentState.equipment_id.in_(ids), EquipmentState.ended_at.is_(None)))
    }


def _line_of(eq: Equipment, cache: dict[int, dict | None]) -> dict | None:
    """The work centre this machine sits under, at whatever depth.

    The floor screen used to answer this by downloading the whole equipment
    tree once a minute and walking it in the browser — every node of the
    plant, to label twenty-four cards. The machine knows its own ancestry;
    it can say so.
    """
    node = eq.parent
    while node is not None:
        if node.id in cache:
            return cache[node.id]
        if node.level == EquipmentLevel.WORK_CENTER:
            cache[node.id] = {"code": node.code, "name": node.name}
            return cache[node.id]
        node = node.parent
    return None


def _matching(machines: list[Equipment], open_states: dict, q: str | None,
              state: str | None) -> list[Equipment]:
    """The machines the grid is asking for, out of the machines in scope.

    Matched here rather than in SQL because the scope is already in memory —
    it has to be, to count the plant honestly — and a second round trip to
    re-select the same rows buys nothing.
    """
    out = machines
    if state:
        out = [eq for eq in out
               if (open_states[eq.id].state.value if eq.id in open_states else "unknown") == state]
    if q:
        needle = q.lower()
        out = [eq for eq in out
               if needle in eq.code.lower() or needle in (eq.name or "").lower()]
    return out


def _build_summary(db: Session, oee_hours: float, line: str | None = None,
                   machine_q: str | None = None, machine_state: str | None = None,
                   machine_limit: int | None = None, machine_offset: int = 0) -> dict:
    scope = _machines(db, line)
    # The open state of every machine in scope, in one query. It is what the
    # "machines running" tile counts and what the state filter matches on, so
    # it is read for the whole scope however small the page is.
    open_states = _open_states(db, [eq.id for eq in scope])
    matching = _matching(scope, open_states, machine_q, machine_state)

    limit = machine_limit if machine_limit is not None else max(len(matching), 1)
    offset = machine_offset if machine_offset < len(matching) else 0
    wanted = matching[offset:offset + limit]

    # OEE is three grouped queries whatever the count, so it is computed for
    # the whole scope: the plant tile has to be the plant's OEE and not the
    # page's. The analog value is one query per machine, so THAT happens for
    # the page only — at 24 machines that is 24 queries and at a thousand it
    # was a thousand, every second, for every screen watching.
    oees = equipment_service.oee_many(db, scope, oee_hours)
    # Whether the MES can still see each machine, in one query for the
    # whole page. A tile that says `unknown` and cannot say why is the
    # thing this answers.
    connections = connection_service.open_connections(db, [eq.id for eq in wanted])
    next_up: dict[int, object] = {}
    for op in workorders.dispatch_list(db):
        next_up.setdefault(op.equipment_id, op)

    machines = []
    lines: dict[int, dict | None] = {}
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
                # A second fact, never folded into the first: a machine can
                # be down and reachable, or fine and unreachable (0028).
                "connection": connection_service.summary(connections.get(eq.id)),
                # The machine's process value under its own name. Not always a
                # temperature: a washer reports WashTemp, a filler FillWeight, a
                # loader FeedRate. Asking every machine for ".Temperature" is why
                # this used to read blank on any line but the cola one.
                "analog": line_service.analog_reading(db, eq),
                # Which line it is on, said by the plant rather than worked out
                # in the browser from a copy of the whole tree.
                "line": _line_of(eq, lines),
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

    # The tiles count the scope, never the page: a grid filtered to six down
    # machines must not report a six-machine plant.
    running = sum(1 for eq in scope
                  if eq.id in open_states and open_states[eq.id].state == EquipmentStateName.RUNNING)
    plant_oee = [oee["oee"] for oee in oees.values() if oee["oee"] is not None]

    return {
        "plant": {
            "machines_total": len(scope),
            "machines_running": running,
            # Counted in the database. This tile used to count the twenty-five
            # orders below it, so a plant with two hundred orders on the floor
            # was told it had twenty-five.
            "active_orders": db.scalar(
                select(func.count()).select_from(WorkOrder).where(WorkOrder.status.in_(_ACTIVE))) or 0,
            "open_ncs": db.scalar(
                select(NonConformance.id).where(NonConformance.status == NcStatus.OPEN).limit(1)
            )
            is not None,
            "erp_pending": db.scalar(select(ErpMessage.id).where(ErpMessage.status == MessageStatus.PENDING).limit(1))
            is not None,
            "oee": round(sum(plant_oee) / len(plant_oee), 4) if plant_oee else None,
        },
        "machines": machines,
        # The same envelope every list in this API uses, so a screen can say
        # "24 of 312 matching, 1,000 in the plant" instead of looking complete.
        "machines_page": {
            "total": len(matching),
            "scope_total": len(scope),
            "limit": len(wanted) if machine_limit is None else machine_limit,
            "offset": offset,
            "has_more": offset + len(wanted) < len(matching),
            "filtered": bool(machine_q or machine_state),
        },
        "orders": orders,
        "audit": audit,
        "non_conformances": open_ncs,
    }
