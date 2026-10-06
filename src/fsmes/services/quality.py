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
    QualitySample,
    QualitySpec,
)
from fsmes.services import (
    Conflict,
    Invalid,
    NotFound,
    WrongSampleSize,
    audit,
    calendar,
    masterdata,
    workorders,
)
from fsmes.services import gauges as gauge_service

#: What this plant calls a non-conformance on the record itself. `NC` by
#: default, giving `NC-00017`, which is what was here before the
#: configuration audit of 2026-09-21 named it: a plant that calls them NCRs
#: changes only its own `code` strings, and nothing off-plant is keyed on the
#: prefix. The width of the number after it stays the product's.
NC_CODE_PREFIX = "NC"


def nc_code_prefix(session: Session) -> str:
    """What this plant calls a non-conformance on the record.

    Read through `fsmes.services.plant_settings`: the row this plant's own
    administrator edited on Quality's Configuration page, then the setting its
    pack compiled, then the literal above. The session is the caller's own, so
    the reading is one memoised query on a unit of work already open and a
    number saved on the screen is in force on the next reading.
    """
    from fsmes.services import plant_settings

    return str(plant_settings.value(session, "quality", "nc_code_prefix",
                                    NC_CODE_PREFIX) or NC_CODE_PREFIX)


def create_spec(
    session: Session,
    *,
    material_code: str,
    characteristic: str,
    unit: str = "",
    min_value: float | None = None,
    max_value: float | None = None,
    sample_size: int | None = None,
    actor: str = "system",
) -> QualitySpec:
    """One characteristic of one material, and how it is inspected.

    `sample_size` is the sampling plan: how many pieces are measured at a
    time. Left out, or one, means one piece at a time, which is what every
    specification written before this existed says and is charted exactly as
    it always was. Above one the readings are a subgroup and the chart is
    X-bar and R (decision 0040). Refused above ten, because the range stops
    being a decent estimate of spread there and this product has no constants
    past it - refused when the specification is written rather than when
    somebody opens the chart and finds it blank.
    """
    if sample_size is not None and not 1 <= sample_size <= spc_max_sample_size():
        raise Invalid(
            f"a sample size of {sample_size} is not something this product can "
            f"chart; it inspects one piece at a time (1, or nothing) or a sample "
            f"of 2 to {spc_max_sample_size()}")
    material = masterdata.get_material(session, material_code)
    existing = session.scalar(
        select(QualitySpec).where(QualitySpec.material_id == material.id, QualitySpec.characteristic == characteristic)
    )
    if existing:
        raise Conflict(f"spec for {material_code}/{characteristic} already exists")
    spec = QualitySpec(
        material=material, characteristic=characteristic, unit=unit, min_value=min_value,
        max_value=max_value, sample_size=sample_size
    )
    session.add(spec)
    session.flush()
    audit.record(
        session,
        actor=actor,
        action="qualityspec.created",
        entity_type="material",
        entity_id=material_code,
        after={"characteristic": characteristic, "min": min_value, "max": max_value,
               "sample_size": sample_size},
    )
    return spec


def spc_max_sample_size() -> int:
    """The largest sample this product has constants for. See `services.spc`."""
    from fsmes.services import spc as spc_service

    return spc_service.MAX_SAMPLE_SIZE


def get_spec(session: Session, material_code: str, characteristic: str) -> QualitySpec:
    """The specification for one characteristic of one material."""
    material = masterdata.get_material(session, material_code)
    spec = session.scalar(
        select(QualitySpec).where(QualitySpec.material_id == material.id,
                                  QualitySpec.characteristic == characteristic)
    )
    if spec is None:
        raise NotFound(f"no quality spec for {material_code}/{characteristic}")
    return spec


def _store_reading(
    session: Session,
    *,
    spec: QualitySpec,
    value: float,
    wo,
    station,
    instrument,
    now,
    actor: str,
    material_code: str,
    sample: QualitySample | None = None,
) -> tuple[QualityCheck, bool]:
    """One reading, stored and audited. Shared by the single check and the sample.

    Every reading is a row in `quality_checks` whether it was taken on its own
    or as one of five, because the measurements card, the inspection history,
    the certificate of analysis, the gauge impact list and every count of
    checks in this product read that table. A sample does not hide its
    readings; it ties them together with `sample_id`.
    """
    in_spec = (spec.min_value is None or value >= spec.min_value) and (
        spec.max_value is None or value <= spec.max_value
    )
    check = QualityCheck(
        spec=spec,
        work_order_id=wo.id if wo else None,
        equipment_id=station.id if station else None,
        gauge_id=instrument.id if instrument else None,
        sample_id=sample.id if sample else None,
        value=value,
        result=CheckResult.PASS if in_spec else CheckResult.FAIL,
        checked_by=actor,
        ts=now,
    )
    # Written from the same instant the row carries, so the shift on the
    # record and the timestamp on it can never disagree. Against the station's
    # own roster when the check names a station, because a line running its
    # own shifts measures its checks on those; site-wide otherwise.
    calendar.attribute(session, check, now, station.id if station else None)
    session.add(check)
    session.flush()
    audit.record(
        session,
        actor=actor,
        action="quality.checked",
        entity_type="workorder" if wo else "material",
        entity_id=wo.code if wo else material_code,
        after={"characteristic": spec.characteristic, "value": value,
               "result": check.result.value,
               "gauge": instrument.code if instrument else None,
               **({"sample": sample.id} if sample else {})},
    )
    return check, in_spec


def record_check(
    session: Session,
    *,
    material_code: str,
    characteristic: str,
    value: float,
    work_order_code: str | None = None,
    equipment_code: str | None = None,
    gauge_code: str | None = None,
    actor: str = "system",
) -> tuple[QualityCheck, NonConformance | None, list[dict]]:
    """One reading, judged against the specification, and what it set off.

    Three things come back: the check, the non-conformance the reading opened
    if it was out of spec, and the SPC signals recording it caused. The rules
    run here, on the write, not only when somebody opens the chart - a
    control chart that only computes on demand tells whoever happened to
    look, which is nobody at two in the morning. See decision record 0027.

    `gauge_code` is the instrument that took the reading. Left out, the check
    says *not recorded* - which is the honest answer and is not the same as
    saying nothing took it. An unknown code is refused rather than dropped: a
    reading attributed to a gauge this plant does not have would read, on the
    chart, exactly like a reading attributed to one it does. A gauge that is
    overdue or out of service is **not** refused: those readings happen on
    real floors, they are what `GET /quality/gauges/{code}/impact` exists to
    bound, and refusing them here would simply leave the gauge unnamed.

    A characteristic whose sampling plan says *n at a time* is refused here.
    One reading of a subgroup of five is not a point on its chart, and
    storing it as if it were would put a single bottle on a chart of means
    and quietly widen every limit on it. `record_sample` is the way in.
    """
    spec = get_spec(session, material_code, characteristic)
    size = spec.sample_size or 1
    if size > 1:
        raise Invalid(
            f"{material_code}/{characteristic} is inspected {size} pieces at a "
            f"time; post all {size} readings together to /quality/samples "
            f"rather than one check at a time")

    wo = workorders.get(session, work_order_code) if work_order_code else None
    station = masterdata.get_equipment(session, equipment_code) if equipment_code else None
    instrument = gauge_service.get(session, gauge_code) if gauge_code else None
    now = utcnow()
    check, in_spec = _store_reading(
        session, spec=spec, value=value, wo=wo, station=station, instrument=instrument,
        now=now, actor=actor, material_code=material_code)

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


def record_sample(
    session: Session,
    *,
    material_code: str,
    characteristic: str,
    values: list[float],
    work_order_code: str | None = None,
    equipment_code: str | None = None,
    gauge_code: str | None = None,
    actor: str = "system",
) -> tuple[QualitySample, list[QualityCheck], NonConformance | None, list[dict]]:
    """One sample of n pieces, recorded whole, judged as one observation.

    The n readings are stored one row each in `quality_checks` - everything in
    this product that counts checks or lists measurements keeps telling the
    truth - and tied together by a `quality_samples` row carrying the stamp,
    the order, the station and the gauge they share. The chart's point is
    their mean, so the rules run **once**, on that mean, and this returns at
    most one non-conformance however many of the readings were interesting:
    five bottles measured together are one look at the process.

    Exactly `sample_size` readings, or `WrongSampleSize` with both numbers in
    the sentence. Fewer is not a smaller sample - a mean of three charted
    against limits built for five is wrong in a way nobody would see - and
    more is not a bigger one.
    """
    spec = get_spec(session, material_code, characteristic)
    size = spec.sample_size or 1
    if size < 2:
        raise Invalid(
            f"{material_code}/{characteristic} is inspected one piece at a time; "
            f"post single readings to /quality/checks, or give the specification "
            f"a sample size first")
    if len(values) != size:
        raise WrongSampleSize(
            f"{material_code}/{characteristic} is inspected {size} pieces at a "
            f"time and this sample has {len(values)} reading(s); send exactly "
            f"{size}")

    wo = workorders.get(session, work_order_code) if work_order_code else None
    station = masterdata.get_equipment(session, equipment_code) if equipment_code else None
    instrument = gauge_service.get(session, gauge_code) if gauge_code else None
    now = utcnow()

    sample = QualitySample(
        spec=spec,
        work_order_id=wo.id if wo else None,
        equipment_id=station.id if station else None,
        gauge_id=instrument.id if instrument else None,
        checked_by=actor,
        ts=now,
    )
    calendar.attribute(session, sample, now, station.id if station else None)
    session.add(sample)
    session.flush()

    checks: list[QualityCheck] = []
    out_of_spec: list[float] = []
    for value in values:
        check, in_spec = _store_reading(
            session, spec=spec, value=value, wo=wo, station=station,
            instrument=instrument, now=now, actor=actor,
            material_code=material_code, sample=sample)
        checks.append(check)
        if not in_spec:
            out_of_spec.append(value)
    session.flush()

    nc = None
    if out_of_spec:
        # One hold for the sample, naming the readings that were outside, not
        # one hold per bottle. The sample is the observation.
        nc = open_nc(
            session,
            description=(
                f"{characteristic} outside [{spec.min_value}, {spec.max_value}] for "
                f"{material_code}: {len(out_of_spec)} of {size} readings in sample "
                f"{sample.id} ("
                + ", ".join(f"{v}{spec.unit}" for v in out_of_spec) + ")"
                + (f" on order {work_order_code}" if work_order_code else "")
            ),
            work_order_code=work_order_code,
            actor=actor,
            evidence={"source": "quality", "material": material_code,
                      "characteristic": characteristic, "unit": spec.unit,
                      "sample": sample.id, "sample_size": size,
                      "values": list(values), "outside": out_of_spec,
                      "gauge": instrument.code if instrument else None},
        )

    # The rules see this sample now, while the line is still running it.
    from fsmes.services import spc as spc_service

    signals = spc_service.evaluate(session, spec, since_sample=sample.id)
    return sample, checks, nc, signals


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

    **The severity is checked against the plant's own list, once it has one.**
    A plant that has approved no severity behaves exactly as this did before
    the list existed: the column takes what it is given. A plant that has one
    may only raise a record at a word on it - and no record already raised is
    read or rewritten, because a hold graded `major` last March was graded
    `major`, whatever the plant calls things now.
    """
    from fsmes.services import severities

    severity = severities.validate(session, severity)
    wo = workorders.get(session, work_order_code) if work_order_code else None
    now = utcnow()
    nc = NonConformance(code="NC-PENDING", description=description[:400], severity=severity,
                        work_order_id=wo.id if wo else None, raised_by=actor,
                        evidence=evidence, created_at=now)
    # The shift it was raised in. Review, disposition and close happen on
    # other shifts and other days; those steps keep their own timestamps and
    # no shift, because the question a per-shift quality report asks is which
    # shift found the problem.
    calendar.attribute(session, nc, now)
    session.add(nc)
    session.flush()
    nc.code = f"{nc_code_prefix(session)}-{nc.id:05d}"
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
