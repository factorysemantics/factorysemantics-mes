"""What the line actually did, read out of the data the replay obeyed.

`fsmes.sim.truth` already reads the *script* - the events somebody wrote down.
This reads the *consequence*: the counters and states the generator produced
from that script, second by second, which is what the MES's own numbers have
to be compared against. Both are ground truth by construction; they answer
different questions, and neither is a second opinion about the other.

Two things about a replay decide how these numbers are read, and both are
stated in every measurement that uses them rather than assumed:

**The file loops.** `fsmes.integrations.opc.csv_replay` plays row `tick %
len(rows)`, so a run that is left playing past the end of the script starts the
hour again with its counters wrapped to zero. A run always is left playing a
little past the end - the agent needs time to drain what it has already read -
so the truth for a run is one full pass *plus* whatever the overlap replayed,
and the size of that overlap is the band inside which a difference in units is
not evidence about the MES. The band grows with replay speed, which is the
honest reason to run an experiment slowly.

**Line time is not wall time.** The generator's second is a line second; at
speed 10 the MES lives through one of them every hundred milliseconds and
records wall seconds. Everything here is in line seconds, and the measurements
convert the MES's numbers rather than the truth's.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

#: The generator's State column. Named here rather than imported as six bare
#: integers so a reader of a measurement can see what `2` meant.
STATE_NAMES = {0: "stopped", 1: "running", 2: "starved", 3: "blocked",
               4: "down", 5: "changeover"}

#: States in which the machine is making nothing through no fault of its own.
IDLE_STATES = ("starved", "blocked")


@dataclass
class StationTruth:
    """One machine's hour, as the line lived it."""

    station: str
    good: int = 0
    scrap: int = 0
    seconds_by_state: dict[str, int] = field(default_factory=dict)
    run_minutes_reported: float | None = None
    rate_per_min: float | None = None
    #: Production in the overlap - the part of a second pass a run replayed
    #: while the agent drained. Not a fault; a band.
    overlap_good: int = 0
    overlap_scrap: int = 0
    overlap_seconds_by_state: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.good + self.scrap

    @property
    def running_seconds(self) -> int:
        return self.seconds_by_state.get("running", 0)

    @property
    def down_seconds(self) -> int:
        return self.seconds_by_state.get("down", 0)

    @property
    def changeover_seconds(self) -> int:
        return self.seconds_by_state.get("changeover", 0)

    @property
    def ideal_cycle_seconds(self) -> float | None:
        """The cycle the line description rated this machine at."""
        return 60.0 / self.rate_per_min if self.rate_per_min else None


@dataclass
class LineTruth:
    """The whole line's hour: every machine, and what each order made."""

    duration_s: int
    overlap_s: int
    stations: dict[str, StationTruth] = field(default_factory=dict)
    good_by_order: dict[str, int] = field(default_factory=dict)
    overlap_good_by_order: dict[str, int] = field(default_factory=dict)
    last_station: str | None = None
    #: What the line had made, second by second, from the first row of the
    #: replay. Index is the line second; the value is the cumulative good count
    #: off the end of the line, summed as positive deltas the way the MES books
    #: them (a counter reset is a re-baseline, not production going backwards).
    #: Kept because a during-run measurement asks what the line had made at the
    #: moment it looked, and the totals cannot answer that.
    line_good_at: list[int] = field(default_factory=list)

    def good_at(self, second: float) -> int | None:
        """What the line had made by this line second, the replay's loop included.

        The file loops: past its last row the replay starts the hour again, so
        a run left playing into a second pass really has made a full pass plus
        whatever of the next one it got to. That is production, not an error,
        and a watcher that looked at that moment saw it.
        """
        if not self.line_good_at or second < 0:
            return None
        span = len(self.line_good_at)
        laps, rest = divmod(int(second), span)
        return laps * self.line_good_at[-1] + self.line_good_at[rest]

    def good_between(self, start: float, end: float) -> int | None:
        """What the line made between two line seconds, or None if unknowable."""
        first, last = self.good_at(start), self.good_at(end)
        if first is None or last is None:
            return None
        return last - first

    @property
    def line_good(self) -> int:
        """What came off the end of the line - the plant's own output."""
        return self.stations[self.last_station].good if self.last_station else 0

    @property
    def line_scrap(self) -> int:
        return sum(s.scrap for s in self.stations.values())

    def as_json(self) -> dict:
        return {
            "duration_s": self.duration_s,
            "replay_overlap_s": self.overlap_s,
            "stations_total": len(self.stations),
            "last_station": self.last_station,
            "line_good": self.line_good,
            "line_scrap": self.line_scrap,
            # `line_good_at` is deliberately absent: it is one integer per line
            # second and truth.json is a file a person opens. The during-run
            # measurement that needs it has it in memory, and what it made of
            # it is in scores.json.
            "orders_total": len(self.good_by_order),
            "good_by_order": self.good_by_order,
            "overlap_good_by_order": self.overlap_good_by_order,
            "stations": {
                name: {
                    "good": s.good,
                    "scrap": s.scrap,
                    "seconds_by_state": s.seconds_by_state,
                    "running_seconds": s.running_seconds,
                    "down_seconds": s.down_seconds,
                    "changeover_seconds": s.changeover_seconds,
                    "rate_per_min": s.rate_per_min,
                    "ideal_cycle_seconds": s.ideal_cycle_seconds,
                    "run_minutes_reported": s.run_minutes_reported,
                    "overlap_good": s.overlap_good,
                    "overlap_scrap": s.overlap_scrap,
                    "overlap_seconds_by_state": s.overlap_seconds_by_state,
                }
                for name, s in self.stations.items()
            },
        }


def _counted(rows: list[dict[str, str]], column: str, numeric=int):
    """What this counter added over these rows, resets and all.

    A counter that goes backwards has been re-baselined, not run in reverse:
    the step across a reset is dropped rather than counted as a negative, which
    is the same rule the MES applies to the same reading.
    """
    total, previous = numeric(0), None
    for row in rows:
        value = numeric(row[column])
        if previous is not None and value >= previous:
            total += value - previous
        elif previous is None:
            total += value
        previous = value
    return round(total, 2) if numeric is float else total


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="ascii", newline="") as handle:
        return list(csv.DictReader(handle))


def overlap_seconds(duration_s: int, played_wall_s: float, speed: float) -> int:
    """How much of a second pass a run replayed, in line seconds.

    Clamped at one full pass: a run left playing for several hours would wrap
    more than once, and this number exists to size a band rather than to
    reconstruct an arbitrarily long replay.
    """
    played = max(0.0, played_wall_s * speed - duration_s)
    return int(min(played, duration_s))


def read(replay_dir: Path, line: dict, duration_s: int, overlap_s: int = 0) -> LineTruth:
    """The hour the generated data describes, for a run that replayed
    `duration_s` line seconds and then `overlap_s` more of a second pass."""
    replay_dir = Path(replay_dir)
    stations = [s["name"] for s in line.get("stations", [])]
    rated = {s["name"]: s.get("rate_per_min") for s in line.get("stations", [])}
    truth = LineTruth(duration_s=duration_s, overlap_s=overlap_s,
                      last_station=stations[-1] if stations else None)

    for name in stations:
        path = replay_dir / f"{name}.csv"
        if not path.is_file():
            continue
        rows = _rows(path)
        station = StationTruth(station=name, rate_per_min=rated.get(name))
        seconds: dict[str, int] = {}
        overlap: dict[str, int] = {}
        for index, row in enumerate(rows):
            state = STATE_NAMES.get(int(row["State"]), str(row["State"]))
            seconds[state] = seconds.get(state, 0) + 1
            if index < overlap_s:
                overlap[state] = overlap.get(state, 0) + 1
        station.seconds_by_state = seconds
        station.overlap_seconds_by_state = overlap
        if rows:
            # Summed as positive deltas, not read off the last row. A counter
            # reset is one of the things a script can write - the PLC snaps to
            # zero after a power blip - and after one the last row holds what
            # the machine has made *since the reset*, not what it made in the
            # hour. Reading it as the hour's total said the line had made four
            # thousand fewer units than it had, and the first run of the lab
            # accused the MES of inventing them. The MES books deltas; so does
            # this, which is the same rule applied to both sides.
            station.good = _counted(rows, "GoodCount")
            station.scrap = _counted(rows, "ScrapCount")
            station.run_minutes_reported = _counted(rows, "RunMinutes", numeric=float)
            if overlap_s:
                edge = rows[:min(overlap_s, len(rows))]
                station.overlap_good = _counted(edge, "GoodCount")
                station.overlap_scrap = _counted(edge, "ScrapCount")
        truth.stations[name] = station

    line_csv = replay_dir / "Line.csv"
    if line_csv.is_file():
        rows = _rows(line_csv)
        previous = 0
        running = 0
        for index, row in enumerate(rows):
            order = str(row["OrderId"])
            made = int(row["LineGoodCount"])
            step = max(0, made - previous)
            running += step
            truth.line_good_at.append(running)
            truth.good_by_order[order] = truth.good_by_order.get(order, 0) + step
            if index < overlap_s:
                truth.overlap_good_by_order[order] = (
                    truth.overlap_good_by_order.get(order, 0) + step)
            previous = made
    return truth
