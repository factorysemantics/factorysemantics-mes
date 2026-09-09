"""Equipment states and OEE (ISO 22400-style: availability x performance x quality)."""

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import case, func, literal, or_, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import Equipment, EquipmentLevel, EquipmentState, EquipmentStateName, ProductionLog
from fsmes.services import audit, masterdata

# Below this much observed runtime history, OEE components are reported as
# unknown rather than computed from a near-zero denominator.
_MIN_WINDOW_SECONDS = 10.0


def set_state(
    session: Session,
    *,
    equipment_code: str,
    state: EquipmentStateName,
    reason: str | None = None,
    actor: str = "system",
) -> EquipmentState:
    """Close the open state interval and start a new one. No-op if unchanged."""
    equipment = masterdata.get_equipment(session, equipment_code)
    current = session.scalar(
        select(EquipmentState).where(EquipmentState.equipment_id == equipment.id, EquipmentState.ended_at.is_(None))
    )
    if current is not None and current.state is state:
        return current
    now = utcnow()
    if current is not None:
        current.ended_at = now
    new = EquipmentState(equipment_id=equipment.id, state=state, reason=reason, started_at=now)
    session.add(new)
    audit.record(
        session,
        actor=actor,
        action="equipment.state_changed",
        entity_type="equipment",
        entity_id=equipment_code,
        before={"state": current.state.value} if current else None,
        after={"state": state.value, "reason": reason},
    )
    return new


def current_states(session: Session) -> list[EquipmentState]:
    return list(
        session.scalars(
            select(EquipmentState).where(EquipmentState.ended_at.is_(None)).order_by(EquipmentState.equipment_id)
        )
    )


def _overlap_seconds(state: EquipmentState, start: datetime, end: datetime) -> float:
    lo = max(state.started_at, start)
    hi = min(state.ended_at or end, end)
    return max(0.0, (hi - lo).total_seconds())


# ---------------------------------------------------------------- aggregates
# The mega-factory found the OEE path's ceiling: eight hours of a 108-station
# plant is 400,000 state intervals, and loading each as an ORM object to add
# up its seconds in Python took the Floor summary from milliseconds to a
# minute, held a pooled connection the whole time, and starved every other
# request. The sums are done in the database now - one grouped query for the
# whole plant - and the rows never leave it.


def _seconds_between(dialect: str, later, earlier):
    """`later - earlier` in seconds, as the database computes it. None when
    this dialect has no known form, in which case the caller sums in Python."""
    if dialect == "sqlite":
        return (func.julianday(later) - func.julianday(earlier)) * 86400.0
    if dialect == "postgresql":
        return func.extract("epoch", later - earlier)
    return None


def _in_window(start: datetime, end: datetime):
    return (
        EquipmentState.started_at < end,
        or_(EquipmentState.ended_at.is_(None), EquipmentState.ended_at > start),
    )


def state_seconds(session: Session, equipment_ids: list[int], start: datetime,
                  end: datetime) -> dict[int, dict[str, float]]:
    """Seconds spent in each state inside [start, end], per machine, with
    every interval clipped to the window. {equipment_id: {state: seconds}};
    a machine with no interval in the window is absent."""
    out: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    if not equipment_ids or end <= start:
        return out
    where = (EquipmentState.equipment_id.in_(equipment_ids), *_in_window(start, end))

    dialect = session.get_bind().dialect.name
    ended = EquipmentState.ended_at.type
    lo = case((EquipmentState.started_at < start, literal(start, ended)), else_=EquipmentState.started_at)
    hi = case((or_(EquipmentState.ended_at.is_(None), EquipmentState.ended_at > end), literal(end, ended)),
              else_=EquipmentState.ended_at)
    seconds = _seconds_between(dialect, hi, lo)
    if seconds is not None:
        rows = session.execute(
            select(EquipmentState.equipment_id, EquipmentState.state, func.sum(seconds))
            .where(*where)
            .group_by(EquipmentState.equipment_id, EquipmentState.state)
        ).all()
        for equipment_id, state, total in rows:
            out[equipment_id][str(getattr(state, "value", state))] += max(0.0, float(total or 0.0))
        return out

    # A dialect without a known interval form: the reference sum, in Python,
    # over bare columns rather than ORM objects.
    rows = session.execute(
        select(EquipmentState.equipment_id, EquipmentState.state,
               EquipmentState.started_at, EquipmentState.ended_at).where(*where)
    ).all()
    for equipment_id, state, started_at, ended_at in rows:
        lo_ = max(started_at, start)
        hi_ = min(ended_at or end, end)
        out[equipment_id][str(getattr(state, "value", state))] += max(0.0, (hi_ - lo_).total_seconds())
    return out


def first_seen(session: Session, equipment_ids: list[int]) -> dict[int, datetime]:
    """When the MES first recorded a state for each machine."""
    if not equipment_ids:
        return {}
    rows = session.execute(
        select(EquipmentState.equipment_id, func.min(EquipmentState.started_at))
        .where(EquipmentState.equipment_id.in_(equipment_ids))
        .group_by(EquipmentState.equipment_id)
    ).all()
    return {equipment_id: seen for equipment_id, seen in rows}


def production_sums(session: Session, equipment_ids: list[int], start: datetime,
                    end: datetime | None = None) -> dict[int, tuple[float, float]]:
    """(good, scrap) booked per machine since `start` (to `end` when given)."""
    if not equipment_ids:
        return {}
    query = (
        select(ProductionLog.equipment_id,
               func.coalesce(func.sum(ProductionLog.good_qty), 0.0),
               func.coalesce(func.sum(ProductionLog.scrap_qty), 0.0))
        .where(ProductionLog.equipment_id.in_(equipment_ids), ProductionLog.ts >= start)
        .group_by(ProductionLog.equipment_id)
    )
    if end is not None:
        query = query.where(ProductionLog.ts <= end)
    return {equipment_id: (float(good), float(scrap))
            for equipment_id, good, scrap in session.execute(query).all()}


def oee_many(session: Session, machines: list[Equipment], hours: float = 8.0) -> dict[str, dict]:
    """OEE for every machine given, over one trailing window, in three
    grouped queries rather than three per machine. {code: oee dict}."""
    end = utcnow()
    asked = end - timedelta(hours=hours)
    ids = [m.id for m in machines]
    seen = first_seen(session, ids)

    # The window never reaches back before the MES started observing a
    # machine: time we have no record of is not downtime.
    starts = {m.id: (end if seen.get(m.id) is None else max(asked, seen[m.id])) for m in machines}
    seconds = state_seconds(session, ids, asked, end)
    # Production is counted from each machine's own clamped start. The usual
    # case - every machine observed for the whole window - is one grouped
    # query; a machine the MES met partway through the window is asked alone.
    whole = [m.id for m in machines if starts[m.id] <= asked]
    made = production_sums(session, whole, asked)
    for m in machines:
        if m.id not in whole and starts[m.id] < end:
            made.update(production_sums(session, [m.id], starts[m.id]))

    out = {}
    for m in machines:
        start = starts[m.id]
        window_seconds = (end - start).total_seconds()
        by_state = seconds.get(m.id, {})
        runtime = by_state.get(EquipmentStateName.RUNNING.value, 0.0)
        downtime = by_state.get(EquipmentStateName.DOWN.value, 0.0)
        good, scrap = made.get(m.id, (0.0, 0.0))
        total = good + scrap

        # Too little observed time to divide by: say "unknown", never "zero".
        availability = runtime / window_seconds if window_seconds >= _MIN_WINDOW_SECONDS else None
        performance = (
            min(1.0, m.ideal_cycle_seconds * total / runtime)
            if runtime > 0 and total > 0 and m.ideal_cycle_seconds
            else None
        )
        quality = good / total if total > 0 else None
        overall = (
            availability * performance * quality
            if None not in (availability, performance, quality)
            else None
        )
        out[m.code] = {
            "equipment": m.code,
            "window_hours": round(window_seconds / 3600, 4),  # effective, after clamping
            "availability": round(availability, 4) if availability is not None else None,
            "performance": round(performance, 4) if performance is not None else None,
            "quality": round(quality, 4) if quality is not None else None,
            "oee": round(overall, 4) if overall is not None else None,
            "good_qty": good,
            "scrap_qty": scrap,
            "runtime_seconds": round(runtime, 1),
            "downtime_seconds": round(downtime, 1),
        }
    return out


def oee(session: Session, *, equipment_code: str, hours: float = 8.0) -> dict:
    """OEE over a trailing window. Components that cannot be computed (no rated
    cycle time, no production) are reported as null rather than guessed.

    The window never reaches back before the MES started observing this
    equipment: time we have no record of is not downtime, and counting it as
    such would make a machine that is running right now report 0% availability.

    One machine's answer is the plant-wide answer restricted to it, so the
    Machine page and the Floor can never disagree.
    """
    equipment = masterdata.get_equipment(session, equipment_code)
    return oee_many(session, [equipment], hours)[equipment.code]


def work_units(session: Session) -> list[Equipment]:
    """Every machine, in code order."""
    return list(session.scalars(
        select(Equipment).where(Equipment.level == EquipmentLevel.WORK_UNIT).order_by(Equipment.code)))
