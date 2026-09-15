"""Quality checks against specs; failures open non-conformances automatically.

A non-conformance then has a life: somebody picks it up, somebody decides what
happens to the material, and only then is it closed. `review`, `disposition`
and `close` are the three steps, each recorded against the person who took it.
Decision record 0024 says why closing without a disposition is refused.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import (
    CheckResult,
    NcDisposition,
    NcStatus,
    NonConformance,
    QualityCheck,
    QualitySpec,
)
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
    equipment_code: str | None = None,
    actor: str = "system",
) -> tuple[QualityCheck, NonConformance | None, list[dict]]:
    """One reading, judged against the specification, and what it set off.

    Three things come back: the check, the non-conformance the reading opened
    if it was out of spec, and the SPC signals recording it caused. The rules
    run here, on the write, not only when somebody opens the chart - a
    control chart that only computes on demand tells whoever happened to
    look, which is nobody at two in the morning. See decision record 0027.
    """
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
    station = masterdata.get_equipment(session, equipment_code) if equipment_code else None
    check = QualityCheck(
        spec=spec,
        work_order_id=wo.id if wo else None,
        equipment_id=station.id if station else None,
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
    # The rules see this reading now, while the line is still running it.
    from fsmes.services import spc as spc_service

    signals = spc_service.evaluate(session, spec, since_id=check.id)
    return check, nc, signals


def open_nc(
    session: Session,
    *,
    description: str,
    severity: str = "minor",
    work_order_code: str | None = None,
    actor: str = "system",
    evidence: dict | None = None,
) -> NonConformance:
    """Raise one. `evidence` is what the MES saw, when the MES raised it itself.

    A record opened by a rule rather than by a person has to carry the reason
    it was opened, or a supervisor is being asked to take the machine's word
    for it. A person raising one writes the description; their evidence stays
    null rather than invented.
    """
    wo = workorders.get(session, work_order_code) if work_order_code else None
    nc = NonConformance(code="NC-PENDING", description=description[:400], severity=severity,
                        work_order_id=wo.id if wo else None, raised_by=actor, evidence=evidence)
    session.add(nc)
    session.flush()
    nc.code = f"NC-{nc.id:05d}"
    audit.record(
        session,
        actor=actor,
        action="nonconformance.opened",
        entity_type="nonconformance",
        entity_id=nc.code,
        after={"description": nc.description, "severity": severity,
               "raised_by_rule": (evidence or {}).get("rule")},
    )
    return nc


def get_nc(session: Session, code: str) -> NonConformance:
    """One non-conformance by its code, or a NotFound naming it."""
    nc = session.scalar(select(NonConformance).where(NonConformance.code == code))
    if nc is None:
        raise NotFound(f"non-conformance {code!r} not found")
    return nc


def review_nc(session: Session, code: str, actor: str = "system") -> NonConformance:
    """Somebody has picked this up. open -> under review."""
    nc = get_nc(session, code)
    if nc.status is not NcStatus.OPEN:
        raise Conflict(f"non-conformance {code} is {nc.status.value}, not open")
    before = nc.status.value
    nc.status = NcStatus.UNDER_REVIEW
    nc.reviewed_by = actor
    nc.reviewed_at = utcnow()
    audit.record(
        session,
        actor=actor,
        action="nonconformance.under_review",
        entity_type="nonconformance",
        entity_id=code,
        before={"status": before},
        after={"status": nc.status.value},
    )
    return nc


def disposition_nc(
    session: Session,
    code: str,
    *,
    disposition: NcDisposition | str,
    reason: str,
    actor: str = "system",
) -> NonConformance:
    """Decide what happens to the material. open or under review -> dispositioned.

    The reason is required, not decorated. `use_as_is` on a batch that failed
    its specification is a concession somebody has to be able to defend a year
    later, and a blank reason is how that becomes undefendable.
    """
    nc = get_nc(session, code)
    if nc.status in (NcStatus.DISPOSITIONED, NcStatus.CLOSED):
        raise Conflict(
            f"non-conformance {code} is already {nc.status.value}"
            + (f" ({nc.disposition.value})" if nc.disposition else "")
        )
    try:
        chosen = NcDisposition(disposition)
    except ValueError:
        allowed = ", ".join(d.value for d in NcDisposition)
        raise Conflict(f"{disposition!r} is not a disposition; choose one of: {allowed}") from None
    if not (reason or "").strip():
        raise Conflict(f"a {chosen.value} disposition needs a reason")

    before = nc.status.value
    nc.status = NcStatus.DISPOSITIONED
    nc.disposition = chosen
    nc.disposition_reason = reason.strip()[:400]
    nc.disposition_by = actor
    nc.disposition_at = utcnow()
    audit.record(
        session,
        actor=actor,
        action="nonconformance.dispositioned",
        entity_type="nonconformance",
        entity_id=code,
        before={"status": before},
        after={"status": nc.status.value, "disposition": chosen.value, "reason": nc.disposition_reason},
    )
    return nc


def close_nc(session: Session, code: str, actor: str = "system") -> NonConformance:
    """Close it. Only after a disposition — see decision record 0024.

    Closing an undispositioned non-conformance says the material question was
    answered when nobody answered it. The refusal names the step that is
    missing rather than the rule that was broken.
    """
    nc = get_nc(session, code)
    if nc.status is NcStatus.CLOSED:
        raise Conflict(f"non-conformance {code} is already closed")
    if nc.disposition is None:
        raise Conflict(
            f"non-conformance {code} has no disposition yet: decide what happens to the "
            f"material ({', '.join(d.value for d in NcDisposition)}) before closing it"
        )
    before = nc.status.value
    nc.status = NcStatus.CLOSED
    nc.closed_by = actor
    nc.closed_at = utcnow()
    audit.record(
        session,
        actor=actor,
        action="nonconformance.closed",
        entity_type="nonconformance",
        entity_id=code,
        before={"status": before},
        after={"status": "closed", "disposition": nc.disposition.value},
    )
    return nc
