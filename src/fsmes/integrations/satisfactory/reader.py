"""The game's polls, written into the MES.

Through the services every other reader uses, and nothing else:
`equipment.set_state` for what a building was doing, `connection.set_connection`
for whether the MES could see it, `execution.report` for what it made,
`counters.save` for how far the reader has got. One transaction per poll, so
a poll is either all in or not in at all.

WHAT IS NOT OBSERVED IS NOT DOWN. Four things make a building unseen, and
each closes its state history with the cause rather than writing a state:

* the game said it was paused (`getSessionInfo.IsPaused`) - time stood still
  in the factory, and nothing was made or stopped;
* the polls stopped for longer than `gap_polls` poll intervals - the game
  was closed, or the poller was off, and the file cannot say which;
* while following, no new poll has arrived for that long by the wall clock -
  the same thing, noticed as it happens;
* a building that was in the list is not any more - torn down, or out of
  the mod's reach.

PRODUCTION IS THE GAME'S RATE, INTEGRATED. The mod gives a rate
(`production[0].CurrentProd`, units a minute), not a counter, so each poll
books rate x the time since the building's previous poll, as a running sum,
and only whole units: the fraction waits for the next poll. Nothing is
booked across a gap or a pause - the rate at either end says nothing about
the time between. A building with no work order books unassigned production,
which is what it is: the player makes things; nobody ordered them.

RESTARTS DO NOT COUNT TWICE. The last poll applied and the units booked per
building are counter baselines, written in the poll's own transaction; a
reader that starts again skips every poll at or before that moment. The
fraction of a unit not yet booked is not kept, so a restart can lose less
than one unit per building and never adds one.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain.equipment import ConnectionStateName, EquipmentState
from fsmes.domain.execution import ProductionSource
from fsmes.domain.masterdata import Equipment, EquipmentLevel
from fsmes.integrations.satisfactory import feed, rules
from fsmes.services import audit, connection, counters, execution, masterdata
from fsmes.services import equipment as equipment_service

log = structlog.get_logger("satisfactory")

SOURCE = "Ficsit Remote Monitoring"
ACTOR = "satisfactory-reader"
#: The counter-baseline row that holds the last poll applied. Not a machine:
#: the reader's own place in the files.
FEED_KEY = ("SATISFACTORY-FEED", "PollEpoch")



def work_centre(building: dict) -> tuple[str, str]:
    """One work centre per kind of building: every Constructor under
    CONSTRUCTORS, every Smelter under SMELTERS."""
    kind = str(building.get("Name") or "Building")
    slug = "".join(ch if ch.isalnum() else "-" for ch in kind.upper()).strip("-")
    return f"{slug}S"[:40], f"{kind}s"


def _epoch(moment: datetime) -> int:
    return int(moment.replace(tzinfo=UTC).timestamp())


def _from_epoch(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, UTC).replace(tzinfo=None)


@dataclass
class Report:
    polls: int = 0
    skipped: int = 0
    paused_polls: int = 0
    gaps: int = 0
    state_changes: int = 0
    units_booked: int = 0
    buildings_created: int = 0
    unseen_reasons: dict[str, int] = field(default_factory=dict)


@dataclass
class _Building:
    last_ts: datetime | None = None
    """The building's previous poll, while the MES could see it. None
    after a gap, a pause or a disappearance: the next poll books nothing."""
    made: float = 0.0
    booked: int = 0


class Reader:
    def __init__(self, *, poll_seconds: float = 60.0, gap_polls: float = 2.5,
                 area: str = "", node: str | None = None) -> None:
        self.poll_seconds = poll_seconds
        #: The area a building's work centre hangs under when the reader has
        #: to make one. The plant's own code, from its pack; empty, or not in
        #: the plant, and the work centre stands on its own.
        self.area = area
        self.gap = timedelta(seconds=poll_seconds * gap_polls)
        self.node = node
        self.last: datetime | None = None
        self.buildings: dict[str, _Building] = {}
        self.unseen = False
        self.report = Report()

    # ---- resume -----------------------------------------------------------

    def resume(self, session: Session) -> None:
        """Pick up where the last reader stopped: the last poll it applied,
        and the units it booked per building."""
        saved = counters.load(session)
        if FEED_KEY in saved:
            self.last = _from_epoch(saved[FEED_KEY])
        for (code, tag), value in saved.items():
            if tag == "GoodCount":
                self.buildings[code] = _Building(made=float(value), booked=value)
        # Nothing carries over the restart: the time since the last poll
        # applied is a gap until the next poll proves otherwise.

    # ---- one poll ---------------------------------------------------------

    def apply(self, session: Session, poll: feed.Poll) -> None:
        if self.last is not None and poll.ts <= self.last:
            self.report.skipped += 1
            return
        if poll.factory is None and not poll.paused:
            # No building list in this poll: nothing seen, nothing said. The
            # poller sometimes writes the session answer a second after the
            # rest of its poll, which reads as a poll of its own with nothing
            # else in it; it says nothing about the buildings. If the list
            # stays away, the gap rule says so.
            self.report.skipped += 1
            return
        if self.last is not None and poll.ts - self.last > self.gap:
            minutes = (poll.ts - self.last).total_seconds() / 60
            self.unobserve(session, at=self.last + timedelta(seconds=self.poll_seconds),
                           detected_at=poll.ts,
                           reason=f"no poll from the game for {minutes:.0f} min: "
                                  "the game was closed or the poller was off")
            self.report.gaps += 1
        if poll.paused:
            self.unobserve(session, at=poll.ts, detected_at=poll.ts,
                           reason="the game was paused")
            self.report.paused_polls += 1
        else:
            self._buildings(session, poll)
        self.last = poll.ts
        baselines = {(code, "GoodCount"): b.booked for code, b in self.buildings.items()}
        baselines[FEED_KEY] = _epoch(poll.ts)
        counters.save(session, baselines, self.node)
        self.report.polls += 1

    def _buildings(self, session: Session, poll: feed.Poll) -> None:
        tripped = rules.tripped_circuits(poll.power)
        seen: set[str] = set()
        for building in poll.factory or []:
            building_id = building.get("ID")
            if not isinstance(building_id, str):
                continue
            code = rules.code(building_id)
            seen.add(code)
            self._ensure(session, code, building)
            connection.set_connection(session, equipment_code=code,
                                      state=ConnectionStateName.CONNECTED, at=poll.ts,
                                      detected_at=poll.ts, source=SOURCE, actor=ACTOR)
            reading = rules.read(building, tripped)
            open_before = session.scalar(
                select(EquipmentState.id).where(EquipmentState.equipment_id == self._id(session, code),
                                                EquipmentState.ended_at.is_(None)))
            interval = equipment_service.set_state(
                session, equipment_code=code, state=reading.state,
                reason=reading.reason, reason_code=reading.reason_code,
                actor=ACTOR, at=poll.ts)
            if interval.id != open_before:
                self.report.state_changes += 1
            self._book(session, code, building, poll.ts)
        gone = [code for code, b in self.buildings.items()
                if code not in seen and b.last_ts is not None]
        for code in gone:
            self._unobserve_one(session, code, at=poll.ts, detected_at=poll.ts,
                                reason="no longer in the game's building list")
        self.unseen = False

    def _book(self, session: Session, code: str, building: dict, moment: datetime) -> None:
        state = self.buildings.setdefault(code, _Building())
        previous, state.last_ts = state.last_ts, moment
        if previous is None:
            return
        seconds = (moment - previous).total_seconds()
        state.made += rules.rate_per_minute(building) * seconds / 60.0
        units = math.floor(state.made + 1e-9) - state.booked
        if units <= 0:
            return
        execution.report(session, equipment_code=code, good=units,
                         source=ProductionSource.EXTERNAL, source_system=SOURCE,
                         actor=ACTOR, ts=moment)
        state.booked += units
        self.report.units_booked += units

    @staticmethod
    def _id(session: Session, code: str) -> int:
        return masterdata.get_equipment(session, code).id

    # ---- not observed -----------------------------------------------------

    def check_quiet(self, session: Session, now: datetime) -> None:
        """No poll for longer than the gap, by the clock: every building is
        unseen from just after the last poll. Said once, not every look."""
        if self.unseen or self.last is None or now - self.last <= self.gap:
            return
        minutes = (now - self.last).total_seconds() / 60
        self.unobserve(session, at=self.last + timedelta(seconds=self.poll_seconds),
                       detected_at=now,
                       reason=f"no poll from the game for {minutes:.0f} min: "
                              "the game is closed or the poller is off")


    def unobserve(self, session: Session, *, at: datetime, detected_at: datetime,
                  reason: str) -> None:
        """Every building this reader has seen, unseen from `at`, for `reason`.
        Only the game's buildings: a plant that also has other machines keeps
        whatever their own readers say about them. A building in the pack
        that no poll ever listed has no connection rows and reads unknown
        already."""
        for code in sorted(self.buildings):
            self._unobserve_one(session, code, at=at, detected_at=detected_at,
                                reason=reason)
        self.unseen = True

    def _unobserve_one(self, session: Session, code: str, *, at: datetime,
                       detected_at: datetime, reason: str) -> None:
        connection.set_connection(session, equipment_code=code,
                                  state=ConnectionStateName.DISCONNECTED, at=at,
                                  detected_at=detected_at, reason=reason, source=SOURCE,
                                  actor=ACTOR)
        if code in self.buildings:
            self.buildings[code].last_ts = None
        key = reason.split(":")[0]
        self.report.unseen_reasons[key] = self.report.unseen_reasons.get(key, 0) + 1

    # ---- master data on first sight ---------------------------------------

    def _ensure(self, session: Session, code: str, building: dict) -> None:
        """The building as equipment: made on first sight, renamed when its
        recipe changes. A pack's master data usually has it already; a
        building placed since the pack was written is made here, under its
        kind's work centre, with its ideal cycle from the game."""
        machine = session.scalar(select(Equipment).where(Equipment.code == code))
        name = rules.display_name(building)[:120]
        max_rate = rules.max_per_minute(building)
        cycle = 60.0 / max_rate if max_rate > 0 else None
        if machine is None:
            parent = self._ensure_parent(session, building)
            masterdata.create_equipment(session, code=code, name=name,
                                        level=EquipmentLevel.WORK_UNIT, parent_code=parent,
                                        ideal_cycle_seconds=cycle, actor=ACTOR)
            self.report.buildings_created += 1
            return
        if machine.name != name or (cycle and machine.ideal_cycle_seconds != cycle):
            before = {"name": machine.name, "ideal_cycle_seconds": machine.ideal_cycle_seconds}
            machine.name = name
            if cycle:
                machine.ideal_cycle_seconds = cycle
            audit.record(session, actor=ACTOR, action="equipment.updated",
                         entity_type="equipment", entity_id=code, before=before,
                         after={"name": name, "ideal_cycle_seconds": machine.ideal_cycle_seconds})

    def _ensure_parent(self, session: Session, building: dict) -> str:
        code, name = work_centre(building)
        if session.scalar(select(Equipment.id).where(Equipment.code == code)) is None:
            area = self.area if self.area and session.scalar(
                select(Equipment.id).where(Equipment.code == self.area)) is not None else None
            masterdata.create_equipment(session, code=code, name=name,
                                        level=EquipmentLevel.WORK_CENTER, parent_code=area,
                                        actor=ACTOR)
        return code


# ---- the process ----------------------------------------------------------


def replay(folder: Path, session_scope: Callable, *, poll_seconds: float = 60.0,
           gap_polls: float = 2.5, area: str = "", now: datetime | None = None) -> Reader:
    """Every poll in the folder, applied once, then the feed's end said as
    what it is: if the last poll is older than the gap, nothing after it was
    seen. For tests and for `--once`."""
    reader = Reader(poll_seconds=poll_seconds, gap_polls=gap_polls, area=area)
    with session_scope() as session:
        reader.resume(session)
    for poll in feed.read_all(folder):
        with session_scope() as session:
            reader.apply(session, poll)
    with session_scope() as session:
        reader.check_quiet(session, now or utcnow())
    return reader


def run(folder: Path, session_scope: Callable, *, poll_seconds: float = 60.0,
        gap_polls: float = 2.5, area: str = "", interval: float = 5.0, quiet: float = 10.0,
        stop: Callable[[], bool] | None = None) -> Reader:
    """Follow the folder for as long as the process lives.

    Every `interval` seconds it reads what the poller has appended. A poll's
    lines arrive within a second or two of each other, so a poll nothing has
    been added to for `quiet` seconds is complete and is applied. When no
    poll has arrived for `gap_polls` intervals, every building is unseen
    from just after the last one: the feed is not running, and the plant
    says *not observed*, not *down*.
    """
    folder = Path(folder).expanduser()
    reader = Reader(poll_seconds=poll_seconds, gap_polls=gap_polls, area=area)
    follower = feed.Follower(folder)
    with session_scope() as session:
        reader.resume(session)
    log.info("reading the game's poll files", folder=str(folder),
             resumed_from=reader.last.isoformat() if reader.last else None)
    if not feed.day_files(folder):
        log.warning("no poll file yet: every building reads not observed until one arrives",
                    folder=str(folder))
    changed_at = time.monotonic()
    while not (stop and stop()):
        seen_before = dict(follower.offset)
        polls = follower.polls()
        if polls or follower.offset != seen_before:
            changed_at = time.monotonic()
        elif follower.pending is not None and time.monotonic() - changed_at > quiet:
            polls = follower.flush()
        for poll in polls:
            with session_scope() as session:
                reader.apply(session, poll)
        with session_scope() as session:
            reader.check_quiet(session, utcnow())
        if stop and stop():
            break
        time.sleep(interval)
    return reader
