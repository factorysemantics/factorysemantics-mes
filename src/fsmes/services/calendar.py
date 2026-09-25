"""Working time: is the plant running, which shift is this, and how long until X.

Three questions, and every promise the MES makes rests on them. A due date that
counts the hours the plant is dark is not a date, it is an arithmetic result.
And a production number with no shift on it is a number no supervisor can act
on: the shift is the unit a plant is run and measured in.

WHICH CLOCK. A shift that starts at six starts at six *in the plant*. Every
instant the MES stores is naive UTC, so each one is turned into the plant's
own wall clock - `MES_PLANT_TIMEZONE`, or this machine's zone when nothing
set it - before it is compared with a shift's times or with the day of a
calendar exception. Before this, a plant five hours from Greenwich had its
day shift start at one in the morning and nobody could see why.

WHICH SHIFT. `shift_for` answers it for one instant and `occurrences` lays the
shifts out over a span, both in the plant's own clock. Three rules hold, and
`docs/decisions/0028` says why:

* A shift is **half-open**, `[starts, ends)`. A unit counted at the second the
  night shift begins belongs to the night shift, and to only one shift.
* A shift that crosses midnight belongs to the **day it started**, which is how
  a roster is written and how a plant counts it.
* No pattern covering an instant is **not attributed**, and says so. It is not
  folded into the nearest shift: an hour nobody rostered is a finding about the
  calendar, and inventing a shift for it would hide the finding (house rule 2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes import identity
from fsmes.domain import CalendarException, ExceptionKind, ShiftPattern
from fsmes.services import Conflict, Invalid, audit, plant_settings

# Scheduling walks forward in steps; a minute is fine for a plant and keeps
# the arithmetic exact rather than approximating with fractions of an hour.
STEP = timedelta(minutes=1)
# A guard so an unschedulable order fails loudly instead of looping for ever
# - a plant with no shifts defined would otherwise hang the planner.
MAX_HORIZON_DAYS = 120

# How far back `shift=previous` will look for a shift that has ended. Two weeks
# covers a plant that ran nothing over a shutdown; past that, asking for "the
# previous shift" is asking a question with no useful answer, and saying so
# beats returning a fortnight-old window as if it were the last one. A seasonal
# plant with a six-week shutdown answers differently, which is why this is the
# literal the product ships rather than the whole answer - see
# `previous_horizon_days` below.
PREVIOUS_HORIZON_DAYS = 14

# The working week a pattern gets when it names no days. The *format* is the
# product's and always will be - seven flags, Monday first - and which mask is
# the default is the plant's, because Sunday to Thursday is a real working week.
WORKING_WEEK_MASK = "1111100"


# How long a reporting window is when a caller asks for none. The source called
# it *a shift*, which is an assumption about somebody else's plant: a plant on
# twelve-hour shifts wants 12.
#
# It lives here, beside the shift patterns, rather than in `services.analysis`
# where it was read most, because the audit's own argument for making it a key
# is that a plant already states its shift length in `shift_patterns` - so the
# question is a calendar question. Practically, it is also the one place both
# `analysis` and `equipment` can read it from: `analysis` imports `equipment`,
# so a second accessor in `analysis` would have made the OEE path import its
# own caller.
DEFAULT_REPORT_HOURS = 8.0


def default_report_hours(session: Session) -> float:
    """How long a window is on this plant when nobody says.

    Every payload states the `requested_hours` it was actually given, so
    nothing downstream reads this as a fact about a window - only as the
    question that was asked when nobody asked one.
    """
    return float(plant_settings.value(
        session, "process", "default_report_hours", DEFAULT_REPORT_HOURS))


def previous_horizon_days(session: Session) -> int:
    """How far back this plant looks for the shift before this one."""
    return int(plant_settings.value(
        session, "process", "previous_shift_horizon_days", PREVIOUS_HORIZON_DAYS))


def working_week_mask(session: Session) -> str:
    """This plant's own default working week, as seven flags, Monday first."""
    return str(plant_settings.value(
        session, "process", "working_week_mask", WORKING_WEEK_MASK))

#: `shift=` on an analysis window: a named shift on a named plant-local day.
NAMED_SHIFT = re.compile(r"\A(\d{4})-(\d{2})-(\d{2})/(?P<code>.+)\Z")


#: Where the active patterns are kept for the life of one session. The
#: calendar is read on every booking, every state change and every minute of a
#: scheduling walk, and it is a handful of rows that change about once a year;
#: re-selecting them thousands of times a second is pure waste. Sessions here
#: are opened per request or per agent tick and closed again (`session_scope`),
#: so "for the life of one session" is a fraction of a second - and
#: `create_pattern` clears it anyway, so the session that writes a pattern
#: reads it back.
_PATTERN_CACHE = "fsmes.calendar.patterns"


def patterns(session: Session, equipment_id: int | None = None) -> list[ShiftPattern]:
    """Shifts that apply to a machine: its own, plus the site-wide ones."""
    rows = session.info.get(_PATTERN_CACHE)
    if rows is None:
        rows = list(session.scalars(
            select(ShiftPattern).where(ShiftPattern.active.is_(True))))
        session.info[_PATTERN_CACHE] = rows
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
    """Is this plant-local wall clock inside this shift?"""
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
               equipment_id: int | None = None, *, zone: tzinfo | None = None) -> bool:
    """Is the plant running at this instant?

    `zone` is the plant's clock, resolved by the caller when it is about to
    ask this several thousand times in a row; left out it is looked up.
    """
    shifts = patterns(session, equipment_id)
    if not shifts:
        # No calendar defined means the plant is assumed to run continuously.
        # Stated rather than silently assumed, because the alternative -
        # refusing to schedule anything - is worse for a plant mid-setup.
        return True

    local = _local(moment, zone)
    exception = _exception_for(session, local.date(), equipment_id)
    if exception is not None:
        # An overtime day runs regardless of the pattern; a shutdown day does
        # not run regardless of it either.
        return exception.kind is not ExceptionKind.NON_WORKING

    return any(_within(local, p) for p in shifts)


def _local(moment: datetime, zone: tzinfo | None = None) -> datetime:
    """A stored instant as the plant's wall clock."""
    if zone is None:
        return identity.to_plant(moment)
    from datetime import UTC

    aware = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
    return aware.astimezone(zone)


@dataclass(frozen=True)
class Shift:
    """One occurrence of a shift pattern: which shift, on which day, and when.

    `day` is the plant-local date the shift *started*, so Friday night's shift
    is Friday's however far into Saturday it runs. `starts_at` and `ends_at`
    are naive UTC like every other instant the MES stores, so they can be
    compared with a row's timestamp without a second conversion.

    `ends_at` may be in the future - that is the shift now in progress, and a
    window built from it is clipped to now by whoever builds it rather than
    here, because the shift itself does not end early just because we asked
    about it halfway through.
    """

    code: str
    name: str
    day: date
    starts_at: datetime
    ends_at: datetime

    @property
    def nominal_hours(self) -> float:
        """How long this shift is rostered for, on the plant's own clock.

        Not always the same number twice: the night a plant's clocks go
        forward, a 22:00-06:00 shift is seven hours, and that is the truth
        about that night rather than an error to correct.
        """
        return round((self.ends_at - self.starts_at).total_seconds() / 3600, 4)

    def key(self) -> str:
        """What a caller passes back as `shift=` to ask for this one again."""
        return f"{self.day.isoformat()}/{self.code}"

    def as_json(self) -> dict:
        return {"code": self.code, "name": self.name, "day": self.day.isoformat(),
                "key": self.key(), "starts": self.starts_at, "ends": self.ends_at,
                "nominal_hours": self.nominal_hours}


def _to_utc(local: datetime) -> datetime:
    """A plant wall clock back to the naive UTC every table stores."""
    return local.astimezone(UTC).replace(tzinfo=None)


def _instance(pattern: ShiftPattern, day: date, zone: tzinfo) -> Shift:
    """The occurrence of `pattern` that started on the plant-local `day`."""
    starts_local = datetime.combine(day, pattern.starts, tzinfo=zone)
    last_day = day + timedelta(days=1) if pattern.crosses_midnight else day
    ends_local = datetime.combine(last_day, pattern.ends, tzinfo=zone)
    return Shift(code=pattern.code, name=pattern.name, day=day,
                 starts_at=_to_utc(starts_local), ends_at=_to_utc(ends_local))


def _runs_on(session: Session, pattern: ShiftPattern, day: date,
             equipment_id: int | None) -> bool:
    """Does this pattern run on this plant-local day?

    The seven-character mask says the normal week. An *overtime* exception adds
    a day the mask leaves out - a Saturday the plant worked is a Saturday whose
    units belong to a shift, and leaving them unattributed because the roster
    says Saturday is dark would lose a whole day of production out of every
    per-shift report.

    A shutdown exception does not take a day away here, and that is deliberate.
    `is_working` answers *was the plant meant to be running*; this answers
    *which shift did this happen in*. A line that ran on a shutdown day still
    ran, and those units belong to the shift whose hours they fell in - the
    exception is the finding, not a reason to drop the attribution.
    """
    if pattern.days[day.weekday()] == "1":
        return True
    exception = _exception_for(session, day, equipment_id)
    return exception is not None and exception.kind is ExceptionKind.WORKING


def _containing(session: Session, pattern: ShiftPattern, local: datetime,
                zone: tzinfo, equipment_id: int | None) -> Shift | None:
    """This pattern's occurrence around a plant-local instant, if it has one."""
    at = local.time()
    if pattern.crosses_midnight:
        if at >= pattern.starts:
            day = local.date()
        elif at < pattern.ends:
            day = local.date() - timedelta(days=1)
        else:
            return None
    elif pattern.starts <= at < pattern.ends:
        day = local.date()
    else:
        return None
    if not _runs_on(session, pattern, day, equipment_id):
        return None
    return _instance(pattern, day, zone)


def shift_for(session: Session, moment: datetime, equipment_id: int | None = None,
              *, zone: tzinfo | None = None) -> Shift | None:
    """Which shift this instant belongs to, or None when no pattern covers it.

    None is a real answer and is reported as *not attributed*. A plant with no
    shift patterns at all gets None for everything, which is the truth: nobody
    has told this MES what its shifts are, and `is_working` treating that as
    "running continuously" is a scheduling fallback, not a shift.

    Two patterns can cover one instant - a line with its own roster sitting
    under a site-wide one, or two shifts that overlap by a handover. The
    machine's own pattern wins, and between two of equal standing the one that
    started later wins: that is the shift the people on the floor would say
    they are on.
    """
    zone = zone or identity.clock().tz
    local = _local(moment, zone)
    best: Shift | None = None
    best_specific = False
    for pattern in patterns(session, equipment_id):
        found = _containing(session, pattern, local, zone, equipment_id)
        if found is None:
            continue
        specific = pattern.equipment_id is not None
        if best is None or (specific, found.starts_at) > (best_specific, best.starts_at):
            best, best_specific = found, specific
    return best


def occurrences(session: Session, start: datetime, end: datetime,
                equipment_id: int | None = None, *,
                zone: tzinfo | None = None) -> list[Shift]:
    """Every shift that overlaps the span `[start, end)`, earliest first.

    Built from the patterns day by day rather than read from a table, because
    there is no shift table: a shift is a pattern plus a date. One day either
    side of the span is generated so a night shift that started before it, or
    ends after it, is still found.
    """
    zone = zone or identity.clock().tz
    first = _local(start, zone).date() - timedelta(days=1)
    last = _local(end, zone).date() + timedelta(days=1)
    found: list[Shift] = []
    for pattern in patterns(session, equipment_id):
        day = first
        while day <= last:
            if _runs_on(session, pattern, day, equipment_id):
                shift = _instance(pattern, day, zone)
                if shift.ends_at > start and shift.starts_at < end:
                    found.append(shift)
            day += timedelta(days=1)
    found.sort(key=lambda s: (s.starts_at, s.code))
    return found


def nothing_to_window(session: Session, equipment_id: int | None) -> str:
    """Why there is no shift to ask for. Two different facts, said apart.

    A plant with no patterns at all has not told this MES what its shifts are.
    A plant whose every pattern belongs to a line has told it, but nothing
    site-wide, and a window meant to cover the whole screen cannot be built
    from one line's roster. Saying "no shift patterns" to the second plant
    would send somebody to re-enter master data they already have.
    """
    every = list(session.scalars(
        select(ShiftPattern).where(ShiftPattern.active.is_(True))))
    if not every:
        return ("this plant has no shift patterns, so nothing can be windowed by "
                "shift - define them in master data (a plant pack's shifts.json, "
                "or `fsmes` master data) before asking for one")
    lines = sorted({p.equipment.code for p in every if p.equipment})
    scope = f"machine {equipment_id}" if equipment_id else "the whole site"
    return (f"every shift pattern here belongs to one line ({', '.join(lines)}), "
            f"and none covers {scope} - a shift window covers a whole screen, so "
            "it needs a site-wide pattern; add one, or ask for a span of hours")


def resolve_shift(session: Session, spec: str, equipment_id: int | None = None,
                  *, now: datetime | None = None) -> Shift:
    """Turn a `shift=` request into the one shift it names.

    Three spellings, and nothing else, because a window a person cannot say
    out loud is a window nobody audits:

        current              the shift running now
        previous             the one before it
        2026-09-14/NIGHT     a named shift on a named plant-local day

    Raises `Invalid` with one sentence when the request names no shift - the
    plant is between shifts, the code is not a pattern, the pattern does not
    run that day. Never silently falls back to a span of hours: a screen that
    said "night shift" while showing the last eight hours would be worse than
    an error.
    """
    from fsmes.db import utcnow

    moment = now or utcnow()
    zone = identity.clock().tz
    known = patterns(session, equipment_id)
    if not known:
        raise Invalid(nothing_to_window(session, equipment_id))

    if spec == "current":
        shift = shift_for(session, moment, equipment_id, zone=zone)
        if shift is None:
            raise Invalid(
                "the plant is not in a shift at the moment, so there is no "
                f"current shift to report - the patterns are "
                f"{', '.join(p.code for p in known)} on "
                f"{identity.clock().says()}")
        return shift

    if spec == "previous":
        horizon = previous_horizon_days(session)
        earliest = moment - timedelta(days=horizon)
        current = shift_for(session, moment, equipment_id, zone=zone)
        cutoff = current.starts_at if current else moment
        ended = [s for s in occurrences(session, earliest, cutoff, equipment_id, zone=zone)
                 if s.ends_at <= cutoff]
        if not ended:
            raise Invalid(
                f"no shift has ended in the last {horizon} days, "
                "so there is no previous shift to report")
        return ended[-1]

    named = NAMED_SHIFT.match(spec)
    if not named:
        raise Invalid(
            f"{spec!r} does not name a shift. Ask for `current`, `previous`, "
            "or a day and a code such as `2026-09-14/NIGHT`.")
    try:
        day = date(int(named.group(1)), int(named.group(2)), int(named.group(3)))
    except ValueError as exc:
        raise Invalid(f"{spec!r} does not name a day: {exc}") from exc

    code = named.group("code")
    pattern = next((p for p in known if p.code == code), None)
    if pattern is None:
        raise Invalid(
            f"no active shift pattern is called {code!r} here "
            f"(known: {', '.join(p.code for p in known)})")
    if not _runs_on(session, pattern, day, equipment_id):
        raise Invalid(
            f"shift {code} does not run on {day.isoformat()}, a "
            f"{day.strftime('%A')} - its days are "
            f"{pattern.days}, Monday first, and no overtime exception covers "
            "that day")
    return _instance(pattern, day, zone)


def stamp(row, shift: Shift | None) -> None:
    """Write a shift onto a row that carries one, or leave it not attributed.

    One function so every table that carries a shift carries it the same way,
    and so the null case is written deliberately rather than by omission.
    """
    row.shift_code = shift.code if shift else None
    row.shift_day = shift.day if shift else None


def attribute(session: Session, row, moment: datetime,
              equipment_id: int | None = None) -> Shift | None:
    """Work out the shift for `moment` and put it on `row`. Returns the shift."""
    shift = shift_for(session, moment, equipment_id)
    stamp(row, shift)
    return shift


def next_working(session: Session, moment: datetime,
                 equipment_id: int | None = None) -> datetime:
    """The next instant the plant is running, `moment` included."""
    cursor = moment.replace(second=0, microsecond=0)
    horizon = cursor + timedelta(days=MAX_HORIZON_DAYS)
    # One zone lookup for the whole walk: this loop steps a minute at a time
    # and can run to a hundred and twenty days.
    zone = identity.clock().tz
    while cursor < horizon:
        if is_working(session, cursor, equipment_id, zone=zone):
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
    zone = identity.clock().tz

    while remaining > 0 and cursor < horizon:
        if is_working(session, cursor, equipment_id, zone=zone):
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
    zone = identity.clock().tz
    while cursor < end:
        if is_working(session, cursor, equipment_id, zone=zone):
            total += 1
        cursor += STEP
    return total


def create_pattern(session: Session, *, code: str, name: str, starts: time,
                   ends: time, days: str | None = None,
                   equipment_code: str | None = None,
                   actor: str = "system") -> ShiftPattern:
    if session.scalar(select(ShiftPattern).where(ShiftPattern.code == code)):
        raise Conflict(f"shift {code} already exists")
    # None means *this plant's own working week*, resolved here rather than in
    # the signature so a caller gets what the plant is running on now.
    if days is None:
        days = working_week_mask(session)
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
    session.info.pop(_PATTERN_CACHE, None)
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
    the_clock = identity.clock()
    return {
        # Which clock those shift times are read on. A screen showing
        # "Day 06:00-14:00" with no zone is a fact about nothing.
        "timezone": the_clock.name,
        "timezone_defaulted": the_clock.defaulted,
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
