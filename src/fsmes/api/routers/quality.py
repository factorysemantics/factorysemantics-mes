"""Quality endpoints: specs, checks, non-conformances."""

from datetime import date, datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from fsmes.api import paging
from fsmes.api.deps import ActorDep, DbDep, require
from fsmes.domain import (
    Material,
    NcDisposition,
    NcStatus,
    NonConformance,
    QualityCheck,
    QualitySpec,
    WorkOrder,
)
from fsmes.services import quality

router = APIRouter()


class SpecIn(BaseModel):
    material: str
    characteristic: str
    unit: str = ""
    min_value: float | None = None
    max_value: float | None = None


@router.get("/specs/facets")
def spec_facets(
    db: DbDep,
    material: str | None = Query(None, description="Narrow the characteristics to this material's."),
    limit: int = Query(500, ge=1, le=2000, description="How many of each to name."),
) -> dict:
    """What the Quality screen's filter selects can offer, without reading
    every specification to find out.

    A screen that builds "any material" from the whole specification list has
    fetched the whole table to draw a dropdown - which is the thing this
    endpoint exists to stop. Both lists say their total, so a select that is
    showing the first 500 of 1,240 can say so rather than looking complete.
    """
    materials = db.execute(
        select(Material.code, func.count(QualitySpec.id))
        .join(QualitySpec, QualitySpec.material_id == Material.id)
        .group_by(Material.code)
        .order_by(Material.code)
        .limit(limit)).all()
    materials_total = db.scalar(
        select(func.count(func.distinct(QualitySpec.material_id)))) or 0

    chars = select(QualitySpec.characteristic, func.count(QualitySpec.id))
    if material:
        chars = chars.join(Material, QualitySpec.material_id == Material.id).where(Material.code == material)
    chars = chars.group_by(QualitySpec.characteristic).order_by(QualitySpec.characteristic).limit(limit)

    distinct_chars = select(func.count(func.distinct(QualitySpec.characteristic)))
    if material:
        distinct_chars = distinct_chars.join(
            Material, QualitySpec.material_id == Material.id).where(Material.code == material)

    return {
        "materials": [{"code": code, "specs": n} for code, n in materials],
        "materials_total": materials_total,
        "characteristics": [{"name": name, "specs": n} for name, n in db.execute(chars).all()],
        "characteristics_total": db.scalar(distinct_chars) or 0,
        "specs_total": db.scalar(select(func.count(QualitySpec.id))) or 0,
        "material": material,
        "limit": limit,
    }


@router.get("/specs")
def list_specs(
    db: DbDep,
    material: str | None = None,
    characteristic: str | None = Query(None, description="Exactly this characteristic, on any material."),
    q: str | None = Query(None, description="Match a material code or a characteristic."),
    limit: int = paging.LimitQuery,
    offset: int = paging.OffsetQuery,
) -> dict:
    """Specifications, by material then characteristic, one page at a time.

    This used to answer with every specification the plant has. At 127 that
    was 13 KB and nobody noticed; the screens that read it - the Quality
    workspace, master data, the SPC picker - were each drawing a dropdown out
    of the whole table, which is the habit that does not survive a catalogue
    ten times the size. The envelope is the same one every other list uses,
    so a screen states its total instead of looking complete.
    """
    query = select(QualitySpec).join(Material, QualitySpec.material_id == Material.id).order_by(
        Material.code, QualitySpec.characteristic)
    if material:
        query = query.where(Material.code == material)
    if characteristic:
        query = query.where(QualitySpec.characteristic == characteristic)
    if q:
        like = f"%{q}%"
        query = query.where(Material.code.ilike(like) | QualitySpec.characteristic.ilike(like))
    rows, total = paging.paginate(db, query, limit, offset)
    return paging.page(
        [
            SpecIn(
                material=s.material.code,
                characteristic=s.characteristic,
                unit=s.unit,
                min_value=s.min_value,
                max_value=s.max_value,
            )
            for s in rows
        ],
        total, limit, offset,
    )


@router.post("/specs", status_code=201, dependencies=[require("masterdata.write")])
def create_spec(body: SpecIn, db: DbDep, actor: ActorDep) -> SpecIn:
    quality.create_spec(
        db,
        material_code=body.material,
        characteristic=body.characteristic,
        unit=body.unit,
        min_value=body.min_value,
        max_value=body.max_value,
        actor=actor,
    )
    return body


class CheckIn(BaseModel):
    material: str
    characteristic: str
    value: float
    order: str | None = None
    equipment: str | None = None


@router.post("/checks", status_code=201, dependencies=[require("quality.record")])
def record_check(body: CheckIn, db: DbDep, actor: ActorDep) -> dict:
    """Record one reading, and say what it set off.

    `spc` is the signals recording this reading tripped - a Western Electric
    rule fires on the write, not when somebody next opens the chart, and each
    signal names the hold it raised. Empty is the usual answer and means the
    process is behaving, not that nothing was checked.
    """
    check, nc, signals = quality.record_check(
        db,
        material_code=body.material,
        characteristic=body.characteristic,
        value=body.value,
        work_order_code=body.order,
        equipment_code=body.equipment,
        actor=actor,
    )
    return {"result": check.result, "value": check.value, "non_conformance": nc.code if nc else None,
            "spc": signals}


@router.get("/checks")
def list_checks(
    db: DbDep,
    material: str | None = None,
    characteristic: str | None = None,
    result: str | None = Query(None, description="pass or fail."),
    order: str | None = Query(None, description="Only checks recorded against this work order."),
    since: datetime | None = Query(None, description="Taken at or after this."),
    until: datetime | None = Query(None, description="Taken at or before this."),
    limit: int = paging.LimitQuery,
    offset: int = paging.OffsetQuery,
) -> dict:
    """Inspection history, newest first.

    Filtering by result is the one a supervisor actually wants: a year of
    passes is not what anybody came here to read. A date range is the other
    one - "what did this shift measure" is a question about a window, and
    scrolling back through a quarter to find it is not an answer.

    There is no station filter, and that is not an oversight: a measurement
    records the material, the characteristic, the inspector, the gauge and
    the order it was taken against, and *not* the machine it was taken at.
    Deriving one from the order's route would name a station nobody stood at.
    Filtering by station needs that fact recorded first, which is a schema
    change and somebody's decision, not this endpoint's guess.
    """
    query = select(QualityCheck).order_by(QualityCheck.id.desc())
    if result:
        query = query.where(QualityCheck.result == result)
    if since:
        query = query.where(QualityCheck.ts >= since)
    if until:
        query = query.where(QualityCheck.ts <= until)
    if order:
        query = query.where(QualityCheck.work_order_id.in_(
            select(WorkOrder.id).where(WorkOrder.code == order)))
    if material or characteristic:
        query = query.join(QualitySpec)
        if characteristic:
            query = query.where(QualitySpec.characteristic == characteristic)
        if material:
            query = query.join(Material).where(Material.code == material)

    checks, total = paging.paginate(db, query, limit, offset)
    order_codes = {}
    ids = {c.work_order_id for c in checks if c.work_order_id}
    if ids:
        order_codes = {wo.id: wo.code for wo in db.scalars(select(WorkOrder).where(WorkOrder.id.in_(ids)))}
    return paging.page(
        [
            {
                "material": c.spec.material.code,
                "characteristic": c.spec.characteristic,
                "value": c.value,
                "result": c.result,
                "checked_by": c.checked_by,
                "order": order_codes.get(c.work_order_id),
                "ts": c.ts,
            }
            for c in checks
        ],
        total, limit, offset,
    )


@router.get("/nonconformances")
def list_ncs(
    db: DbDep,
    status: list[NcStatus] | None = Query(
        None, description="Repeatable. A non-conformance in any of these states is returned."),
    q: str | None = Query(None, description="Match a code or a description."),
    limit: int = paging.LimitQuery,
    offset: int = paging.OffsetQuery,
) -> dict:
    """Non-conformances, newest first, one page at a time.

    The paging envelope now: after a day of a 108-station plant this list
    was two thousand rows and half a megabyte on every refresh of the
    Quality screen, and it only grows. The order it was raised on is here
    so a screen can lead somewhere from it.

    `status` is repeatable because "still open" is now three states, not one:
    a supervisor who takes a record under review must not watch it vanish out
    of the list they are working.
    """
    query = select(NonConformance).order_by(NonConformance.id.desc())
    if status:
        query = query.where(NonConformance.status.in_(status))
    if q:
        like = f"%{q}%"
        query = query.where(NonConformance.code.ilike(like) | NonConformance.description.ilike(like))
    rows, total = paging.paginate(db, query, limit, offset)
    order_ids = {nc.work_order_id for nc in rows if nc.work_order_id}
    orders = {}
    if order_ids:
        orders = {wo.id: wo.code for wo in db.scalars(select(WorkOrder).where(WorkOrder.id.in_(order_ids)))}
    return paging.page([_nc_out(nc, orders.get(nc.work_order_id)) for nc in rows],
                       total, limit, offset)


def _nc_out(nc: NonConformance, order: str | None = None) -> dict:
    """One non-conformance, with every step it has been through.

    The history rides along rather than sitting behind a second request: a
    screen that shows a status without showing who put it there invites the
    reader to assume the system decided, and nothing here decides.
    """
    return {
        "code": nc.code,
        "description": nc.description,
        "severity": nc.severity,
        "status": nc.status,
        "created_at": nc.created_at,
        "closed_at": nc.closed_at,
        "order": order,
        "raised_by": nc.raised_by,
        "reviewed_by": nc.reviewed_by,
        "reviewed_at": nc.reviewed_at,
        "disposition": nc.disposition,
        "disposition_reason": nc.disposition_reason,
        "disposition_by": nc.disposition_by,
        "disposition_at": nc.disposition_at,
        "closed_by": nc.closed_by,
        "history": nc.history(),
        "next_steps": _next_steps(nc),
        # What the MES saw, when the MES raised it. Null for one a person
        # raised: they wrote the description, and inventing evidence for them
        # would be worse than having none.
        "evidence": nc.evidence,
    }


def _next_steps(nc: NonConformance) -> list[str]:
    """What may be done to this one next, so a screen need not re-derive the rules."""
    if nc.status is NcStatus.OPEN:
        return ["review", "disposition"]
    if nc.status is NcStatus.UNDER_REVIEW:
        return ["disposition"]
    if nc.status is NcStatus.DISPOSITIONED:
        return ["close"]
    return []


@router.get("/nonconformances/{code}")
def get_nc(code: str, db: DbDep) -> dict:
    """One non-conformance and its history."""
    nc = quality.get_nc(db, code)
    order = None
    if nc.work_order_id:
        wo = db.get(WorkOrder, nc.work_order_id)
        order = wo.code if wo else None
    return _nc_out(nc, order)


@router.post("/nonconformances/{code}/review", dependencies=[require("quality.close_nc")])
def review_nc(code: str, db: DbDep, actor: ActorDep) -> dict:
    """Somebody has picked it up. Recorded against them, with the time."""
    nc = quality.review_nc(db, code, actor)
    return _nc_out(nc)


class DispositionIn(BaseModel):
    disposition: NcDisposition
    reason: str


@router.post("/nonconformances/{code}/disposition", dependencies=[require("quality.close_nc")])
def disposition_nc(code: str, body: DispositionIn, db: DbDep, actor: ActorDep) -> dict:
    """Decide what happens to the material: use as is, rework, scrap or return.

    The reason is required. A concession nobody wrote a reason for is the one
    that cannot be defended when somebody asks about it a year later.
    """
    nc = quality.disposition_nc(db, code, disposition=body.disposition, reason=body.reason, actor=actor)
    return _nc_out(nc)


@router.post("/nonconformances/{code}/close", dependencies=[require("quality.close_nc")])
def close_nc(code: str, db: DbDep, actor: ActorDep) -> dict:
    """Close it — refused until the material has been dispositioned."""
    nc = quality.close_nc(db, code, actor)
    return _nc_out(nc)


# ------------------------------------------------------------ SPC and gauges

class GaugeIn(BaseModel):
    code: str
    name: str
    kind: str = "general"
    interval_days: int = 365
    resolution: float | None = None
    location: str | None = None


class CalibrationIn(BaseModel):
    result: str
    performed_by: str
    performed_on: date | None = None
    certificate: str | None = None
    notes: str | None = None


@router.get("/spc/{material}/{characteristic}")
def spc(material: str, characteristic: str, db: DbDep, limit: int = 200) -> dict:
    """An individuals control chart, with capability and what fired.

    Control limits come from the process's own variation, not the tolerance.
    A process can sit inside spec while drifting badly, and a chart drawn
    against the specification will never show it.
    """
    from fsmes.services import spc as spc_service

    return spc_service.chart(db, material, characteristic, limit)


@router.get("/gauges")
def gauges(
    db: DbDep,
    status: str | None = Query(None, description="in_service, out_of_service or lost."),
    overdue: bool | None = Query(None, description="Only gauges out of calibration (true) or in it (false)."),
    q: str | None = Query(None, description="Match a gauge code, name or location."),
) -> dict:
    """The gauge register, and which are out of calibration.

    The verdict and the overdue count are the whole register's, whatever the
    filter: a filtered view of the gauges must not change what the plant is
    told about its calibration state."""
    from fsmes.services import gauges as gauge_service

    register = gauge_service.register_list(db)
    rows = register["gauges"]
    if status:
        rows = [g for g in rows if g.get("status") == status]
    if overdue is not None:
        rows = [g for g in rows if bool(g.get("overdue")) == overdue]
    if q:
        needle = q.lower()
        rows = [g for g in rows
                if needle in f"{g.get('code', '')} {g.get('name', '')} {g.get('location') or ''}".lower()]
    return {**register, "gauges": rows, "gauges_total": len(register["gauges"])}


@router.post("/gauges", status_code=201, dependencies=[require("masterdata.write")])
def register_gauge(body: GaugeIn, db: DbDep, actor: ActorDep) -> dict:
    from fsmes.services import gauges as gauge_service

    gauge = gauge_service.register(
        db, code=body.code, name=body.name, kind=body.kind,
        interval_days=body.interval_days, resolution=body.resolution,
        location=body.location, actor=actor)
    return {"code": gauge.code, "next_due": gauge_service.due_on(gauge)}


@router.post("/gauges/{code}/calibrate", dependencies=[require("quality.close_nc")])
def calibrate(code: str, body: CalibrationIn, db: DbDep, actor: ActorDep) -> dict:
    """Record a calibration.

    A gauge found out of tolerance comes off the floor, and the response lists
    the measurements it invalidated - a plant that cannot bound that ends up
    quarantining far more than it needs to.
    """
    from fsmes.services import gauges as gauge_service

    return gauge_service.calibrate(
        db, code, result=body.result, performed_by=body.performed_by,
        performed_on=body.performed_on, certificate=body.certificate,
        notes=body.notes, actor=actor)


@router.get("/gauges/{code}/impact")
def gauge_impact(code: str, db: DbDep) -> dict:
    """Everything this gauge measured since its last calibration."""
    from fsmes.services import gauges as gauge_service

    return gauge_service.impact(db, code)


@router.get("/gauges/{code}/resolution")
def gauge_resolution(code: str, tolerance: float, db: DbDep) -> dict:
    """Can this gauge judge this tolerance? The rule of ten."""
    from fsmes.services import gauges as gauge_service

    return gauge_service.resolution_check(db, code, tolerance)
