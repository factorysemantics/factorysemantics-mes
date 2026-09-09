"""Quality checks against specs; failures open non-conformances automatically."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import CheckResult, NcStatus, NonConformance, QualityCheck, QualitySpec
from fsmes.services import Conflict, NotFound, audit, masterdata, workorders


def create_spec(
    session: Session,
    *,
    material_code: str,
    characteristic: str,
    unit: str = "",
    min_value: float | None = None,
    max_value: float | None = None,
    actor: str = "system",
) -> QualitySpec:
    material = masterdata.get_material(session, material_code)
    existing = session.scalar(
        select(QualitySpec).where(QualitySpec.material_id == material.id, QualitySpec.characteristic == characteristic)
    )
    if existing:
        raise Conflict(f"spec for {material_code}/{characteristic} already exists")
    spec = QualitySpec(
        material=material, characteristic=characteristic, unit=unit, min_value=min_value, max_value=max_value
    )
    session.add(spec)
    session.flush()
    audit.record(
        session,
        actor=actor,
        action="qualityspec.created",
        entity_type="material",
        entity_id=material_code,
        after={"characteristic": characteristic, "min": min_value, "max": max_value},
    )
    return spec


def record_check(
    session: Session,
    *,
    material_code: str,
    characteristic: str,
    value: float,
    work_order_code: str | None = None,
    actor: str = "system",
) -> tuple[QualityCheck, NonConformance | None]:
    material = masterdata.get_material(session, material_code)
    spec = session.scalar(
        select(QualitySpec).where(QualitySpec.material_id == material.id, QualitySpec.characteristic == characteristic)
    )
    if spec is None:
        raise NotFound(f"no quality spec for {material_code}/{characteristic}")

    in_spec = (spec.min_value is None or value >= spec.min_value) and (
        spec.max_value is None or value <= spec.max_value
    )
    wo = workorders.get(session, work_order_code) if work_order_code else None
    check = QualityCheck(
        spec=spec,
        work_order_id=wo.id if wo else None,
        value=value,
        result=CheckResult.PASS if in_spec else CheckResult.FAIL,
        checked_by=actor,
    )
    session.add(check)
    session.flush()
    audit.record(
        session,
        actor=actor,
        action="quality.checked",
        entity_type="workorder" if wo else "material",
        entity_id=wo.code if wo else material_code,
        after={"characteristic": characteristic, "value": value, "result": check.result.value},
    )

    nc = None
    if not in_spec:
        nc = open_nc(
            session,
            description=(
                f"{characteristic}={value}{spec.unit} outside [{spec.min_value}, {spec.max_value}] "
                f"for {material_code}" + (f" on order {work_order_code}" if work_order_code else "")
            ),
            work_order_code=work_order_code,
            actor=actor,
        )
    return check, nc


def open_nc(
    session: Session,
    *,
    description: str,
    severity: str = "minor",
    work_order_code: str | None = None,
    actor: str = "system",
) -> NonConformance:
    wo = workorders.get(session, work_order_code) if work_order_code else None
    nc = NonConformance(code="NC-PENDING", description=description[:400], severity=severity,
                        work_order_id=wo.id if wo else None)
    session.add(nc)
    session.flush()
    nc.code = f"NC-{nc.id:05d}"
    audit.record(
        session,
        actor=actor,
        action="nonconformance.opened",
        entity_type="nonconformance",
        entity_id=nc.code,
        after={"description": nc.description, "severity": severity},
    )
    return nc


def close_nc(session: Session, code: str, actor: str = "system") -> NonConformance:
    nc = session.scalar(select(NonConformance).where(NonConformance.code == code))
    if nc is None:
        raise NotFound(f"non-conformance {code!r} not found")
    if nc.status is NcStatus.CLOSED:
        raise Conflict(f"non-conformance {code} is already closed")
    nc.status = NcStatus.CLOSED
    nc.closed_at = utcnow()
    audit.record(
        session,
        actor=actor,
        action="nonconformance.closed",
        entity_type="nonconformance",
        entity_id=code,
        before={"status": "open"},
        after={"status": "closed"},
    )
    return nc
