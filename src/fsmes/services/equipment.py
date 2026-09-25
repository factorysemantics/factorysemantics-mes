"""Equipment states and OEE (ISO 22400-style: availability x performance x quality)."""

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import Equipment, EquipmentLevel, EquipmentState, EquipmentStateName, ProductionLog
from fsmes.services import audit, calendar, coverage, line_clock, masterdata, outbox
from fsmes.services import connection as connection_service
from fsmes.services import oee as oee_rules

# Below this much observed runtime history, OEE components are reported as
# unknown rather than computed from a near-zero denominator.
_MIN_WINDOW_SECONDS = 10.0


def set_state(
    session: Session,
    *,
    equipment_code: str,
    state: EquipmentStateName,
    reason: str | None = None,
    reason_code: str | None = None,
    actor: str = "system",
) -> EquipmentState:
    """Close the open state interval and start a new one. No-op if unchanged.

    `reason_code` is the plant's own vocabulary; `reason` is the sentence that
    goes with it. Both are stored, never one instead of the other, so every
    reader this product already has - the screens, the pareto's text half, the
    namespace event - keeps reading what it has always read.
    """
    equipment = masterdata.get_equipment(session, equipment_code)
    current = session.scalar(
        select(EquipmentState).where(EquipmentState.equipment_id == equipment.id, EquipmentState.ended_at.is_(None))
    )
    if current is not None and current.state is state:
        return current
    now = utcnow()
    if current is not None:
        current.ended_at = now
    new = EquipmentState(equipment_id=equipment.id, state=state, reason=reason,
                         reason_code=reason_code, started_at=now)
    # The shift the interval began in. It is not re-stamped when the interval
    # closes: an interval that ran past a boundary belongs, as a record, to
    # the shift it started in, and per-shift reporting clips its seconds to
    # the window so the time itself still lands on both shifts.
    calendar.attribute(session, new, now, equipment.id)
    session.add(new)
    session.flush()
    audit.record(
        session,
        actor=actor,
        action="equipment.state_changed",
        entity_type="equipment",
        entity_id=equipment_code,
        before={"state": current.state.value} if current else None,
        after={"state": state.value, "reason": reason, "reason_code": reason_code},
    )
    # The same transaction that moved the machine writes the event, so the
    # namespace can never disagree with the state history about what
    # happened. Nothing is delivered from here; the publisher reads the log.
    outbox.equipment_state_changed(session, equipment=equipment, opened=new, closed=current,
                                   actor=actor)
    return new


def label_stop(
    session: Session,
    *,
    equipment_code: str,
    start: datetime,
    end: datetime | None = None,
    reason: str,
    source: str,
    actor: str = "system",
) -> list[EquipmentState]:
    """Put somebody else's reason code on stops this MES already observed.

    A technician labels a stop in whatever system the plant runs. That label
    belongs on the interval the MES watched, not on a new one: the interval
    is this MES's observation and stays that way, while `reason` and
    `reason_source` record who named it.

    No interval is created here, ever. If nothing was observed in the window,
    this returns an empty list and the caller says so. Manufacturing a
    downtime interval from another system's claim would put seconds into
    availability that this MES never watched, and there would be no way
    afterwards to tell those seconds from the real ones.

    Only intervals that are not already labelled are touched, and only ones
    where the machine was not running: a supplied label never overwrites a
    label somebody here already gave, and never contradicts an observation.
    """
    equipment = masterdata.get_equipment(session, equipment_code)
    window_end = end or utcnow()
    candidates = list(session.scalars(
        select(EquipmentState).where(
            EquipmentState.equipment_id == equipment.id,
            EquipmentState.state != EquipmentStateName.RUNNING,
            EquipmentState.reason.is_(None),
            EquipmentState.started_at < window_end,
            or_(EquipmentState.ended_at.is_(None), EquipmentState.ended_at > start),
        ).order_by(EquipmentState.started_at)
    ))
    if not candidates:
        return []
    for interval in candidates:
        interval.reason = reason[:120]
        interval.reason_source = source[:80]
    audit.record(
        session,
        actor=actor,
        action="equipment.stop_labelled",
        entity_type="equipment",
        entity_id=equipment.code,
        after={"reason": reason, "source": source, "intervals": len(candidates),
               "from": start.isoformat(), "to": end.isoformat() if end else None},
    )
    session.flush()
    return candidates


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
    """When the MES started watching each machine — whichever it recorded
    first, a state or a connection.

    Watching and succeeding are different things. A machine whose agent has
    never once reached its server has no state row, and clamping its window to
    the state history alone would report it as a machine the MES has not been
    watching rather than one it has been failing to see. Both are unknown
    time; only one of them is a fault somebody has to go and fix.
    """
    if not equipment_ids:
        return {}
    rows = session.execute(
        select(EquipmentState.equipment_id, func.min(EquipmentState.started_at))
        .where(EquipmentState.equipment_id.in_(equipment_ids))
        .group_by(EquipmentState.equipment_id)
    ).all()
    seen = {equipment_id: first for equipment_id, first in rows}
    for equipment_id, first in connection_service.first_seen(session, equipment_ids).items():
        if equipment_id not in seen or first < seen[equipment_id]:
            seen[equipment_id] = first
    return seen


def _running_when_booked():
    """1 for a production row whose own instant falls inside one of this
    machine's running intervals, 0 otherwise.

    THE ONE INTERVAL THAT CAN CONTAIN THE BOOKING, NOT A SEARCH FOR ANY. A
    state history is a sequence of intervals that do not overlap: `set_state`
    closes the open one at the instant it opens the next, and nothing in this
    MES backdates a state row. So the only interval that can contain an
    instant is the last one that started at or before it - and if that one had
    already closed, nothing was recorded there at all. This asks the database
    for exactly that row, and reads its state and its end off it.

    It used to ask whether *any* running interval contained the instant, which
    is the same answer and a different amount of work. `EXISTS` cannot stop
    early on a booking that was not running: it scans back through every
    running interval the machine has, finds none that reaches forward far
    enough, and only then says no. That is an index range per booking whose
    length grows with the history, so the cost grows with bookings x history.
    Measured on Scott's bottling plant on 2026-09-18 - 94,000 bookings against
    12,000 state intervals, six hours of one plant - it was **8.2 seconds** for
    one grouped query, and it was the whole of the ten seconds the floor screen
    took. Ordered and limited to one row it is a single index seek per booking:
    **0.07 seconds** on the same data, the same numbers to the unit.

    `ORDER BY ... LIMIT 1` inside a correlated subquery, and the rows still
    never leave the database - the same rule the rest of this module follows.
    """
    latest = (
        select(
            case(
                (
                    and_(
                        EquipmentState.state == EquipmentStateName.RUNNING,
                        or_(EquipmentState.ended_at.is_(None),
                            EquipmentState.ended_at > ProductionLog.ts),
                    ),
                    literal(1),
                ),
                else_=literal(0),
            )
        )
        .where(
            EquipmentState.equipment_id == ProductionLog.equipment_id,
            EquipmentState.started_at <= ProductionLog.ts,
        )
        # `id` breaks the tie when two intervals share a start instant, so the
        # answer does not depend on the order rows come back in.
        .order_by(EquipmentState.started_at.desc(), EquipmentState.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    # No state row at or before the booking at all: the subquery is NULL, the
    # comparison is NULL, and the row counts as booked outside run time -
    # which is what it is. The MES has no record of the machine running then.
    return latest == 1


def production_sums(session: Session, equipment_ids: list[int], start: datetime,
                    end: datetime | None = None) -> dict[int, tuple[float, float, float]]:
    """(good, scrap, counted_outside_run_time) per machine since `start`
    (to `end` when given).

    The third number is how many of those units - good and scrap together -
    the MES booked at an instant its own state history did not have the
    machine running. It is a fact about this MES's two records of the same
    machine, not a claim about the plant: a counter catching up after a stop
    looks like this, and so does run time the MES sampled too coarsely to
    see. It is reported and named; nothing is moved or dropped because of it
    (house rule 1).
    """
    if not equipment_ids:
        return {}
    running = _running_when_booked()
    outside = case((running, 0.0), else_=ProductionLog.good_qty + ProductionLog.scrap_qty)
    query = (
        select(ProductionLog.equipment_id,
               func.coalesce(func.sum(ProductionLog.good_qty), 0.0),
               func.coalesce(func.sum(ProductionLog.scrap_qty), 0.0),
               func.coalesce(func.sum(outside), 0.0))
        .where(ProductionLog.equipment_id.in_(equipment_ids), ProductionLog.ts >= start)
        .group_by(ProductionLog.equipment_id)
    )
    if end is not None:
        query = query.where(ProductionLog.ts <= end)
    return {equipment_id: (float(good), float(scrap), float(outside_qty))
            for equipment_id, good, scrap, outside_qty in session.execute(query).all()}


def oee_many(session: Session, machines: list[Equipment],
             hours: float | None = None) -> dict[str, dict]:
    """OEE for every machine given, over one trailing window, in a handful of
    grouped queries rather than three per machine. {code: oee dict}.

    "A handful" is three, plus one for each distinct instant at which a
    machine was first observed inside the window - which on a plant that has
    been running longer than the window is none at all.

    TWO WINDOWS, AND THEY ANSWER DIFFERENT QUESTIONS. `window_hours` is how
    much history this MES actually holds for the machine - the asked-for span
    clamped to when it started watching - and the run time and the unit counts
    are read over it. **Coverage is stated against the span that was asked
    for**, because that is the question a coverage number exists to answer: of
    the eight hours you asked about, how much did anybody watch? Clamping that
    denominator too would report 100 % coverage on a machine commissioned ten
    minutes ago, which is the reading the ledger exists to stop.
    """
    # None is *this plant's own reporting window* - `[process]
    # default_report_hours` - read here rather than declared in the signature,
    # so the eight hours a caller used to inherit is the plant's answer and not
    # this module's assumption that a shift is eight hours long.
    if hours is None:
        hours = calendar.default_report_hours(session)
    end = utcnow()
    asked = end - timedelta(hours=hours)
    ids = [m.id for m in machines]
    seen = first_seen(session, ids)

    # The account of the asked-for window: every second of it in one
    # disposition, and every unobserved second with a cause on it.
    # Availability is derived from this rather than computed beside it.
    ledgers = coverage.totals_many(session, ids, asked, end, seen)
    the_floor = coverage.floor()
    # 1.0 for every plant whose clock is the line's. Above that, the counts
    # come in at the line's pace and every duration here is wall clock — see
    # `fsmes.services.line_clock`.
    replay = line_clock.factor()

    # The window never reaches back before the MES started observing a
    # machine: time we have no record of is not downtime.
    starts = {m.id: (end if seen.get(m.id) is None else max(asked, seen[m.id])) for m in machines}
    seconds = state_seconds(session, ids, asked, end)
    # The same rule applied to holes in the middle of the window rather than
    # at its start: seconds the MES could not see this machine are not
    # downtime either, and they leave the denominator (decision 0030).
    unknown = connection_service.unknown_seconds(session, ids, asked, end)
    # Production is counted from each machine's own clamped start. The usual
    # case - every machine observed for the whole window - is one grouped
    # query. Machines the MES met partway through the window are grouped by
    # the instant it met them, so a batch commissioned together costs one
    # query between them rather than one each.
    #
    # It used to be one query per such machine, which is invisible on a plant
    # that has been running for days (nothing is partway through an eight-hour
    # window) and is one query per machine in the plant, on every refresh of
    # every screen, on the day a plant stands up. A set, not a list: `in` over
    # a thousand-element list inside a thousand-iteration loop is its own
    # quiet cliff.
    whole = {m.id for m in machines if starts[m.id] <= asked}
    made = production_sums(session, list(whole), asked)
    partway: dict[datetime, list[int]] = {}
    for m in machines:
        if m.id not in whole and starts[m.id] < end:
            partway.setdefault(starts[m.id], []).append(m.id)
    for start, batch in partway.items():
        made.update(production_sums(session, batch, start))

    out = {}
    for m in machines:
        start = starts[m.id]
        window_seconds = (end - start).total_seconds()
        # Clipped to this machine's own clamped window: a disconnection that
        # started before the MES first saw the machine is not inside it.
        unknown_seconds = min(unknown.get(m.id, 0.0), window_seconds)
        observed_seconds = max(0.0, window_seconds - unknown_seconds)
        by_state = seconds.get(m.id, {})
        runtime = by_state.get(EquipmentStateName.RUNNING.value, 0.0)
        downtime = by_state.get(EquipmentStateName.DOWN.value, 0.0)
        good, scrap, outside = made.get(m.id, (0.0, 0.0, 0.0))
        total = good + scrap

        # Derived from the ledger, not computed beside it: run time over the
        # seconds the ledger says somebody was watching. Too little of that to
        # divide by means "unknown", never "zero".
        account = ledgers[m.id]
        availability = account.availability
        cover = account.coverage
        # Never capped, and the note is the sentence the screen puts beside it
        # — see `fsmes.services.oee`. No figure at all when the counted work
        # will not fit inside the run time: the ratio is kept beside it and
        # named, not printed (decision 0026, amended 2026-09-18).
        measured = oee_rules.performance(
            m.ideal_cycle_seconds, total, runtime, outside, replay_factor=replay)
        performance, performance_note = measured.value, measured.note
        quality = good / total if total > 0 else None
        overall = (
            availability * performance * quality
            if None not in (availability, performance, quality)
            else None
        )
        # A pack that sets a coverage floor is asking to be told *unknown*
        # rather than shown a figure built on a window nobody watched. The
        # ledger stays on the object either way: what is withheld is the
        # number, never the evidence.
        coverage_note = coverage.withhold(cover, the_floor)
        if coverage_note:
            availability = performance = quality = overall = None
        out[m.code] = {
            "equipment": m.code,
            "window_hours": round(window_seconds / 3600, 4),  # effective, after clamping
            # How much of that window nobody was watching, and what share of
            # it that is. Beside the figure, never folded into it: 92 %
            # availability over forty observed minutes of an eight-hour
            # window is not the same claim as 92 % over the shift.
            "observed_hours": round(observed_seconds / 3600, 4),
            "unknown_seconds": round(unknown_seconds, 1),
            "unknown_share": (round(unknown_seconds / window_seconds, 4)
                              if window_seconds > 0 else None),
            # How much of the window that was *asked for* this MES saw, and
            # the account behind it. Always present, beside every figure:
            # there is no reading of 92 % availability that survives not
            # knowing it was measured over eleven observed minutes.
            "coverage": round(cover, 4) if cover is not None else None,
            "coverage_floor": the_floor,
            "coverage_note": coverage_note or (None if the_floor else coverage.NO_FLOOR),
            "ledger": account.as_json(),
            "availability": round(availability, 4) if availability is not None else None,
            "performance": round(performance, 4) if performance is not None else None,
            "performance_note": performance_note,
            # The arithmetic, kept whether or not it may be printed. Above 1.0
            # it measures two of this MES's own records disagreeing, not the
            # machine, which is why it is not the figure above.
            "performance_ratio": round(measured.ratio, 4) if measured.ratio is not None else None,
            "counts_outrun_run_time": measured.outruns_run_time,
            "quality": round(quality, 4) if quality is not None else None,
            "oee": round(overall, 4) if overall is not None else None,
            "good_qty": good,
            "scrap_qty": scrap,
            "runtime_seconds": round(runtime, 1),
            "downtime_seconds": round(downtime, 1),
            # Named, never netted off: see `production_sums`.
            "counted_outside_run_time": round(outside, 3),
            # Which clock the rates above were computed on. `None` on every
            # plant whose clock is the line's, which is every real one.
            "clock": line_clock.summary(replay),
        }
    return out


def oee(session: Session, *, equipment_code: str, hours: float | None = None) -> dict:
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
