"""Quality endpoints: specs, checks, non-conformances."""

from datetime import date

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from fsmes.api import paging
from fsmes.api.deps import ActorDep, DbDep, require
from fsmes.domain import Material, NcStatus, NonConformance, QualityCheck, QualitySpec, WorkOrder
from fsmes.services import quality

router = APIRouter()


class SpecIn(BaseModel):
    material: str
    characteristic: str
    unit: str = ""
    min_value: float | None = None
    max_value: float | None = None


@router.get("/specs")
def list_specs(
    db: DbDep,
    material: str | None = None,
    q: str | None = Query(None, description="Match a material code or a characteristic."),
) -> list[SpecIn]:
    query = select(QualitySpec).join(Material, QualitySpec.material_id == Material.id).order_by(
        Material.code, QualitySpec.characteristic)
    if material:
        query = query.where(Material.code == material)
    if q:
        like = f"%{q}%"
        query = query.where(Material.code.like(like) | QualitySpec.characteristic.like(like))
    return [
        SpecIn(
            material=s.material.code,
            characteristic=s.characteristic,
            unit=s.unit,
            min_value=s.min_value,
            max_value=s.max_value,
        )
        for s in db.scalars(query)
    ]


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


@router.post("/checks", status_code=201, dependencies=[require("quality.record")])
def record_check(body: CheckIn, db: DbDep, actor: ActorDep) -> dict:
    check, nc = quality.record_check(
        db,
        material_code=body.material,
        characteristic=body.characteristic,
        value=body.value,
        work_order_code=body.order,
        actor=actor,
    )
    return {"result": check.result, "value": check.value, "non_conformance": nc.code if nc else None}


@router.get("/checks")
def list_checks(
    db: DbDep,
    material: str | None = None,
    characteristic: str | None = None,
    result: str | None = Query(None, description="pass or fail."),
    limit: int = paging.LimitQuery,
    offset: int = paging.OffsetQuery,
) -> dict:
    """Inspection history, newest first.

    Filtering by result is the one a supervisor actually wants: a year of
    passes is not what anybody came here to read.
    """
    query = select(QualityCheck).order_by(QualityCheck.id.desc())
    if result:
        query = query.where(QualityCheck.result == result)
    if material or characteristic:
        query = query.join(QualitySpec)
        if characteristic:
            query = query.where(QualitySpec.characteristic == characteristic)
        if material:
            query = query.join(Material).where(Material.code == material)

    checks, total = paging.paginate(db, query, limit, offset)
    return paging.page(
        [
            {
                "material": c.spec.material.code,
                "characteristic": c.spec.characteristic,
                "value": c.value,
                "result": c.result,
                "checked_by": c.checked_by,
                "ts": c.ts,
            }
            for c in checks
        ],
        total, limit, offset,
    )


@router.get("/nonconformances")
def list_ncs(
    db: DbDep,
    status: NcStatus | None = None,
    q: str | None = Query(None, description="Match a code or a description."),
    limit: int = paging.LimitQuery,
    offset: int = paging.OffsetQuery,
) -> dict:
    """Non-conformances, newest first, one page at a time.

    The paging envelope now: after a day of a 108-station plant this list
    was two thousand rows and half a megabyte on every refresh of the
    Quality screen, and it only grows. The order it was raised on is here
    so a screen can lead somewhere from it.
    """
    query = select(NonConformance).order_by(NonConformance.id.desc())
    if status:
        query = query.where(NonConformance.status == status)
    if q:
        like = f"%{q}%"
        query = query.where(NonConformance.code.like(like) | NonConformance.description.like(like))
    rows, total = paging.paginate(db, query, limit, offset)
    order_ids = {nc.work_order_id for nc in rows if nc.work_order_id}
    orders = {}
    if order_ids:
        orders = {wo.id: wo.code for wo in db.scalars(select(WorkOrder).where(WorkOrder.id.in_(order_ids)))}
    return paging.page([
        {
            "code": nc.code,
            "description": nc.description,
            "severity": nc.severity,
            "status": nc.status,
            "created_at": nc.created_at,
            "closed_at": nc.closed_at,
            "order": orders.get(nc.work_order_id),
        }
        for nc in rows
    ], total, limit, offset)


@router.post("/nonconformances/{code}/close", dependencies=[require("quality.close_nc")])
def close_nc(code: str, db: DbDep, actor: ActorDep) -> dict:
    nc = quality.close_nc(db, code, actor)
    return {"code": nc.code, "status": nc.status}


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
