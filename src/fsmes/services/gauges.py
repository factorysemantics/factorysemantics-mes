"""Gauge control: due dates, and what a failed calibration invalidates.

The consequential part is `impact`. When a gauge is found out of tolerance,
every measurement it took since its last good calibration is suspect - and a
plant that cannot list those measurements cannot bound the problem, so it ends
up quarantining far more than it needs to.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.domain import Calibration, CalibrationResult, Gauge, GaugeStatus, QualityCheck
from fsmes.services import Conflict, Invalid, NotFound, audit


def get(session: Session, code: str) -> Gauge:
    gauge = session.scalar(select(Gauge).where(Gauge.code == code))
    if gauge is None:
        raise NotFound(f"no gauge {code}")
    return gauge


def due_on(gauge: Gauge) -> date | None:
    if gauge.last_calibrated is None:
        return None
    return gauge.last_calibrated + timedelta(days=gauge.interval_days)


def _out(session: Session, gauge: Gauge, today: date | None = None) -> dict:
    today = today or date.today()
    due = due_on(gauge)
    overdue = due is not None and due < today
    return {
        "code": gauge.code,
        "name": gauge.name,
        "kind": gauge.kind,
        "location": gauge.location,
        "status": gauge.status.value,
        "resolution": gauge.resolution,
        "interval_days": gauge.interval_days,
        "last_calibrated": gauge.last_calibrated,
        "due_on": due,
        # A gauge never calibrated is not "fine until proven otherwise".
        "never_calibrated": gauge.last_calibrated is None,
        "overdue": overdue or gauge.last_calibrated is None,
        "days_until_due": (due - today).days if due else None,
    }


def register(session: Session, *, code: str, name: str, kind: str = "general",
             interval_days: int = 365, resolution: float | None = None,
             location: str | None = None, actor: str = "system") -> Gauge:
    if session.scalar(select(Gauge).where(Gauge.code == code)):
        raise Conflict(f"gauge {code} already exists")
    if interval_days <= 0:
        raise Invalid("calibration interval must be positive")

    gauge = Gauge(code=code, name=name, kind=kind, interval_days=interval_days,
                  resolution=resolution, location=location)
    session.add(gauge)
    session.flush()
    audit.record(session, actor=actor, action="gauge.registered", entity_type="gauge",
                 entity_id=code, after={"interval_days": interval_days})
    return gauge


def calibrate(session: Session, code: str, *, result: str, performed_by: str,
              performed_on: date | None = None, certificate: str | None = None,
              notes: str | None = None, actor: str = "system") -> dict:
    """Record a calibration, and say what a failure invalidates."""
    try:
        outcome = CalibrationResult(result)
    except ValueError as exc:
        raise Invalid(
            f"unknown result {result!r}. Expected one of "
            f"{', '.join(r.value for r in CalibrationResult)}") from exc

    gauge = get(session, code)
    previous = gauge.last_calibrated
    when = performed_on or date.today()

    session.add(Calibration(gauge_id=gauge.id, performed_on=when, result=outcome,
                            performed_by=performed_by, certificate=certificate,
                            notes=notes))
    gauge.last_calibrated = when
    if outcome is CalibrationResult.FAIL_AS_FOUND:
        # Found out of tolerance: it comes off the floor until somebody decides
        # otherwise. Leaving it in service is how bad data keeps arriving.
        gauge.status = GaugeStatus.OUT_OF_SERVICE
    else:
        gauge.status = GaugeStatus.IN_SERVICE
    session.flush()

    audit.record(session, actor=actor, action="gauge.calibrated", entity_type="gauge",
                 entity_id=code,
                 after={"result": outcome.value, "performed_by": performed_by,
                        "certificate": certificate})

    affected = None
    if outcome is CalibrationResult.FAIL_AS_FOUND:
        affected = impact(session, code, since=previous)
    return {"gauge": code, "result": outcome.value, "status": gauge.status.value,
            "next_due": due_on(gauge), "suspect_measurements": affected}


def impact(session: Session, code: str, since: date | None = None) -> dict:
    """Measurements taken by this gauge since its last good calibration.

    A plant that cannot list these cannot bound the problem, and ends up
    quarantining far more than it needs to.
    """
    gauge = get(session, code)
    query = select(QualityCheck).where(QualityCheck.gauge_id == gauge.id)
    if since is not None:
        from datetime import datetime, time
        query = query.where(QualityCheck.ts >= datetime.combine(since, time.min))

    checks = list(session.scalars(query.order_by(QualityCheck.id)))
    return {
        "gauge": code,
        "since": since,
        "count": len(checks),
        # By id then code: QualityCheck holds the foreign key, not a
        # relationship, and inventing one here would be a schema change to
        # save a lookup.
        "orders": _order_codes(session, checks),
        "note": ("every one of these was measured with a gauge later found out "
                 "of tolerance and should be re-judged"
                 if checks else "no measurements recorded against this gauge"),
    }


def _order_codes(session: Session, checks: list[QualityCheck]) -> list[str]:
    from fsmes.domain import WorkOrder

    ids = {c.work_order_id for c in checks if c.work_order_id}
    if not ids:
        return []
    return sorted(o.code for o in session.scalars(
        select(WorkOrder).where(WorkOrder.id.in_(ids))))


def register_list(session: Session, today: date | None = None) -> dict:
    gauges = [_out(session, g, today)
              for g in session.scalars(select(Gauge).order_by(Gauge.code))]
    overdue = [g for g in gauges if g["overdue"]]
    return {
        "gauges": gauges,
        "overdue": len(overdue),
        "verdict": (f"{len(overdue)} gauge(s) are out of calibration and still "
                    f"on the floor" if overdue else "every gauge is in calibration"),
    }


def resolution_check(session: Session, code: str, tolerance: float) -> dict:
    """Can this gauge judge this tolerance?

    The rule of ten: a gauge should resolve to about a tenth of the tolerance
    it is judging. Four is the usual floor. Below that the measurement is
    mostly the gauge, and the control chart is charting the instrument.
    """
    gauge = get(session, code)
    if gauge.resolution is None or gauge.resolution <= 0:
        return {"gauge": code, "verdict": "gauge resolution is not recorded"}
    ratio = tolerance / gauge.resolution
    return {
        "gauge": code,
        "resolution": gauge.resolution,
        "tolerance": tolerance,
        "ratio": round(ratio, 1),
        "adequate": ratio >= 10,
        "usable": ratio >= 4,
        "verdict": (
            f"resolves {ratio:.0f}:1 against the tolerance - adequate"
            if ratio >= 10 else
            f"resolves only {ratio:.0f}:1 - usable but marginal; ten to one is "
            f"the working rule" if ratio >= 4 else
            f"resolves {ratio:.0f}:1 - too coarse to judge this tolerance. The "
            f"measurement would be mostly the gauge."
        ),
    }
