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

PRODUCTION IS THE GAME'S RATE, INTEGRATED, WHILE THE BUILDING RUNS. The mod
gives a rate (`production[0].CurrentProd`, units a minute), not a counter, so
each poll books rate x the time since the building's previous poll, as a
running sum, and only whole units: the fraction waits for the next poll.
Booked production implies run time, so a poll books only when the state the
MES holds for the interval it closes - the one written at the building's
previous poll - is *running*. `CurrentProd` is a rolling figure: a building
that has stopped (starved, blocked, on a tripped fuse) goes on reporting a
rate for minutes, and the game's own `IsProducing` is what says it stopped.
Nothing is booked across a gap or a pause - the rate at either end says
nothing about the time between. A building with no work order books
unassigned production, which is what it is: the player makes things; nobody
ordered them.

ONE SAVE ON THE FLOOR. The session answer names the save being played
(`getSessionInfo.SessionName`). When a building list arrives under a
different save from the last one applied, every building of this reader's
that is not in the list is retired - taken off the floor with its history
kept - for "not in save <name>". A retired building that is listed again
comes back. A poll with no session answer inherits the last name: a change
is a change only when a name is present and different.

ENERGY IS THE DRAW, INTEGRATED, WHILE THE MES CAN SEE THE BUILDING. Each
poll writes, as readings in the tag history the OPC agent writes (one row
per figure, at the poll's moment): every building's draw now and at full
speed (MW), its energy since its previous poll (MWh: the draw now x the time
since, the same way production is booked), and - when it changes - the kWh
one part of its recipe should cost; every circuit's capacity, consumption,
peak demand, production and battery, and whether its fuse is out; every
generator's output, capacity, load and fuel. Energy is booked whatever the
building was doing - a starved building still draws - but never across a
gap or a pause: the draw at either end says nothing about the time between.
`energy` says what each figure is made from.

GENERATORS ARE EQUIPMENT TOO, from the generator answer (`getGenerators`):
made on first sight under a GENERATORS work centre, running while they make
power, idle and starved with no fuel, down on a tripped fuse. A poll with no
generator answer - every file before 2026-10-30 - says nothing about them.

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
from fsmes.domain.equipment import (
    ConnectionStateName,
    EquipmentConnection,
    EquipmentState,
    EquipmentStateName,
)
from fsmes.domain.execution import ProductionSource
from fsmes.domain.masterdata import Equipment, EquipmentLevel
from fsmes.domain.timeseries import TagValue
from fsmes.integrations.satisfactory import energy, feed, rules
from fsmes.services import (
    audit,
    connection,
    counters,
    execution,
    last_play,
    masterdata,
    parts_mix,
)
from fsmes.services import equipment as equipment_service

log = structlog.get_logger("satisfactory")

SOURCE = "Ficsit Remote Monitoring"
ACTOR = "satisfactory-reader"
#: The counter-baseline row that holds the last poll applied. Not a machine:
#: the reader's own place in the files.
FEED_KEY = ("SATISFACTORY-FEED", "PollEpoch")
#: The save of the last building list applied, as a counter-baseline row
#: `("SATISFACTORY-FEED", "Save:<name>")` holding the epoch it was last
#: applied at - the newest row is the save on the floor.
SAVE_TAG = "Save:"
#: Where a generator is filed when the reader has to make it.
GENERATORS = ("GENERATORS", "Generators")


def is_generator(code: str) -> bool:
    """Every generator's game class starts `Build_Generator`."""
    return code.startswith("GENERATOR")


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
    retired: int = 0
    returned: int = 0
    readings: int = 0
    energy_mwh: float = 0.0
    unseen_reasons: dict[str, int] = field(default_factory=dict)


@dataclass
class _Building:
    last_ts: datetime | None = None
    """The building's previous poll, while the MES could see it. None
    after a gap, a pause or a disappearance: the next poll books nothing."""
    made: float = 0.0
    booked: int = 0
    running: bool = False
    """Whether the state written at the building's previous poll was
    *running*: the interval the next poll closes is run time only then."""
    absent: int = 0
    """Unpaused building lists of this save in a row that left it out. At
    `torn_down_polls` the building is retired, "torn down"."""


class Reader:
    def __init__(self, *, poll_seconds: float = 60.0, gap_polls: float = 2.5,
                 area: str = "", node: str | None = None,
                 recipes: energy.Recipes | None = None, torn_down_polls: int = 5) -> None:
        self.poll_seconds = poll_seconds
        #: How many unpaused building lists of one save in a row may leave a
        #: building out before it is retired as torn down.
        self.torn_down_polls = max(1, int(torn_down_polls))
        #: The last poll of the stretch being watched (`services.last_play`);
        #: None after a gap, a pause or a start.
        self.stretch_last: datetime | None = None
        #: The item last written per building, so it is written on change.
        self.items_said: dict[str, str] = {}
        #: The game's recipe list, for the energy a part should cost. Empty,
        #: and every ideal is unknown.
        self.recipes = recipes or energy.Recipes()
        #: MW at full speed per kind of building, as last seen at 100 % clock.
        self.nominal: dict[str, float] = {}
        #: The ideal last written per building, so it is written on change.
        self.ideal_said: dict[str, tuple[float | None, str]] = {}
        self._ids: dict[str, int] = {}
        #: The area a building's work centre hangs under when the reader has
        #: to make one. The plant's own code, from its pack; empty, or not in
        #: the plant, and the work centre stands on its own.
        self.area = area
        self.gap = timedelta(seconds=poll_seconds * gap_polls)
        self.node = node
        self.last: datetime | None = None
        self.buildings: dict[str, _Building] = {}
        self.unseen = False
        #: The save named by the latest session answer, and the save of the
        #: last building list applied. Different means the player loaded
        #: another save since.
        self.save: str | None = None
        self.applied_save: str | None = None
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
        self.applied_save = self.save = saved_save(saved)
        # Nothing carries over the restart: the time since the last poll
        # applied is a gap until the next poll proves otherwise.

    # ---- one poll ---------------------------------------------------------

    def apply(self, session: Session, poll: feed.Poll) -> None:
        name = (poll.session or {}).get("SessionName")
        if isinstance(name, str) and name.strip():
            self.save = name.strip()
        if self.last is not None and poll.ts <= self.last:
            # Applied before this reader started; its save was the one on
            # the floor then.
            if poll.factory is not None and self.save:
                self.applied_save = self.save
            self.report.skipped += 1
            return
        if poll.factory is None and not poll.paused:
            # No building list in this poll: nothing seen, nothing said. An
            # answer the poller wrote a second after the rest of its poll is
            # already part of that poll (`feed.late`); what is left here is a
            # poll that genuinely had no building list. If the list stays
            # away, the gap rule says so.
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
            self._watched(session, poll)
        self.last = poll.ts
        baselines = {(code, "GoodCount"): b.booked for code, b in self.buildings.items()}
        baselines[FEED_KEY] = _epoch(poll.ts)
        if self.applied_save:
            baselines[(FEED_KEY[0], (SAVE_TAG + self.applied_save)[:80])] = _epoch(poll.ts)
        counters.save(session, baselines, self.node)
        self.report.polls += 1

    def _watched(self, session: Session, poll: feed.Poll) -> None:
        """One stretch reading for this watched poll - 0 opens a stretch, 1
        continues it - and the game's own production figures, the check on
        what was booked (`fsmes.services.last_play`, `.parts_mix`)."""
        self._reading(session, None, last_play.STRETCH,
                      0.0 if self.stretch_last is None else 1.0, poll.ts)
        self.stretch_last = poll.ts
        for row in poll.prod_stats or []:
            name = row.get("Name") if isinstance(row, dict) else None
            if not isinstance(name, str) or not name.strip():
                continue
            figures = {parts_mix.MADE: row.get("CurrentProd"),
                       parts_mix.MAX: row.get("MaxProd"),
                       parts_mix.CONSUMED: row.get("CurrentConsumed")}
            numbers = {k: float(v) for k, v in figures.items()
                       if isinstance(v, (int, float)) and not isinstance(v, bool)}
            if not any(numbers.values()):
                # An item the save neither makes nor uses at this poll: a
                # row of zeros says nothing the absence of one does not.
                continue
            for field_, value in numbers.items():
                self._reading(session, None, parts_mix.parts_tag(name.strip(), field_),
                              value, poll.ts)

    def _item(self, session: Session, code: str, building: dict, moment: datetime) -> None:
        """The item the building makes, written when it changes."""
        item = rules.item(building)
        if item and self.items_said.get(code) != item:
            self._reading(session, code, parts_mix.ITEM, item, moment)
            self.items_said[code] = item

    def _buildings(self, session: Session, poll: feed.Poll) -> None:
        tripped = rules.tripped_circuits(poll.power)
        seen: set[str] = set()
        self.nominal.update(energy.nominal_draws(poll.factory))
        self._circuits(session, poll)
        for building in poll.factory or []:
            building_id = building.get("ID")
            if not isinstance(building_id, str):
                continue
            code = rules.code(building_id)
            seen.add(code)
            self._ensure(session, code, building)
            if masterdata.return_equipment(
                    session, code, actor=ACTOR,
                    reason=f"in save {self.save}" if self.save else "in the game's building list"):
                self.report.returned += 1
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
            previous = self.buildings[code].last_ts if code in self.buildings else None
            self._item(session, code, building, poll.ts)
            self._book(session, code, building, poll.ts,
                       running=reading.state is EquipmentStateName.RUNNING)
            self._draw(session, code, building, poll.ts, previous)
        if poll.generators is None:
            # No generator answer in this poll: the generators are neither
            # seen nor gone.
            seen |= {code for code in self.buildings if is_generator(code)}
        else:
            self._generators(session, poll, tripped, seen)
        if self.save and self.applied_save and self.save != self.applied_save:
            self._retire_other_save(session, seen, poll.ts,
                                    generators_listed=poll.generators is not None)
        if self.save:
            self.applied_save = self.save
        gone = [code for code, b in self.buildings.items()
                if code not in seen and b.last_ts is not None]
        for code in gone:
            self._unobserve_one(session, code, at=poll.ts, detected_at=poll.ts,
                                reason="no longer in the game's building list")
        self._torn_down(session, seen, poll.ts)
        self.unseen = False

    def _torn_down(self, session: Session, seen: set[str], moment: datetime) -> None:
        """A building left out of `torn_down_polls` unpaused lists of the same
        save in a row is retired, "torn down"; listed again, it comes back
        (`_buildings` returns every building it lists). One list leaving a
        building out is not enough: the mod has been seen to drop one for a
        poll."""
        for code, state in self.buildings.items():
            if code in seen:
                state.absent = 0
                continue
            state.absent += 1
            if state.absent != self.torn_down_polls:
                continue
            machine = session.scalar(select(Equipment).where(Equipment.code == code))
            if machine is None or machine.retired_at is not None:
                continue
            masterdata.retire_equipment(session, code, reason="torn down", at=moment,
                                        actor=ACTOR)
            self.report.retired += 1

    def _retire_other_save(self, session: Session, seen: set[str], moment: datetime,
                           generators_listed: bool = True) -> None:
        """Another save is being played: every building this reader has
        written about that is not in its list leaves the floor. The game's
        buildings are the ones whose connection this reader wrote, so one
        the reader knew only in an earlier process is found too."""
        reason = f"not in save {self.save}"
        known = set(self.buildings) | set(session.scalars(
            select(Equipment.code).join(EquipmentConnection,
                                        EquipmentConnection.equipment_id == Equipment.id)
            .where(EquipmentConnection.source == SOURCE).distinct()))
        if not generators_listed:
            known = {code for code in known if not is_generator(code)}
        for code in sorted(known - seen):
            machine = session.scalar(select(Equipment).where(Equipment.code == code))
            if machine is None or machine.retired_at is not None:
                continue
            self._unobserve_one(session, code, at=moment, detected_at=moment, reason=reason)
            masterdata.retire_equipment(session, code, reason=reason, at=moment, actor=ACTOR)
            self.report.retired += 1

    def _book(self, session: Session, code: str, building: dict, moment: datetime, *,
              running: bool) -> None:
        state = self.buildings.setdefault(code, _Building())
        previous, state.last_ts = state.last_ts, moment
        was_running, state.running = state.running, running
        if previous is None or not was_running:
            # Unseen before this poll, or not running since the last one:
            # whatever rate the game still reports, the MES holds no run time
            # for this interval and books nothing in it.
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

    def _id(self, session: Session, code: str) -> int:
        if code not in self._ids:
            self._ids[code] = masterdata.get_equipment(session, code).id
        return self._ids[code]

    # ---- power --------------------------------------------------------------

    def _reading(self, session: Session, code: str | None, tag: str, value,
                 moment: datetime) -> None:
        """One figure into the tag history, at the poll's moment, as the OPC
        agent writes one. `code` None is a figure about no machine: a circuit."""
        if value is None:
            return
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        session.add(TagValue(
            equipment_id=self._id(session, code) if code else None,
            tag=f"{code}.{tag}" if code else tag, ts=moment,
            value_num=float(value) if numeric else None,
            value_text=None if numeric else str(value)[:120]))
        self.report.readings += 1

    def _circuits(self, session: Session, poll: feed.Poll) -> None:
        for circuit in energy.circuits(poll.power):
            for field_, value in circuit.figures.items():
                self._reading(session, None, energy.circuit_tag(circuit.group, field_),
                              value, poll.ts)
            self._reading(session, None, energy.circuit_tag(circuit.group, energy.TRIPPED),
                          1.0 if circuit.tripped else 0.0, poll.ts)

    def _energy(self, session: Session, code: str, mw: float | None, moment: datetime,
                previous: datetime | None) -> None:
        """The energy of the interval this poll closes, when the MES saw both
        of its ends."""
        if previous is None:
            return
        booked = energy.mwh(mw, (moment - previous).total_seconds())
        if booked > 0:
            self._reading(session, code, energy.ENERGY, booked, moment)
            self.report.energy_mwh += booked

    def _draw(self, session: Session, code: str, building: dict, moment: datetime,
              previous: datetime | None) -> None:
        now, top = energy.draw(building)
        self._reading(session, code, energy.DRAW, now, moment)
        self._reading(session, code, energy.DRAW_MAX, top, moment)
        self._energy(session, code, now, moment, previous)
        ideal = self.recipes.ideal(building, self.nominal)
        said = (None if ideal is None or ideal.kwh is None else round(ideal.kwh, 6),
                "none" if ideal is None else ideal.why)
        if ideal is not None and self.ideal_said.get(code) != said:
            # A number when there is one; otherwise the sentence saying why
            # there is not, so the latest reading is never an older recipe's.
            self._reading(session, code, energy.IDEAL,
                          ideal.kwh if ideal.kwh is not None else ideal.why, moment)
        self.ideal_said[code] = said

    def _generators(self, session: Session, poll: feed.Poll, tripped: set[int],
                    seen: set[str]) -> None:
        for row in poll.generators or []:
            ident = row.get("ID")
            if not isinstance(ident, str):
                continue
            code = rules.code(ident)
            seen.add(code)
            self._ensure(session, code, row, parent=GENERATORS)
            if masterdata.return_equipment(
                    session, code, actor=ACTOR,
                    reason=f"in save {self.save}" if self.save else "in the game's generator list"):
                self.report.returned += 1
            connection.set_connection(session, equipment_code=code,
                                      state=ConnectionStateName.CONNECTED, at=poll.ts,
                                      detected_at=poll.ts, source=SOURCE, actor=ACTOR)
            gen = energy.generator(row)
            circuit = rules.circuit_of(row)
            if (row.get("PowerInfo") or {}).get("FuseTriggered") or (
                    circuit is not None and circuit in tripped):
                reading = rules.Reading(EquipmentStateName.DOWN, "fuse")
            elif gen.running:
                reading = rules.Reading(EquipmentStateName.RUNNING)
            elif not gen.fuelled:
                reading = rules.Reading(EquipmentStateName.IDLE, "starved")
            else:
                reading = rules.Reading(EquipmentStateName.IDLE)
            open_before = session.scalar(
                select(EquipmentState.id).where(EquipmentState.equipment_id == self._id(session, code),
                                                EquipmentState.ended_at.is_(None)))
            interval = equipment_service.set_state(
                session, equipment_code=code, state=reading.state, reason=reading.reason,
                reason_code=reading.reason_code, actor=ACTOR, at=poll.ts)
            if interval.id != open_before:
                self.report.state_changes += 1
            state = self.buildings.setdefault(code, _Building())
            previous, state.last_ts = state.last_ts, poll.ts
            state.running = reading.state is EquipmentStateName.RUNNING
            self._reading(session, code, energy.OUTPUT, gen.output_mw, poll.ts)
            self._reading(session, code, energy.CAPACITY, gen.capacity_mw, poll.ts)
            self._reading(session, code, energy.LOAD, gen.load_percent, poll.ts)
            self._reading(session, code, energy.FUEL, gen.fuel, poll.ts)
            self._reading(session, code, energy.FUEL_ITEMS, gen.fuel_items, poll.ts)
            # What a generator books is what it made.
            self._energy(session, code, gen.output_mw, poll.ts, previous)

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
        # Nothing is booked across a gap or a pause, and no stretch runs
        # across one either.
        self.stretch_last = None

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

    def _ensure(self, session: Session, code: str, building: dict,
                parent: tuple[str, str] | None = None) -> None:
        """The building as equipment: made on first sight, renamed when its
        recipe changes. A pack's master data usually has it already; a
        building placed since the pack was written is made here, under its
        kind's work centre, with its ideal cycle from the game."""
        machine = session.scalar(select(Equipment).where(Equipment.code == code))
        name = rules.display_name(building)[:120]
        max_rate = rules.max_per_minute(building)
        cycle = 60.0 / max_rate if max_rate > 0 else None
        if machine is None:
            centre = self._ensure_parent(session, parent or work_centre(building))
            masterdata.create_equipment(session, code=code, name=name,
                                        level=EquipmentLevel.WORK_UNIT, parent_code=centre,
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

    def _ensure_parent(self, session: Session, centre: tuple[str, str]) -> str:
        code, name = centre
        if session.scalar(select(Equipment.id).where(Equipment.code == code)) is None:
            area = self.area if self.area and session.scalar(
                select(Equipment.id).where(Equipment.code == self.area)) is not None else None
            masterdata.create_equipment(session, code=code, name=name,
                                        level=EquipmentLevel.WORK_CENTER, parent_code=area,
                                        actor=ACTOR)
        return code


# ---- the save on the floor ------------------------------------------------


def saved_save(saved: dict[tuple[str, str], int]) -> str | None:
    """The save of the last building list applied, from the counter
    baselines: the newest `Save:` row. None before any poll named one."""
    rows = [(value, tag[len(SAVE_TAG):]) for (code, tag), value in saved.items()
            if code == FEED_KEY[0] and tag.startswith(SAVE_TAG)]
    return max(rows)[1] if rows else None


def source_note(session: Session, note: str) -> str:
    """The plant's sentence about its source, with the save on the floor in
    front of it: "save fsmes · A game factory ...". The sentence alone
    before any poll has named a save."""
    from fsmes.domain import CounterBaseline

    rows = session.execute(
        select(CounterBaseline.equipment_code, CounterBaseline.tag, CounterBaseline.value)
        .where(CounterBaseline.equipment_code == FEED_KEY[0],
               CounterBaseline.tag.startswith(SAVE_TAG))).all()
    save = saved_save({(code, tag): int(value) for code, tag, value in rows})
    if not save:
        return note
    return f"save {save} · {note}" if note else f"save {save}"


# ---- the process ----------------------------------------------------------


def replay(folder: Path, session_scope: Callable, *, poll_seconds: float = 60.0,
           gap_polls: float = 2.5, area: str = "", now: datetime | None = None,
           torn_down_polls: int = 5) -> Reader:
    """Every poll in the folder, applied once, then the feed's end said as
    what it is: if the last poll is older than the gap, nothing after it was
    seen. For tests and for `--once`."""
    reader = Reader(poll_seconds=poll_seconds, gap_polls=gap_polls, area=area,
                    torn_down_polls=torn_down_polls, recipes=energy.Recipes.load(Path(folder)))
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
        stop: Callable[[], bool] | None = None, torn_down_polls: int = 5) -> Reader:
    """Follow the folder for as long as the process lives.

    Every `interval` seconds it reads what the poller has appended. A poll's
    lines arrive within a second or two of each other, so a poll nothing has
    been added to for `quiet` seconds is complete and is applied. When no
    poll has arrived for `gap_polls` intervals, every building is unseen
    from just after the last one: the feed is not running, and the plant
    says *not observed*, not *down*.
    """
    folder = Path(folder).expanduser()
    reader = Reader(poll_seconds=poll_seconds, gap_polls=gap_polls, area=area,
                    torn_down_polls=torn_down_polls, recipes=energy.Recipes.load(folder))
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
