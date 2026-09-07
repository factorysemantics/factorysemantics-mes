"""Working time: is the plant running, and how much time is there before X.

Two questions, and every promise the MES makes rests on them. A due date that
counts the hours the plant is dark is not a date, it is an arithmetic result.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.domain import CalendarException, ExceptionKind, ShiftPattern
from fsmes.services import Conflict, Invalid, audit

# Scheduling walks forward in steps; a minute is fine for a plant and keeps
# the arithmetic exact rather than approximating with fractions of an hour.
STEP = timedelta(minutes=1)
# A guard so an unschedulable order fails loudly instead of looping for ever
# - a plant with no shifts defined would otherwise hang the planner.
MAX_HORIZON_DAYS = 120


def patterns(session: Session, equipment_id: int | None = None) -> list[ShiftPattern]:
    """Shifts that apply to a machine: its own, plus the site-wide ones."""
    rows = list(session.scalars(
        select(ShiftPattern).where(ShiftPattern.active.is_(True))))
    return [p for p in rows
            if p.equipment_id is None or p.equipment_id == equipment_id]


def _exception_for(session: Session, day: date,
                   equipment_id: int | None) -> CalendarException | None:
    rows = session.scalars(select(CalendarException).where(CalendarException.day == day))
    # A machine-specific exception beats a site-wide one: one line can be down
    # for a rebuild while the rest of the plant runs.
    best = None
    for row in rows:
        if row.equipment_id == equipment_id:
            return row
        if row.equipment_id is None:
            best = row
    return best


def _within(moment: datetime, pattern: ShiftPattern) -> bool:
    """Is this instant inside this shift?"""
    at = moment.time()
    if pattern.crosses_midnight:
        # A night shift belongs to the day it *started*, so the small hours of
        # Saturday are still Friday's shift - which is how a plant counts it
        # and how the roster is written.
        if at >= pattern.starts:
            weekday = moment.weekday()
        elif at < pattern.ends:
            weekday = (moment - timedelta(days=1)).weekday()
        else:
            return False
        return pattern.days[weekday] == "1"
    return pattern.starts <= at < pattern.ends and pattern.days[moment.weekday()] == "1"


def is_working(session: Session, moment: datetime,
               equipment_id: int | None = None) -> bool:
    shifts = patterns(session, equipment_id)
    if not shifts:
        # No calendar defined means the plant is assumed to run continuously.
        # Stated rather than silently assumed, because the alternative -
        # refusing to schedule anything - is worse for a plant mid-setup.
        return True

    exception = _exception_for(session, moment.date(), equipment_id)
    if exception is not None:
        # An overtime day runs regardless of the pattern; a shutdown day does
        # not run regardless of it either.
        return exception.kind is not ExceptionKind.NON_WORKING

    return any(_within(moment, p) for p in shifts)


def next_working(session: Session, moment: datetime,
                 equipment_id: int | None = None) -> datetime:
    """The next instant the plant is running, `moment` included."""
    cursor = moment.replace(second=0, microsecond=0)
    horizon = cursor + timedelta(days=MAX_HORIZON_DAYS)
    while cursor < horizon:
        if is_working(session, cursor, equipment_id):
            return cursor
        cursor += STEP
    raise Conflict(
        f"no working time found within {MAX_HORIZON_DAYS} days of {moment} - "
        f"check the shift patterns and calendar exceptions")


def add_working(session: Session, start: datetime, minutes: float,
                equipment_id: int | None = None) -> datetime:
    """`minutes` of *working* time after `start`.

    This is the function that makes a promised date a promise. Adding clock
    minutes instead would have a six-hour job finishing at two in the morning
    on a plant that stops at ten.
    """
    if minutes < 0:
        raise Invalid("cannot add negative working time")
    cursor = next_working(session, start, equipment_id)
    remaining = float(minutes)
    horizon = cursor + timedelta(days=MAX_HORIZON_DAYS)

    while remaining > 0 and cursor < horizon:
        if is_working(session, cursor, equipment_id):
            remaining -= 1
            cursor += STEP
        else:
            cursor = next_working(session, cursor, equipment_id)
    if remaining > 0:
        raise Conflict(
            f"{minutes} working minutes do not fit within {MAX_HORIZON_DAYS} days")
    return cursor


def working_minutes(session: Session, start: datetime, end: datetime,
                    equipment_id: int | None = None) -> float:
    """How much working time lies between two instants."""
    if end <= start:
        return 0.0
    cursor = start.replace(second=0, microsecond=0)
    total = 0.0
    while cursor < end:
        if is_working(session, cursor, equipment_id):
            total += 1
        cursor += STEP
    return total


def create_pattern(session: Session, *, code: str, name: str, starts: time,
                   ends: time, days: str = "1111100",
                   equipment_code: str | None = None,
                   actor: str = "system") -> ShiftPattern:
    if session.scalar(select(ShiftPattern).where(ShiftPattern.code == code)):
        raise Conflict(f"shift {code} already exists")
    if len(days) != 7 or set(days) - {"0", "1"}:
        raise Invalid("days must be seven characters of 0 or 1, Monday first")

    equipment_id = None
    if equipment_code:
        from fsmes.services import masterdata
        equipment_id = masterdata.get_equipment(session, equipment_code).id

    pattern = ShiftPattern(code=code, name=name, starts=starts, ends=ends,
                           days=days, equipment_id=equipment_id)
    session.add(pattern)
    session.flush()
    audit.record(session, actor=actor, action="shift.created", entity_type="calendar",
                 entity_id=code, after={"starts": str(starts), "ends": str(ends),
                                        "days": days})
    return pattern


def add_exception(session: Session, *, day: date, kind: str, reason: str,
                  equipment_code: str | None = None,
                  actor: str = "system") -> CalendarException:
    try:
        which = ExceptionKind(kind)
    except ValueError as exc:
        raise Invalid(f"unknown exception kind {kind!r}") from exc

    equipment_id = None
    if equipment_code:
        from fsmes.services import masterdata
        equipment_id = masterdata.get_equipment(session, equipment_code).id

    row = CalendarException(day=day, kind=which, reason=reason,
                            equipment_id=equipment_id)
    session.add(row)
    session.flush()
    audit.record(session, actor=actor, action="calendar.exception",
                 entity_type="calendar", entity_id=str(day),
                 after={"kind": which.value, "reason": reason})
    return row


def describe(session: Session, equipment_id: int | None = None) -> dict:
    """The calendar in words, for a screen or an agent."""
    shifts = patterns(session, equipment_id)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return {
        "shifts": [
            {"code": p.code, "name": p.name,
             "starts": str(p.starts), "ends": str(p.ends),
             "days": [names[i] for i, on in enumerate(p.days) if on == "1"],
             "crosses_midnight": p.crosses_midnight,
             "equipment": p.equipment.code if p.equipment else None}
            for p in shifts
        ],
        "exceptions": [
            {"day": str(e.day), "kind": e.kind.value, "reason": e.reason,
             "equipment": e.equipment.code if e.equipment else None}
            for e in session.scalars(
                select(CalendarException).order_by(CalendarException.day))
        ],
        "note": ("no shift patterns defined - the plant is treated as running "
                 "continuously" if not shifts else None),
    }
