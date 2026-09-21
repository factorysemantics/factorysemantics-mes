"""The dashboard's data feed.

One endpoint returns everything the operator screen shows, so the page makes a
single request per refresh instead of a dozen — the difference between a
dashboard that scales to a wall panel and one that hammers the API.
"""

import threading
import time

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes import modules
from fsmes.api.deps import ReadDbDep, UserDep
from fsmes.config import get_settings
from fsmes.domain import (
    AuditLog,
    Equipment,
    EquipmentConnection,
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
from fsmes.services import line_clock, masterdata, workorders
from fsmes.services import review as review_service

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
        # A machine going dark closes its state interval rather than opening
        # one, so max(EquipmentState.id) does not move and the floor would
        # have gone on showing it as it was for the life of the cache entry.
        # Same argument as the one above, and the same cost.
        db.scalar(select(func.max(EquipmentConnection.id))),
    )


@router.get("/summary")
def summary(
    db: ReadDbDep,
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
                # be down and reachable, or fine and unreachable (0030).
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
    # The mean is over the machines that have an OEE, and the tile says how
    # many that was. A machine is missing from it for a stated reason - too
    # little of the window watched, no rating, nothing counted, or counted
    # work that will not fit inside its run time - and a plant average that
    # does not say how many machines are behind it turns every one of those
    # reasons into silence.
    plant_oee = [oee["oee"] for oee in oees.values() if oee["oee"] is not None]
    outrun = [code for code, oee in oees.items() if oee.get("counts_outrun_run_time")]

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
            # Of how many, out of how many there are.
            "oee_machines": len(plant_oee),
            "oee_machines_total": len(scope),
            # And how many of the missing ones are missing because their
            # counts outrun their run time, which is the one a reader of a
            # replayed plant most needs pointed at.
            "oee_counts_outrun": len(outrun),
            # Which clock the figures on this screen were computed on. `None`
            # on a plant whose clock is the line's, which is every real one.
            "clock": line_clock.summary(),
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


# --------------------------------------------------- what is waiting on you

#: The kinds of waiting item this endpoint can answer for, and the capability
#: that lets somebody act on each. Read from the one registry that also knows
#: how to *review* each kind (`fsmes.services.review.KINDS`): a panel that can
#: list a kind it cannot show the substance of is the blind signature this
#: product already shipped once. One entry today: the plant's downtime
#: vocabulary. Work instructions, triggers, setpoint adjustments and the
#: design-chat notes are the follow-up - each already has a lifecycle and a
#: screen of its own, and joining them is an entry in that registry, not a new
#: mechanism here.
PENDING_KINDS: dict[str, str] = review_service.capabilities()


@router.get("/pending-approvals")
def pending_approvals(db: ReadDbDep, user: UserDep,
                      limit: int = Query(20, ge=1, le=100)) -> dict:
    """What is waiting that *this caller* may act on, counted, with its total.

    The step the three lifecycles in this product were missing. A document
    draft, a trigger draft and a proposed adjustment each wait on a screen
    somebody has to know to open, nothing tells anybody, and drafting sits
    with supervisors and agents while approving sits with administrators - so
    the proposer and the approver are usually different people and nothing
    crosses between them.

    The rule this answers to: **a pending item appears on the screen of the
    role that can act on it, counted, with its total - and on no screen that
    cannot act on it.** The server decides from the caller's live capabilities
    rather than the screen hiding what the answer already contained, so a
    caller who holds no approve capability is told about nothing, and
    `kinds_total` is zero - which is what the screen reads as *this panel is
    not for you*.

    Per-caller, so it is deliberately not part of the cached `/summary`: that
    payload is shared between everyone watching, and this one may not be.

    A draft nobody acts on waits, visibly. It does not expire, it does not go
    live by itself, and nothing here drops it.
    """
    mine = _kinds_for(db, user)

    items: list[dict] = []
    total = 0
    for kind in mine:
        got, count = review_service.KINDS[kind].waiting(db, limit)
        items.extend(got)
        total += count
    items.sort(key=lambda row: row["waiting_seconds"], reverse=True)
    return {
        "items": items[:limit],
        # The whole queue, not the page. A deep queue that reads short is the
        # failure this panel exists to end.
        "total": total,
        "limit": limit,
        "offset": 0,
        "has_more": len(items[:limit]) < total,
        # Which kinds this caller may act on at all, and how many kinds the
        # product can answer for. Nothing waiting and nothing possible are
        # different answers, and the screen says them differently.
        "kinds": mine,
        "kinds_total": len(mine),
        "kinds_known": sorted(PENDING_KINDS),
    }


def _kinds_for(db: Session, user: dict) -> list[str]:
    """The kinds this caller may act on, from their live capabilities.

    Read here rather than taken from the token, so revoking a power takes
    effect on this panel at once - the same rule `require()` follows.
    """
    from fsmes.services import auth as auth_service

    role = auth_service.current_role(db, user) or user["role"]
    held = auth_service.capabilities_for(db, role)
    return [kind for kind, capability in PENDING_KINDS.items() if capability in held]


@router.get("/pending-approvals/{kind}/{code}/{revision}")
def pending_approval(kind: str, code: str, revision: int,
                     db: ReadDbDep, user: UserDep) -> dict:
    """What one waiting item would actually change, and the walk through it.

    The panel above found the draft; this is what the person reads before
    they sign it. Everything the review needs in one call - the diff against
    the revision it would supersede, how large the plant's list is now and
    after, how much recorded history already carries the code, who drafted it
    and on whose behalf, the one click that puts back what the plant has
    today - and a guide generated from that diff, in the shape the floor
    assistant already plays.

    **Gated by the same capability that lists it.** A caller who cannot sign a
    kind cannot read its drafts here either: a review is a reading of
    configuration that has not been put in force, and a panel that refuses the
    button while showing the substance is a panel that leaks the draft.
    """
    if kind not in PENDING_KINDS:
        raise HTTPException(404, f"nothing here reviews a {kind!r}")
    if kind not in _kinds_for(db, user):
        raise HTTPException(
            403,
            f"reviewing a {kind.replace('_', ' ')} needs the "
            f"{PENDING_KINDS[kind]!r} capability, which this role does not grant")
    return review_service.review(db, kind, code, revision)


# ------------------------------------------- what is configurable in here

@router.get("/config/{domain}/sections")
def config_sections(domain: str, db: ReadDbDep, user: UserDep) -> dict:
    """Everything configurable in one workspace, with its total.

    The list behind the **Configuration** entry in one workspace. Scott, 2026-09-21,
    looking at the nav bar after the downtime vocabulary got a chip of its
    own: one Configuration entry per domain, with that domain's configurable
    sections inside it - not a top-level entry per configurable thing, which
    with the hundreds of sections this product is heading for is a nav bar
    nobody can read.

    Read from `fsmes.modules`, filtered by what this plant serves, so a
    section whose module is switched off is not offered a row that opens onto
    a 404 - and the count of those is returned rather than dropped, because a
    workspace with one section and a workspace with one section and three
    switched off are different plants.

    **This endpoint gates nothing.** Every section is listed to anybody who
    may see the plant, exactly as the screens behind them already list
    themselves; what it adds is `may_define` and `may_approve` per row, read
    live from the caller's role, so a person can see whether the door in
    front of them opens before they walk into it. The gates themselves stay
    on each section's own screen and endpoints.
    """
    from fsmes.services import auth as auth_service

    known = modules.DOMAIN_BY_SLUG.get(domain)
    if known is None:
        raise HTTPException(
            404,
            f"there is no configuration workspace called {domain!r}. "
            f"This version has: {', '.join(sorted(modules.DOMAIN_BY_SLUG))}.")

    served = get_settings().enabled_modules()
    shown = modules.config_sections(domain, served)
    everything = modules.config_sections(domain)

    role = auth_service.current_role(db, user) or user["role"]
    held = auth_service.capabilities_for(db, role)

    return {
        "domain": known.slug,
        "title": known.title,
        "about": known.about,
        "items": [
            {
                "key": section.key,
                "label": section.label,
                "about": section.about,
                "href": section.href,
                "define": section.define,
                "approve": section.approve,
                "may_define": section.define is None or section.define in held,
                "may_approve": section.approve is not None and section.approve in held,
            }
            for section in shown
        ],
        "total": len(shown),
        # Not served here, but part of this version. Named, not just counted:
        # "three sections you cannot see" is a question, and the answer is
        # which modules this plant switched off.
        "switched_off": [s.label for s in everything if s not in shown],
    }
