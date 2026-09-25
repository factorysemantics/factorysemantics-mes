"""The coverage ledger: how much of a window this MES actually saw.

Every availability figure in this product has a denominator, and until this
module existed the denominator was *time that passed* less the seconds a
disconnection was recorded for (decision 0030). That is better than the whole
window, and it is still not an account. It answers "how long was the window"
and never "how much of it did anybody watch, and where did the rest go".

So: a **ledger**. Per machine, per window, a list of disjoint intervals that
tile the window **exactly** — every second of it belongs to one interval and
no second belongs to two — and each interval carries one disposition:

    observed_running              the MES watched it and it was running
    observed_stopped_labelled     the MES watched it stopped, with a reason
    observed_stopped_unlabelled   the MES watched it stopped, no reason given
    not_observed                  nobody was watching

and, for `not_observed`, one cause naming where the time went. Availability is
then derived *from* the ledger rather than computed beside it, and it carries
`coverage` — observed ÷ window — as a second number that is always shown.

**The seconds must add up.** An OEE window whose seconds do not add up is a
bug, not a rounding difference, and `Ledger.tiles_exactly()` says so. The test
suite pins it, and so does `fsmes oee explain`, which prints the totals at the
bottom of the table it draws.

## Why only four causes, when the plant can fail in more ways than that

The obvious cause list is longer: the agent was down, the tag went stale, the
plant was unreachable. This MES cannot tell those three apart from what it has
written down. A disconnection interval carries a sentence somebody's code
wrote (`the OPC server did not answer: timed out`) and the endpoint it was
dialling; classifying that free text into a taxonomy would be the MES claiming
to know which of three things happened when all it holds is that the link was
gone. House rule 1 the other way round: *unknown is not zero*, and a made-up
cause is worse than a named gap.

So `disconnected` is one cause and carries the recorded sentence beside it as
`detail`, unaltered. The day a plant's OPC server publishes a heartbeat tag,
per-machine staleness becomes a positive signal (decision 0030's "to revisit")
and this list gains a fifth member with evidence behind it. Not before.

`no_state_recorded` is the fourth, and it is a real and separate thing: the
machine was inside the MES's watching period, no disconnection was recorded,
and the state history still has a hole. An agent killed outright writes no
disconnection on its way down — decision 0030 wrote that down as known and not
solved — and this is what that looks like in the ledger.

## Two ways in, and they must agree

`ledger()` materialises the intervals for one machine: what `fsmes oee explain`
prints and what the machine page shows. `totals_many()` computes the same sums
for a whole plant in grouped queries that never bring an interval out of the
database, because eight hours of a 108-station plant is 400,000 state
intervals and the OEE path is a screen refresh (see `equipment.state_seconds`
for what that cost the first time). They use the same cause precedence, and a
test holds them to the same numbers on the same data.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import case, func, literal, or_, select
from sqlalchemy.orm import Session

from fsmes.domain import (
    ConnectionStateName,
    EquipmentConnection,
    EquipmentState,
    EquipmentStateName,
)

# ------------------------------------------------------------- the vocabulary

#: The machine was watched and it was running.
OBSERVED_RUNNING = "observed_running"
#: Watched, not running, and somebody said why.
OBSERVED_STOPPED_LABELLED = "observed_stopped_labelled"
#: Watched, not running, and nobody has said why yet. Reported as unlabelled
#: rather than folded into a catch-all that looks explained (house rule 3).
OBSERVED_STOPPED_UNLABELLED = "observed_stopped_unlabelled"
#: Nobody was watching. Never a state: the machine had one, or it did not, and
#: this MES has no idea which.
NOT_OBSERVED = "not_observed"

OBSERVED = (OBSERVED_RUNNING, OBSERVED_STOPPED_LABELLED, OBSERVED_STOPPED_UNLABELLED)
DISPOSITIONS = (*OBSERVED, NOT_OBSERVED)

#: Window time before the MES had ever recorded anything about this machine —
#: no state, no connection. Not a fault: a machine commissioned this morning
#: has eight hours of it in an eight-hour window, and saying so is the
#: difference between "new" and "blind".
BEFORE_FIRST_SAMPLE = "before_first_sample"
#: A disconnection this MES recorded (decision 0030). `detail` carries the
#: sentence whatever noticed it wrote, unaltered.
DISCONNECTED = "disconnected"
#: Window time after the last thing this MES recorded about the machine, with
#: nothing left open. The machine stopped being reported on and nothing said
#: why.
AFTER_LAST_SAMPLE = "after_last_sample"
#: Inside the watching period, no disconnection recorded, and the state
#: history has a hole anyway. An agent killed outright leaves this.
NO_STATE_RECORDED = "no_state_recorded"

CAUSES = (BEFORE_FIRST_SAMPLE, DISCONNECTED, AFTER_LAST_SAMPLE, NO_STATE_RECORDED)

#: The rule that produced each kind of interval, in one sentence, so a plant
#: engineer reading `fsmes oee explain` can argue with the rule and not only
#: with the number.
RULES: dict[str, str] = {
    OBSERVED_RUNNING: "a recorded state interval said running",
    OBSERVED_STOPPED_LABELLED: "a recorded state interval said not running, and carried a reason",
    OBSERVED_STOPPED_UNLABELLED: "a recorded state interval said not running, with no reason on it",
    BEFORE_FIRST_SAMPLE: "earlier than the first state or connection this MES ever recorded here",
    DISCONNECTED: "a recorded disconnection: this MES could not see the machine (decision 0030)",
    AFTER_LAST_SAMPLE: "later than the last record, and nothing is still open",
    NO_STATE_RECORDED: "inside the watching period, no disconnection recorded, and no state interval either",
}

#: Said beside a figure that is withheld because the window's coverage is
#: below the pack's floor.
BELOW_FLOOR = (
    "withheld: this MES saw {coverage:.1%} of the window and this plant's pack asks "
    "for at least {floor:.0%} before a figure is reported. The ledger says where the "
    "rest of the window went"
)

#: Said when the pack sets no floor. Nothing is withheld; coverage is still
#: printed beside every figure.
NO_FLOOR = "this plant's pack sets no coverage floor, so nothing is withheld"


# ---------------------------------------------------------------- the ledger


#: Below this much observed time, nothing is divided by it. The same threshold
#: `equipment` and `analysis` already used, in one place now - and the literal
#: the product ships rather than the whole answer since 2026-09-25: it is
#: `[oee] min_observed_seconds`, beside `coverage_floor`, because the argument
#: that made that one a plant's to set is the argument for this one. What a
#: plant is running on is `min_observed_seconds()` below.
MIN_OBSERVED_SECONDS = 10.0


def min_observed_seconds(session: Session) -> float:
    """The least observed time this plant will divide by.

    Read through the caller's session, like every other live setting; the two
    dataclasses below carry the answer as a field rather than reading it from a
    property, because a property on a frozen account of one window should not
    be able to give two different answers on two readings of the same object.
    """
    from fsmes.services import plant_settings

    return float(plant_settings.value(
        session, "oee", "min_observed_seconds", MIN_OBSERVED_SECONDS))


@dataclass(frozen=True)
class Interval:
    """One stretch of the window, with one disposition and nothing implied."""

    start: datetime
    end: datetime
    disposition: str
    cause: str | None = None
    detail: str | None = None
    #: The sampling cadence the observation behind this interval was built at.
    #: `None` for `not_observed`, and `None` for an observed interval on a
    #: plant that has no configured cadence to report.
    #:
    #: It is the plant's configured cadence, not a measurement of this
    #: interval: the state history does not record what wrote each row. That
    #: matters because a machine flipping state every three seconds, watched
    #: on a fifteen-second grid, loses run time to the grid — measured, in
    #: decision 0026. This field is where a later change-driven capture will
    #: put the real per-interval number, and the ledger's shape does not
    #: change when it does.
    sample_interval_seconds: float | None = None

    @property
    def seconds(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds())

    @property
    def observed(self) -> bool:
        return self.disposition in OBSERVED

    @property
    def rule(self) -> str:
        return RULES[self.cause or self.disposition]

    def as_json(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "seconds": round(self.seconds, 1),
            "disposition": self.disposition,
            "cause": self.cause,
            "detail": self.detail,
            "rule": self.rule,
            "sample_interval_seconds": self.sample_interval_seconds,
        }


@dataclass(frozen=True)
class Ledger:
    """Every second of one machine's window, accounted for."""

    equipment: str
    start: datetime
    end: datetime
    intervals: tuple[Interval, ...] = ()
    #: Where `sample_interval_seconds` came from, named rather than assumed.
    sample_interval_source: str | None = None
    #: The least observed time this plant divides by, carried on the account
    #: rather than read from a setting inside `availability` - so one ledger
    #: cannot answer the same question two ways, and a ledger built in a test
    #: with no plant behind it still has the product's own floor.
    min_observed: float = MIN_OBSERVED_SECONDS

    @property
    def window_seconds(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds())

    @property
    def seconds_by_disposition(self) -> dict[str, float]:
        out = {name: 0.0 for name in DISPOSITIONS}
        for interval in self.intervals:
            out[interval.disposition] += interval.seconds
        return out

    @property
    def seconds_by_cause(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for interval in self.intervals:
            if interval.cause:
                out[interval.cause] = out.get(interval.cause, 0.0) + interval.seconds
        return out

    @property
    def observed_seconds(self) -> float:
        return sum(i.seconds for i in self.intervals if i.observed)

    @property
    def not_observed_seconds(self) -> float:
        return sum(i.seconds for i in self.intervals if not i.observed)

    @property
    def running_seconds(self) -> float:
        return self.seconds_by_disposition[OBSERVED_RUNNING]

    @property
    def coverage(self) -> float | None:
        """Observed ÷ window. `None` for a zero-width window: a window with no
        seconds in it has no share of itself observed, and zero would read as
        "we saw none of it"."""
        if self.window_seconds <= 0:
            return None
        return self.observed_seconds / self.window_seconds

    @property
    def availability(self) -> float | None:
        """Run time ÷ **observed** time. `None` when there is not enough
        observed time to divide by — never zero, which would say the machine
        stood still rather than that nobody watched it."""
        observed = self.observed_seconds
        if observed < self.min_observed:
            return None
        return self.running_seconds / observed

    def tiles_exactly(self, tolerance: float = 0.001) -> bool:
        """Do the seconds add up, and does every interval meet the next?

        An OEE window whose seconds do not add up is a bug, not a rounding
        difference. `tolerance` is a millisecond, which is what a database
        round-trip through a float can cost, and nothing wider.
        """
        if not self.intervals:
            return self.window_seconds <= tolerance
        if self.intervals[0].start != self.start or self.intervals[-1].end != self.end:
            return False
        for earlier, later in zip(self.intervals, self.intervals[1:], strict=False):
            if earlier.end != later.start:
                return False
        total = sum(i.seconds for i in self.intervals)
        return abs(total - self.window_seconds) <= tolerance

    def as_json(self) -> dict:
        """What the API and the screens read. Every list states its total."""
        return {
            "equipment": self.equipment,
            "start": self.start,
            "end": self.end,
            "window_seconds": round(self.window_seconds, 1),
            "observed_seconds": round(self.observed_seconds, 1),
            "not_observed_seconds": round(self.not_observed_seconds, 1),
            "coverage": round(self.coverage, 4) if self.coverage is not None else None,
            "intervals": [i.as_json() for i in self.intervals],
            "interval_count": len(self.intervals),
            "seconds_by_disposition": {k: round(v, 1) for k, v in self.seconds_by_disposition.items()},
            "seconds_by_cause": {k: round(v, 1) for k, v in self.seconds_by_cause.items()},
            "sample_interval_source": self.sample_interval_source,
            "tiles_exactly": self.tiles_exactly(),
        }


@dataclass
class Totals:
    """The same account as a `Ledger`, summed in the database instead of
    listed. What the OEE path uses, because a plant refresh must not carry
    400,000 intervals out of the database to add them up."""

    window_seconds: float
    observed_running: float = 0.0
    observed_stopped_labelled: float = 0.0
    observed_stopped_unlabelled: float = 0.0
    not_observed_by_cause: dict[str, float] = field(default_factory=dict)
    #: As on `Ledger` above, and for the same reason.
    min_observed: float = MIN_OBSERVED_SECONDS

    @property
    def observed_seconds(self) -> float:
        return (self.observed_running + self.observed_stopped_labelled
                + self.observed_stopped_unlabelled)

    @property
    def not_observed_seconds(self) -> float:
        return sum(self.not_observed_by_cause.values())

    @property
    def coverage(self) -> float | None:
        if self.window_seconds <= 0:
            return None
        return self.observed_seconds / self.window_seconds

    @property
    def availability(self) -> float | None:
        if self.observed_seconds < self.min_observed:
            return None
        return self.observed_running / self.observed_seconds

    @property
    def seconds_by_disposition(self) -> dict[str, float]:
        return {
            OBSERVED_RUNNING: self.observed_running,
            OBSERVED_STOPPED_LABELLED: self.observed_stopped_labelled,
            OBSERVED_STOPPED_UNLABELLED: self.observed_stopped_unlabelled,
            NOT_OBSERVED: self.not_observed_seconds,
        }

    def tiles_exactly(self, tolerance: float = 0.001) -> bool:
        return abs(self.observed_seconds + self.not_observed_seconds - self.window_seconds) <= tolerance

    def as_json(self) -> dict:
        return {
            "window_seconds": round(self.window_seconds, 1),
            "observed_seconds": round(self.observed_seconds, 1),
            "not_observed_seconds": round(self.not_observed_seconds, 1),
            "coverage": round(self.coverage, 4) if self.coverage is not None else None,
            "seconds_by_disposition": {k: round(v, 1) for k, v in self.seconds_by_disposition.items()},
            # Only causes with seconds worth printing. The test is the rounded
            # number, not the raw one: a residue of a microsecond left over
            # from clipping an open interval is not a cause, and listing it as
            # one would put "0.0 s of after_last_sample" on a screen.
            "seconds_by_cause": {k: round(v, 1) for k, v in self.not_observed_by_cause.items()
                                 if round(v, 1) > 0},
            "tiles_exactly": self.tiles_exactly(),
        }


# ------------------------------------------------------- the sampling cadence


def sample_interval() -> tuple[float | None, str | None]:
    """The cadence this plant's machine layer is configured to publish at, and
    where that number came from.

    Config, not code, and *stated* rather than assumed: it is what the plant
    set its OPC publish interval to, not a measurement of any one interval. A
    plant fed by hand or over MQTT has no cadence this can honestly report,
    which is why the source sentence travels with the number everywhere it
    goes.
    """
    from fsmes.config import get_settings

    settings = get_settings()
    ms = getattr(settings, "opc_publish_ms", None)
    if not ms or ms <= 0:
        return None, None
    return round(float(ms) / 1000.0, 3), (
        "this plant's configured OPC publish interval (MES_OPC_PUBLISH_MS), not a "
        "measurement of any one interval"
    )


# ------------------------------------------------------------- the floor


def floor() -> float | None:
    """The coverage a figure must reach before this plant reports it, or
    `None` when the pack sets none.

    There is no silent default. A plant that sets nothing gets every figure
    with its coverage beside it and nothing withheld; a pack that writes
    `[oee] coverage_floor` is asking, deliberately, to be told *unknown*
    rather than shown a number built on a window nobody watched.
    """
    from fsmes.config import get_settings

    value = float(getattr(get_settings(), "oee_coverage_floor", 0.0) or 0.0)
    if value <= 0.0:
        return None
    return min(1.0, value)


def withhold(coverage: float | None, the_floor: float | None) -> str | None:
    """The sentence to print instead of the figure, or `None` to print it.

    A window whose coverage is unknown is withheld too when a floor is set:
    "we cannot say how much of this we saw" does not clear a bar that asks how
    much of it we saw.
    """
    if the_floor is None:
        return None
    if coverage is None:
        return BELOW_FLOOR.format(coverage=0.0, floor=the_floor)
    if coverage + 1e-9 < the_floor:
        return BELOW_FLOOR.format(coverage=coverage, floor=the_floor)
    return None


# --------------------------------------------------------- reading the window


def _seconds_between(dialect: str, later, earlier):
    """`later - earlier` in seconds, as the database computes it, or None when
    this dialect has no known form and the caller must sum in Python."""
    if dialect == "sqlite":
        return (func.julianday(later) - func.julianday(earlier)) * 86400.0
    if dialect == "postgresql":
        return func.extract("epoch", later - earlier)
    return None


def _state_rows(session: Session, equipment_ids: list[int], start: datetime, end: datetime):
    return session.execute(
        select(EquipmentState.equipment_id, EquipmentState.state, EquipmentState.reason,
               EquipmentState.started_at, EquipmentState.ended_at)
        .where(EquipmentState.equipment_id.in_(equipment_ids),
               EquipmentState.started_at < end,
               or_(EquipmentState.ended_at.is_(None), EquipmentState.ended_at > start))
        .order_by(EquipmentState.started_at, EquipmentState.id)
    ).all()


def _disconnection_rows(session: Session, equipment_ids: list[int], start: datetime, end: datetime):
    return session.execute(
        select(EquipmentConnection.equipment_id, EquipmentConnection.started_at,
               EquipmentConnection.ended_at, EquipmentConnection.reason,
               EquipmentConnection.source)
        .where(EquipmentConnection.equipment_id.in_(equipment_ids),
               EquipmentConnection.state == ConnectionStateName.DISCONNECTED,
               EquipmentConnection.started_at < end,
               or_(EquipmentConnection.ended_at.is_(None), EquipmentConnection.ended_at > start))
        .order_by(EquipmentConnection.started_at, EquipmentConnection.id)
    ).all()


def observed_seconds_many(session: Session, equipment_ids: list[int], start: datetime,
                          end: datetime) -> dict[int, tuple[float, float, float]]:
    """(running, stopped_labelled, stopped_unlabelled) per machine, clipped to
    the window and summed in the database.

    One grouped query for the whole plant. The rows never leave the database,
    for the reason `equipment.state_seconds` documents at length.
    """
    out: dict[int, tuple[float, float, float]] = {}
    if not equipment_ids or end <= start:
        return out
    where = (
        EquipmentState.equipment_id.in_(equipment_ids),
        EquipmentState.started_at < end,
        or_(EquipmentState.ended_at.is_(None), EquipmentState.ended_at > start),
    )
    running = EquipmentState.state == EquipmentStateName.RUNNING
    labelled = EquipmentState.reason.is_not(None)
    bucket = case(
        (running, literal(OBSERVED_RUNNING)),
        (labelled, literal(OBSERVED_STOPPED_LABELLED)),
        else_=literal(OBSERVED_STOPPED_UNLABELLED),
    )

    dialect = session.get_bind().dialect.name
    ended = EquipmentState.ended_at.type
    lo = case((EquipmentState.started_at < start, literal(start, ended)), else_=EquipmentState.started_at)
    hi = case((or_(EquipmentState.ended_at.is_(None), EquipmentState.ended_at > end),
               literal(end, ended)), else_=EquipmentState.ended_at)
    seconds = _seconds_between(dialect, hi, lo)

    totals: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    if seconds is not None:
        rows = session.execute(
            select(EquipmentState.equipment_id, bucket, func.sum(seconds))
            .where(*where).group_by(EquipmentState.equipment_id, bucket)
        ).all()
        for equipment_id, name, total in rows:
            totals[equipment_id][str(name)] += max(0.0, float(total or 0.0))
    else:
        # A dialect without a known interval form: the reference sum, in
        # Python, over bare columns rather than ORM objects.
        for equipment_id, state, reason, started_at, ended_at in _state_rows(
                session, equipment_ids, start, end):
            lo_ = max(started_at, start)
            hi_ = min(ended_at or end, end)
            name = (OBSERVED_RUNNING if state is EquipmentStateName.RUNNING
                    else OBSERVED_STOPPED_LABELLED if reason else OBSERVED_STOPPED_UNLABELLED)
            totals[equipment_id][name] += max(0.0, (hi_ - lo_).total_seconds())

    for equipment_id, by_name in totals.items():
        out[equipment_id] = (by_name.get(OBSERVED_RUNNING, 0.0),
                             by_name.get(OBSERVED_STOPPED_LABELLED, 0.0),
                             by_name.get(OBSERVED_STOPPED_UNLABELLED, 0.0))
    return out


def last_record(session: Session, equipment_ids: list[int]) -> dict[int, datetime | None]:
    """When each machine was last written about, or `None` when something is
    still open.

    `None` is the useful answer, not a missing one: a machine with an open
    state interval or an open connection interval is being written about
    *now*, so there is no "after the last record" to account for. A machine
    with no row at all is absent from the result.
    """
    out: dict[int, datetime | None] = {}
    if not equipment_ids:
        return out
    for model in (EquipmentState, EquipmentConnection):
        rows = session.execute(
            select(model.equipment_id, func.max(model.ended_at), func.count(model.id),
                   func.count(model.ended_at))
            .where(model.equipment_id.in_(equipment_ids))
            .group_by(model.equipment_id)
        ).all()
        for equipment_id, latest, rows_total, closed_total in rows:
            if rows_total != closed_total:
                out[equipment_id] = None  # something is open
                continue
            if equipment_id in out and out[equipment_id] is None:
                continue
            if latest is not None and (equipment_id not in out or latest > out[equipment_id]):
                out[equipment_id] = latest
    return out


# --------------------------------------------------------- building a ledger


def _clip(lo: datetime, hi: datetime, start: datetime, end: datetime) -> tuple[datetime, datetime] | None:
    lo, hi = max(lo, start), min(hi, end)
    return (lo, hi) if hi > lo else None


def _tile(start: datetime, end: datetime, pieces: list[tuple[datetime, datetime, Interval]],
          fill) -> list[Interval]:
    """Lay `pieces` down in order and fill every gap between them with `fill`.

    `pieces` must already be disjoint and sorted. The result starts at `start`,
    ends at `end` and has no holes — which is the whole point of this module,
    so it is done once, here, and not re-derived by each caller.
    """
    out: list[Interval] = []
    cursor = start
    for lo, hi, interval in pieces:
        if lo > cursor:
            out.extend(fill(cursor, lo))
        out.append(interval)
        cursor = max(cursor, hi)
    if cursor < end:
        out.extend(fill(cursor, end))
    return out


def ledger(session: Session, *, equipment_code: str, equipment_id: int, start: datetime,
           end: datetime, first_seen: datetime | None = None) -> Ledger:
    """One machine's window, tiled exactly.

    `first_seen` is when this MES first recorded anything about the machine —
    a state or a connection. Pass it when the caller already has it for the
    whole plant; it is read here when not.

    The cause precedence, which `totals_many` shares and a test holds both to:

      1. `before_first_sample` — earlier than anything this MES recorded here.
      2. `disconnected` — inside a recorded disconnection.
      3. `after_last_sample` — later than the last record, nothing still open.
      4. `no_state_recorded` — everything else nobody watched.
    """
    from fsmes.services import equipment as equipment_service

    if first_seen is None:
        first_seen = equipment_service.first_seen(session, [equipment_id]).get(equipment_id)
    cadence, cadence_source = sample_interval()
    # Read once, here, and carried on the account: an accessor called from
    # `availability` could answer differently on two readings of one ledger.
    least = min_observed_seconds(session)

    if end <= start:
        return Ledger(equipment=equipment_code, start=start, end=end,
                      sample_interval_source=cadence_source, min_observed=least)

    watching_from = max(start, first_seen) if first_seen is not None else end
    latest = last_record(session, [equipment_id]).get(equipment_id, first_seen)
    # A machine with no row at all: `last_record` has nothing to say, and the
    # whole window is before the first sample anyway.
    blind_from = max(watching_from, min(latest, end)) if latest is not None else end

    def unobserved(lo: datetime, hi: datetime,
                   inside: list[tuple[datetime, datetime, str, str | None]]) -> list[Interval]:
        """Fill [lo, hi) with not-observed intervals, split at every cause
        boundary the precedence draws through it."""
        pieces: list[tuple[datetime, datetime, Interval]] = []
        for d_lo, d_hi, cause, detail in inside:
            clipped = _clip(d_lo, d_hi, lo, hi)
            if clipped is None:
                continue
            pieces.append((clipped[0], clipped[1],
                           Interval(clipped[0], clipped[1], NOT_OBSERVED, cause, detail)))
        pieces.sort(key=lambda p: p[0])

        def plain(a: datetime, b: datetime) -> list[Interval]:
            # The three causes that are not a recorded disconnection, each
            # over the stretch of [a, b) it owns: before the MES had seen
            # anything, after the last thing it recorded, and — between those
            # two — a hole in the state history nothing explains.
            out: list[Interval] = []
            edges = []
            before = _clip(a, b, start, watching_from)
            after = _clip(a, b, blind_from, end)
            if before:
                edges.append((before[0], before[1], BEFORE_FIRST_SAMPLE))
            if after:
                edges.append((after[0], after[1], AFTER_LAST_SAMPLE))
            edges.sort(key=lambda e: e[0])
            cursor = a
            for e_lo, e_hi, cause in edges:
                if e_lo > cursor:
                    out.append(Interval(cursor, e_lo, NOT_OBSERVED, NO_STATE_RECORDED))
                if e_hi > max(cursor, e_lo):
                    out.append(Interval(max(cursor, e_lo), e_hi, NOT_OBSERVED, cause))
                cursor = max(cursor, e_hi)
            if cursor < b:
                out.append(Interval(cursor, b, NOT_OBSERVED, NO_STATE_RECORDED))
            return out

        return _tile(lo, hi, pieces, plain)

    disconnections: list[tuple[datetime, datetime, str, str | None]] = []
    for _, d_start, d_end, reason, source in _disconnection_rows(session, [equipment_id], start, end):
        detail = reason or None
        if source:
            detail = f"{detail} ({source})" if detail else f"reported by {source}"
        disconnections.append((d_start, d_end or end, DISCONNECTED, detail))

    observed_pieces: list[tuple[datetime, datetime, Interval]] = []
    for _, state, reason, s_start, s_end in _state_rows(session, [equipment_id], start, end):
        clipped = _clip(s_start, s_end or end, start, end)
        if clipped is None:
            continue
        lo, hi = clipped
        # An interval that overlaps one already laid down (a backdated row, a
        # clock that went backwards) is trimmed rather than allowed to
        # double-count: the window is tiled, so a second cannot be observed
        # twice.
        if observed_pieces and lo < observed_pieces[-1][1]:
            lo = observed_pieces[-1][1]
            if hi <= lo:
                continue
        disposition = (OBSERVED_RUNNING if state is EquipmentStateName.RUNNING
                       else OBSERVED_STOPPED_LABELLED if reason else OBSERVED_STOPPED_UNLABELLED)
        observed_pieces.append((lo, hi, Interval(lo, hi, disposition, detail=reason,
                                                 sample_interval_seconds=cadence)))

    intervals = _tile(start, end, observed_pieces,
                      lambda lo, hi: unobserved(lo, hi, disconnections))
    return Ledger(equipment=equipment_code, start=start, end=end,
                  intervals=tuple(intervals), sample_interval_source=cadence_source,
                  min_observed=least)


def totals_many(session: Session, equipment_ids: list[int], start: datetime,
                end: datetime, first_seen: dict[int, datetime] | None = None) -> dict[int, Totals]:
    """The ledger's sums for a whole plant, without materialising an interval.

    Same cause precedence as `ledger`, computed from four grouped queries
    instead of from a list. A machine with no record at all still gets a
    `Totals`: the whole window is `before_first_sample`, which is the honest
    answer and not an empty one.
    """
    from fsmes.services import connection as connection_service
    from fsmes.services import equipment as equipment_service

    window = max(0.0, (end - start).total_seconds())
    least = min_observed_seconds(session)
    out = {equipment_id: Totals(window_seconds=window, min_observed=least)
           for equipment_id in equipment_ids}
    if not equipment_ids or window <= 0:
        return out

    if first_seen is None:
        first_seen = equipment_service.first_seen(session, equipment_ids)
    observed = observed_seconds_many(session, equipment_ids, start, end)
    latest = last_record(session, equipment_ids)

    # Disconnections, clipped to the part of the window this MES was already
    # watching — a disconnection recorded before the first sample would
    # otherwise be counted twice, once as each cause.
    watching_from = {
        equipment_id: (max(start, first_seen[equipment_id]) if equipment_id in first_seen else end)
        for equipment_id in equipment_ids
    }
    disconnected: dict[int, float] = {}
    by_start: dict[datetime, list[int]] = defaultdict(list)
    for equipment_id, from_ in watching_from.items():
        if from_ < end:
            by_start[from_].append(equipment_id)
    for from_, batch in by_start.items():
        disconnected.update(connection_service.unknown_seconds(session, batch, from_, end))

    for equipment_id in equipment_ids:
        totals = out[equipment_id]
        running, labelled, unlabelled = observed.get(equipment_id, (0.0, 0.0, 0.0))
        totals.observed_running = running
        totals.observed_stopped_labelled = labelled
        totals.observed_stopped_unlabelled = unlabelled

        from_ = watching_from[equipment_id]
        before = max(0.0, (min(from_, end) - start).total_seconds())
        # `last_record` says None when something is still open, and is absent
        # when the machine has no row at all — in which case the whole window
        # is already `before_first_sample`.
        last = latest.get(equipment_id, end if equipment_id not in first_seen else None)
        blind_from = max(from_, min(last, end)) if last is not None else end
        after = max(0.0, (end - blind_from).total_seconds())
        gap = disconnected.get(equipment_id, 0.0)

        # The precedence, spelled out as arithmetic so it cannot drift from
        # the one `ledger` lays down: observed time is claimed first, then
        # each cause takes what is left in order, and whatever survives all
        # four is the hole nothing explains. Written this way the causes
        # always sum to exactly the unobserved seconds — no clamp can hide a
        # double count, because there is nothing left for one to come out of.
        remaining = max(0.0, window - totals.observed_seconds)
        before = min(before, remaining)
        remaining -= before
        gap = min(gap, remaining)
        remaining -= gap
        after = min(after, remaining)
        remaining -= after
        totals.not_observed_by_cause = {
            BEFORE_FIRST_SAMPLE: before,
            DISCONNECTED: gap,
            AFTER_LAST_SAMPLE: after,
            NO_STATE_RECORDED: remaining,
        }
    return out
