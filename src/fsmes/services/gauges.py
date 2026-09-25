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

from fsmes import identity
from fsmes.domain import Calibration, CalibrationResult, Gauge, GaugeStatus, QualityCheck
from fsmes.services import Conflict, Invalid, NotFound, audit

#: The rule of ten, and its floor of four. AIAG says a gauge should resolve
#: to about a tenth of the tolerance it judges; ANSI Z540 says a quarter.
#: Both plants are right, nothing off-plant reads the word `adequate`, and a
#: plant follows one standard for every gauge it owns - so they are
#: `[quality] gauge_ratio_adequate` and `gauge_ratio_floor`, shipping the ten
#: and the four that were here.
RATIO_ADEQUATE = 10.0
RATIO_FLOOR = 4.0
#: The calibration interval a newly registered gauge gets when nobody says
#: otherwise. `[quality] gauge_default_interval_days`, 365 by default. Each
#: gauge's own interval is the engineer's and is untouched by this.
DEFAULT_INTERVAL_DAYS = 365
#: How many days before a gauge falls due this product calls it *due soon*
#: when nobody has said otherwise for that gauge. Thirty, which is the number
#: the browser had invented for itself; it is now the default of a column on
#: the gauge, because a quarterly calibration wants a fortnight's warning and
#: an annual one wants two months.
DEFAULT_WARN_DAYS = 30


def _number(session: Session, name: str, fallback):
    """One of this plant's two gauge judgments, read at the moment it is needed.

    Read through `fsmes.services.plant_settings`: the row this plant's own
    administrator edited on Quality's Configuration page, then the setting its
    pack compiled, then the literal above. The session is the caller's own, so
    the reading is one memoised query on a unit of work already open and a
    number saved on the screen is in force on the next reading.
    """
    from fsmes.services import plant_settings

    return plant_settings.value(session, "quality", name, fallback)


def ratios(session: Session) -> tuple[float, float]:
    """`(adequate, floor)` - this plant's two resolution bars.

    Together, because they are one judgment written as two numbers and a
    caller that read one without the other could call a gauge both adequate
    and too coarse.
    """
    return (float(_number(session, "gauge_ratio_adequate", RATIO_ADEQUATE)),
            float(_number(session, "gauge_ratio_floor", RATIO_FLOOR)))


def default_interval_days(session: Session) -> int:
    """The house calibration interval for a gauge nobody gave one."""
    return int(_number(session, "gauge_default_interval_days", DEFAULT_INTERVAL_DAYS))


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
    # The plant's date, not this process's. A gauge falls due at midnight on
    # the shop floor; a server in another zone would call it overdue a few
    # hours early or late, and "overdue" is the word that stops a line.
    today = today or identity.today()
    due = due_on(gauge)
    overdue = due is not None and due < today
    days = (due - today).days if due else None
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
        "days_until_due": days,
        # How much warning this gauge wants, and whether it is inside it. The
        # server answered only `days_until_due` and `overdue` until now, so
        # the browser had invented thirty days for itself at three separate
        # places on one screen - which meant the shop floor's definition of
        # *due soon* lived in JavaScript and nowhere else.
        "warn_days": gauge.warn_days,
        "due_soon": (not overdue and days is not None and 0 <= days <= gauge.warn_days),
    }


def register(session: Session, *, code: str, name: str, kind: str = "general",
             interval_days: int | None = None, resolution: float | None = None,
             location: str | None = None, warn_days: int | None = None,
             actor: str = "system") -> Gauge:
    """Put a gauge on the register.

    `interval_days` and `warn_days` left out are the plant's house answers -
    `[quality] gauge_default_interval_days` and the column's own default -
    rather than numbers this function decides. Both are the gauge's own from
    the moment somebody gives it one.
    """
    if session.scalar(select(Gauge).where(Gauge.code == code)):
        raise Conflict(f"gauge {code} already exists")
    interval_days = (default_interval_days(session) if interval_days is None
                     else interval_days)
    if interval_days <= 0:
        raise Invalid("calibration interval must be positive")
    if warn_days is not None and warn_days < 0:
        raise Invalid("a gauge cannot be warned about after it falls due; "
                      "warn_days counts the days before")

    gauge = Gauge(code=code, name=name, kind=kind, interval_days=interval_days,
                  resolution=resolution, location=location,
                  **({} if warn_days is None else {"warn_days": warn_days}))
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
    when = performed_on or identity.today()

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
        # Counted here rather than in the browser, because "due soon" is a
        # judgment about each gauge's own warning window and the screen had
        # been making it with a literal of its own.
        "due_soon": sum(1 for g in gauges if g["due_soon"]),
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
    adequate, floor = ratios(session)
    return {
        "gauge": code,
        "resolution": gauge.resolution,
        "tolerance": tolerance,
        "ratio": round(ratio, 1),
        "adequate": ratio >= adequate,
        "usable": ratio >= floor,
        # The two bars, beside the verdict they produced: a plant that follows
        # ANSI Z540 rather than AIAG is reading the same word about a
        # different rule, and a reader who cannot see which is being told a
        # verdict with no premise.
        "ratio_adequate": adequate,
        "ratio_floor": floor,
        "verdict": (
            f"resolves {ratio:.0f}:1 against the tolerance - adequate"
            if ratio >= adequate else
            f"resolves only {ratio:.0f}:1 - usable but marginal; "
            f"{adequate:g} to one is this plant's working rule"
            if ratio >= floor else
            f"resolves {ratio:.0f}:1 - too coarse to judge this tolerance. The "
            f"measurement would be mostly the gauge."
        ),
    }
