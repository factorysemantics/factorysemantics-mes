"""The scripted hours, turned into a labelled set of stops.

The simulated plants script their own breakdowns, changeovers, micro-stops
and idling as named regression tests, so the true reason for every stop in a
recorded run is already written down - in `line/<plant>.json`, which is not
documentation but the input the generator obeyed. That makes an evaluation
set most people who wire a model into an MES will never have: a stop, the
window around it as the MES saw it, and the reason it actually had.

This module builds that set and nothing else. It asks nothing, decides
nothing and grades nothing; `fsmes.sim.calibration` does the asking and the
measuring. Keeping them apart is what lets the set be built, read and
argued with on a machine that has no key and no network.

**Where the label comes from.** The generator writes one state word per
machine per simulated second, and its order of precedence is fixed and
readable (`fsmes.sim.generate.simulate`): changeover, then breakdown, then a
pre-rolled micro-stop, then scripted blocking, then scripted starving, then
the blocking and starving that the line's own buffers produce. So the state
word *is* the truth about why a machine stopped, and the label is read off
it rather than inferred. Whether a scripted event named the window is
recorded separately, because a stop the buffers produced on their own is
still a real stop with a real reason - it is simply one nobody wrote down in
advance.

**What is withheld, and why.** That same state word would hand the answer to
anyone reading the window, and a question whose answer is printed in its own
evidence measures nothing. Real machine layers vary: some publish a reason
code, most publish a run bit, an alarm word and counters and leave the
reason to be worked out. So the default view (`stopped-not-why`) replaces
the state word with a plain `Running` column - 1 while the machine was
making something, 0 while it was not - and keeps every other tag, including
the alarm word and the ready bit, exactly as the machine published them. The
`full` view keeps the state word. Both are built by the same code, so the
difference between what they measure is the reason code and nothing else,
and that difference is itself a number worth having.

**What the MES did not see stays unseen.** A scripted disconnect closes the
OPC endpoint: the line runs on and the MES has no state for those seconds at
all (decision 0030). The tag history still holds them, because the
generator wrote them, but handing them to a question about what the MES
observed would be inventing observation. So rows inside a disconnect are cut
out of the window and replaced by a line saying how long the gap was, and a
stop that happened entirely inside one is marked unobservable.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from fsmes.sim import generate, truth

#: The vocabulary, taken from the generator's own event types and state
#: words rather than invented here. Seven options, stated because a fixed
#: vocabulary's total is the measure of what it cannot say: a stop whose
#: real reason is not one of these seven can only be answered wrongly.
REASONS: tuple[tuple[str, str], ...] = (
    ("breakdown",
     "The machine broke down. It was willing to run, something failed, and "
     "it made nothing until it was fixed."),
    ("changeover",
     "The line was changing over from one product to the next. A planned "
     "stop: nothing is wrong and nobody is being called out."),
    ("micro_stop",
     "A short stop of the kind that happens many times an hour - a jam, a "
     "misfeed, a reset - cleared in seconds by whoever was standing there. "
     "Nothing is broken and nothing needs reporting."),
    ("starved",
     "Nothing arrived for it to work on. The machine was willing and there "
     "was nothing wrong with it; the shortage was upstream."),
    ("blocked",
     "There was nowhere to put what it made. The machine was willing and "
     "there was nothing wrong with it; the hold-up was downstream."),
    ("counter_reset",
     "The machine's counters went back to zero without the machine "
     "stopping - a controller restart or a shift reset, not a production "
     "event at all."),
    ("none",
     "The machine did not stop in this window. It ran throughout and there "
     "is no reason to give."),
)

REASON_NAMES: tuple[str, ...] = tuple(name for name, _ in REASONS)

#: The state word, as the label it stands for. Read straight off
#: `fsmes.sim.generate`'s constants so the two cannot drift apart.
_STATE_LABEL: dict[int, str] = {
    generate.CHANGEOVER: "changeover",
    generate.DOWN: "breakdown",
    generate.STOPPED: "micro_stop",
    generate.BLOCKED: "blocked",
    generate.STARVED: "starved",
}

#: The scripted event type that names each label, where one can.
_SCRIPTED_BY: dict[str, tuple[str, ...]] = {
    "breakdown": ("down",),
    "changeover": ("changeover",),
    "micro_stop": ("micro_stops",),
    "starved": ("starve",),
    "blocked": ("block",),
}

#: Below this many seconds nothing in the truth can be named: the shortest
#: stop this generator ever scripts is a six-second micro-stop, and every
#: window shorter than five seconds is the line's buffers breathing rather
#: than a stop anybody would ask about. Stated here, overridable on the
#: command line, and the number that was used is written into the set.
DEFAULT_MINIMUM_SECONDS = 5

#: How much line either side of the stop goes into the window. A minute is
#: enough to show what the neighbours were doing before it stopped, which is
#: the only way to tell starved from blocked, and short enough that the
#: question stays a few thousand tokens rather than ten.
DEFAULT_WINDOW_SECONDS = 60

#: At one row per second a long stop would send hundreds of rows of very
#: little news. Above this many the window is thinned by taking every nth
#: row - never the first or the last, which are the two that matter - and
#: the window says it was thinned.
DEFAULT_MAXIMUM_ROWS = 120

#: A neighbour is there to answer "was anything arriving" and "was anything
#: leaving", which needs its counters and its run bit and nothing else.
DEFAULT_NEIGHBOUR_ROWS = 60
NEIGHBOUR_COLUMNS = ("TSec", "Running", "GoodCount", "ScrapCount")

#: Windows in which the machine never stopped, kept so the set has something
#: to be wrong about in the other direction. A question asked only about
#: stops cannot show a model inventing a stop, and inventing one is exactly
#: what round 5 raised: a condition that scored highest on every run,
#: including the six where nothing of the kind happened. One per machine:
#: enough that every machine contributes a window it ran clean through, few
#: enough that a quiet run does not become mostly controls.
DEFAULT_CONTROLS_PER_MACHINE = 1

STATE_VIEWS = ("stopped-not-why", "full")


@dataclass(frozen=True)
class Stop:
    """One window of a recorded run, with the reason it actually had."""

    results: str
    experiment: str
    plant: str
    station: str
    equipment: str
    start_s: int
    end_s: int
    label: str
    label_says: str
    scripted: bool
    observable: bool
    unseen_seconds: int
    state: str
    state_view: str
    state_rows: int
    thinned: bool
    mes_verdict: dict | None
    mes_verdict_says: str

    @property
    def id(self) -> str:
        return f"{self.results}/{self.plant}/{self.equipment}/{self.start_s}"

    @property
    def seconds(self) -> int:
        return self.end_s - self.start_s

    def as_record(self) -> dict:
        return {
            "id": self.id,
            "results": self.results,
            "experiment": self.experiment,
            "plant": self.plant,
            "station": self.station,
            "equipment": self.equipment,
            "window_sim_s": [self.start_s, self.end_s],
            "seconds": self.seconds,
            "label": self.label,
            "label_says": self.label_says,
            "scripted": self.scripted,
            "observable": self.observable,
            "unseen_seconds": self.unseen_seconds,
            "state_view": self.state_view,
            "state_rows": self.state_rows,
            "state_characters": len(self.state),
            "state_thinned": self.thinned,
            "state_sha256": hashlib.sha256(
                self.state.encode("utf-8")).hexdigest()[:16],
            "state": self.state,
            "mes_verdict": self.mes_verdict,
            "mes_verdict_says": self.mes_verdict_says,
        }


@dataclass
class _Run:
    """One results directory's worth of what a set is built from."""

    name: str
    experiment: str
    plants: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _line_json(results: Path, plant: str) -> Path:
    return results / "line" / f"{plant}.json"


def _tag_map(results: Path, plant: str) -> Path:
    return results / "packs" / plant / "tag_map.json"


def plants_in(results: Path) -> list[str]:
    """Every plant a results directory recorded, by name, in file order.

    Read from the replay directories rather than from `scores.json`, because
    a run that was killed part way through still wrote its replay and is
    still a perfectly good source of labelled stops.
    """
    replay = results / "replay"
    if not replay.is_dir():
        return []
    return sorted(p.name for p in replay.iterdir() if p.is_dir())


def _station_order(line: dict) -> list[list[str]]:
    """The stations of each line, in the order the line runs them.

    A factory description holds several lines, and a station's neighbours
    are the ones on its own line - the palletiser at the end of the bottling
    line is not downstream of anything on the machining line. The names are
    namespaced exactly as `fsmes.sim.generate` namespaces them, by calling
    the same function, so the order lines up with the CSV file names.
    """
    if "lines" in line:
        return [[s["name"] for s in generate._prefix_line(entry["name"], entry)
                 .get("stations", [])]
                for entry in line["lines"]]
    return [[s["name"] for s in line.get("stations", [])]]


def _disconnect_windows(events: list) -> list[tuple[int, int]]:
    """When the MES could see nothing at all, in line seconds."""
    return sorted((int(e.start_s), int(e.end_s)) for e in events
                  if e.is_disconnect and e.start_s is not None and e.end_s is not None)


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def _read_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _stops_in(rows: list[dict], *, minimum_seconds: int) -> list[tuple[int, int, int]]:
    """Contiguous runs of one non-running state, as `(state, start, end)`.

    `end` is exclusive, in the CSV's own line seconds. A run of adjacent but
    different non-running states is two stops, not one: a machine that was
    starved and then broke is two things that happened, and merging them
    would put a window in the set that has no single true answer.
    """
    out: list[tuple[int, int, int]] = []
    state: int | None = None
    start = 0
    last = 0
    for row in rows:
        second = int(row["TSec"])
        here = int(row["State"])
        if here == state:
            last = second
            continue
        if state is not None:
            out.append((state, start, last + 1))
        state = here if here != generate.RUNNING else None
        start = second
        last = second
    if state is not None:
        out.append((state, start, last + 1))
    return [stop for stop in out if stop[2] - stop[1] >= minimum_seconds]


def _controls_in(rows: list[dict], stops: list[tuple[int, int, int]], *,
                 window_seconds: int, wanted: int) -> list[tuple[int, int]]:
    """Windows this machine ran straight through, spread across the run.

    One anchor per control, evenly spaced through the hour, and from each
    anchor the first window forward that touches no stop. Spread rather than
    taken from the start, because the first quiet minute of a run is the
    least interesting minute in it and a set of controls all cut from it
    would say nothing about the rest of the hour. No randomness anywhere, so
    the same run always yields the same controls.
    """
    if wanted <= 0 or not rows:
        return []
    first, last = int(rows[0]["TSec"]), int(rows[-1]["TSec"])
    span = window_seconds
    if last - first < span:
        return []
    out: list[tuple[int, int]] = []
    room = last - first - span
    for index in range(wanted):
        anchor = first + int(room * (index + 0.5) / wanted)
        for step in range(room + 1):
            at = first + (anchor - first + step) % (room + 1)
            window = (at, at + span)
            if any(_overlap(window, (s, e)) for _, s, e in stops):
                continue
            if any(_overlap(window, other) for other in out):
                continue
            out.append(window)
            break
    return sorted(out)


def _label_of(state: int, window: tuple[int, int], equipment: str | None,
              station: str, events: list) -> tuple[str, bool, str]:
    """The reason, whether anybody scripted it, and the sentence that says so."""
    label = _STATE_LABEL.get(state)
    if label is None:  # a state word this module does not know
        return "none", False, (f"the machine reported state {state}, which is "
                               f"not one this set has a word for")
    wanted = _SCRIPTED_BY.get(label, ())
    for event in events:
        if event.type not in wanted:
            continue
        if event.start_s is None or event.end_s is None:
            continue
        if event.station is not None and event.station != station:
            continue
        if event.equipment is not None and equipment is not None \
                and event.equipment != equipment:
            continue
        if _overlap(window, (int(event.start_s), int(event.end_s))) <= 0:
            continue
        where = event.station or "the whole line"
        return label, True, (f"the run scripted `{event.type}` for {where} from "
                             f"{int(event.start_s)}s to {int(event.end_s)}s")
    if label == "micro_stop":
        # Micro-stops are pre-rolled from the run's seed inside the
        # generator rather than listed one by one, so the event that names
        # them carries no window. The state word is still the truth.
        return label, True, ("the run scripted micro-stops for this machine; "
                             "their windows are rolled from the seed")
    return label, False, ("nobody scripted this one: the line's own buffers "
                          "produced it")


def _counter_reset_in(window: tuple[int, int], station: str, events: list) -> str | None:
    """A scripted counter reset inside the window, if there is one."""
    for event in events:
        if event.type != "counter_reset" or event.station != station:
            continue
        at = event.detail.get("at")
        if at is None or not (window[0] <= int(at) < window[1]):
            continue
        return f"the run scripted `counter_reset` for {station} at {int(at)}s"
    return None


def _verdict_for(card: dict, equipment: str, window: tuple[int, int]
                 ) -> tuple[dict | None, str]:
    """What the MES's own scorecard said about this window, if anything."""
    if not card:
        return None, "no scorecard was recorded for this run"
    for kind in ("faults", "late_faults", "planned_stops", "idle_stops"):
        for index, entry in enumerate(card.get(kind) or []):
            if entry.get("equipment") not in (None, equipment):
                continue
            scripted = entry.get("window_sim_s") or []
            if len(scripted) != 2:
                continue
            if _overlap(window, (int(scripted[0]), int(scripted[1]))) <= 0:
                continue
            return (dict(entry, kind=kind),
                    f"the scorecard's {kind}[{index}] covers this window")
    return None, ("the scorecard names no scripted event in this window, which "
                  "is what it does for a stop nobody scripted")


def _cut(rows: list[dict], window: tuple[int, int]) -> list[dict]:
    return [row for row in rows if window[0] <= int(row["TSec"]) < window[1]]


def _thin(rows: list[dict], limit: int) -> tuple[list[dict], bool]:
    """Every nth row, keeping the first and the last. Says whether it thinned."""
    if limit <= 0 or len(rows) <= limit:
        return rows, False
    stride = (len(rows) + limit - 1) // limit
    kept = rows[::stride]
    if kept[-1] is not rows[-1]:
        kept.append(rows[-1])
    return kept, True


def _as_table(rows: list[dict], columns: list[str], gaps: list[tuple[int, int]]) -> str:
    """The rows as CSV text, with the seconds the MES could not see cut out.

    A gap is not written as blanks or as the last state anybody heard. Both
    of those are ways of writing down something that did not happen; a line
    saying how long the MES was blind is the only honest thing to put there.
    """
    out = [",".join(columns)]
    written_gap: set[tuple[int, int]] = set()
    for row in rows:
        second = int(row["TSec"])
        gap = next((g for g in gaps if g[0] <= second < g[1]), None)
        if gap is not None:
            if gap not in written_gap:
                written_gap.add(gap)
                out.append(f"# no rows from {gap[0]}s to {gap[1]}s: the MES had no "
                           f"connection to the machine layer and no state for it")
            continue
        out.append(",".join(str(row.get(column, "")) for column in columns))
    return "\n".join(out)


def _running_view(rows: list[dict], view: str) -> tuple[list[dict], list[str]]:
    """The rows and their columns, in the view asked for."""
    if not rows:
        return [], []
    columns = list(rows[0].keys())
    if view == "full":
        return rows, columns
    at = columns.index("State")
    columns = [*columns[:at], "Running", *columns[at + 1:]]
    out = []
    for row in rows:
        copied = dict(row)
        copied["Running"] = 1 if int(copied.pop("State")) == generate.RUNNING else 0
        out.append({column: copied[column] for column in columns})
    return out, columns


def _state_text(*, plant: str, experiment: str, equipment: str, station: str,
                position: int, of: int, window: tuple[int, int],
                stopped: tuple[int, int] | None, view: str,
                rows: list[dict], neighbours: list[tuple[str, str, list[dict]]],
                gaps: list[tuple[int, int]], maximum_rows: int,
                neighbour_rows: int) -> tuple[str, int, bool]:
    """The window, as the text a question is asked over."""
    cut = _cut(rows, window)
    viewed, columns = _running_view(cut, view)
    thinned_rows, thinned = _thin(viewed, maximum_rows)

    said = [
        f"A window from one hour of the {plant} plant, recorded by the "
        f"`{experiment}` experiment of this MES's simulator.",
        f"The machine is {equipment} ({station}), number {position} of {of} on "
        f"its line, counted from the start of the line.",
        f"The window is line seconds {window[0]} to {window[1]}; one row per "
        f"line second.",
    ]
    if stopped is not None:
        said.append(f"{equipment} was not running from {stopped[0]}s to "
                    f"{stopped[1]}s ({stopped[1] - stopped[0]} seconds).")
    else:
        said.append(f"{equipment} was running for the whole window.")
    if view == "full":
        said.append("`State` is the machine's raw state word: 0 stopped, "
                    "1 running, 2 starved, 3 blocked, 4 down, 5 changeover.")
    else:
        said.append("`Running` is 1 while the machine was making something and "
                    "0 while it was not. This machine layer does not publish a "
                    "reason; the other tags are as the machine sent them.")
    if thinned:
        said.append(f"The rows below are thinned to about {maximum_rows}: every "
                    f"nth second, with the first and the last kept.")
    if gaps:
        said.append(f"{len(gaps)} gap(s) in total where the MES had no "
                    f"connection and therefore no state at all.")

    blocks = [f"{equipment} - the machine this window is about\n"
              + _as_table(thinned_rows, columns, gaps)]
    for role, name, neighbour_all in neighbours:
        neighbour_cut = _cut(neighbour_all, window)
        neighbour_viewed, _ = _running_view(neighbour_cut, view)
        kept, _ = _thin(neighbour_viewed, neighbour_rows)
        wanted = [column for column in NEIGHBOUR_COLUMNS
                  if column in (kept[0].keys() if kept else ())]
        blocks.append(f"{name} - {role}\n" + _as_table(kept, wanted, gaps))

    text = "\n".join(said) + "\n\n" + "\n\n".join(blocks) + "\n"
    return text, len(thinned_rows), thinned


def build(results: Path | str, *, window_seconds: int = DEFAULT_WINDOW_SECONDS,
          minimum_seconds: int = DEFAULT_MINIMUM_SECONDS,
          maximum_rows: int = DEFAULT_MAXIMUM_ROWS,
          neighbour_rows: int = DEFAULT_NEIGHBOUR_ROWS,
          controls: int = DEFAULT_CONTROLS_PER_MACHINE,
          state_view: str = STATE_VIEWS[0]) -> tuple[list[Stop], _Run]:
    """One results directory, as labelled stops. Reads; runs nothing."""
    if state_view not in STATE_VIEWS:
        raise ValueError(f"unknown state view {state_view!r}; "
                         f"one of {', '.join(STATE_VIEWS)}")
    results = Path(results)
    scores = _read_json(results / "scores.json") if (results / "scores.json").is_file() else {}
    run = _Run(name=results.name, experiment=str(scores.get("experiment") or results.name))

    stops: list[Stop] = []
    for plant in plants_in(results):
        line_json, tag_map = _line_json(results, plant), _tag_map(results, plant)
        if not line_json.is_file() or not tag_map.is_file():
            run.notes.append(f"{plant}: no line description or tag map recorded, "
                             f"so no stop in it can be labelled; skipped")
            continue
        run.plants.append(plant)
        scripted = truth.load_truth(line_json, tag_map)
        events = scripted["events"]
        gaps = _disconnect_windows(events)
        mapping = truth.station_to_equipment(tag_map)
        card_path = results / "recorded" / f"{plant}-scorecard.json"
        card = _read_json(card_path) if card_path.is_file() else {}
        lines = _station_order(_read_json(line_json))

        for line_stations in lines:
            rows_by_station = {}
            for station in line_stations:
                path = results / "replay" / plant / f"{station}.csv"
                if path.is_file():
                    rows_by_station[station] = _read_rows(path)
            for position, station in enumerate(line_stations, start=1):
                rows = rows_by_station.get(station)
                if not rows or "State" not in rows[0]:
                    continue
                equipment = mapping.get(station) or station
                neighbours = []
                if position >= 2 and line_stations[position - 2] in rows_by_station:
                    before = line_stations[position - 2]
                    neighbours.append(("the machine before it on the line",
                                       mapping.get(before) or before,
                                       rows_by_station[before]))
                if position < len(line_stations) and line_stations[position] in rows_by_station:
                    after = line_stations[position]
                    neighbours.append(("the machine after it on the line",
                                       mapping.get(after) or after,
                                       rows_by_station[after]))

                found = _stops_in(rows, minimum_seconds=minimum_seconds)
                windows: list[tuple[tuple[int, int], tuple[int, int] | None, int]] = [
                    ((start, end), (start, end), state) for state, start, end in found]
                windows.extend((window, None, generate.RUNNING) for window in
                               _controls_in(rows, found, window_seconds=window_seconds,
                                            wanted=controls))

                for (start, end), stopped, state in windows:
                    window = (max(int(rows[0]["TSec"]), start - window_seconds),
                              min(int(rows[-1]["TSec"]) + 1, end + window_seconds))
                    if stopped is None:
                        label, was_scripted, says = "none", False, (
                            "the machine ran through this window; it is here as a "
                            "control, so the set can show a reason being invented")
                        reset = _counter_reset_in(window, station, events)
                        if reset is not None:
                            label, was_scripted, says = "counter_reset", True, reset
                    else:
                        label, was_scripted, says = _label_of(
                            state, (start, end), equipment, station, events)
                    unseen = sum(_overlap((start, end), gap) for gap in gaps)
                    verdict, verdict_says = _verdict_for(card, equipment, (start, end))
                    text, kept, thinned = _state_text(
                        plant=plant, experiment=run.experiment, equipment=equipment,
                        station=station, position=position, of=len(line_stations),
                        window=window, stopped=stopped, view=state_view, rows=rows,
                        neighbours=neighbours, gaps=gaps, maximum_rows=maximum_rows,
                        neighbour_rows=neighbour_rows)
                    stops.append(Stop(
                        results=run.name, experiment=run.experiment, plant=plant,
                        station=station, equipment=equipment, start_s=start, end_s=end,
                        label=label, label_says=says, scripted=was_scripted,
                        observable=unseen < (end - start),
                        unseen_seconds=unseen, state=text, state_view=state_view,
                        state_rows=kept, thinned=thinned, mes_verdict=verdict,
                        mes_verdict_says=verdict_says))
    stops.sort(key=lambda stop: (stop.plant, stop.equipment, stop.start_s))
    return stops, run


def build_many(directories: list[Path | str], **options) -> dict:
    """Several results directories, as one set, with its totals stated."""
    records: list[dict] = []
    runs: list[dict] = []
    for directory in directories:
        stops, run = build(directory, **options)
        records.extend(stop.as_record() for stop in stops)
        runs.append({"results": run.name, "experiment": run.experiment,
                     "plants": run.plants, "plants_total": len(run.plants),
                     "stops": sum(1 for stop in stops if stop.label != "none"),
                     "controls": sum(1 for stop in stops if stop.label == "none"),
                     "notes": run.notes})
    return {"_what": ("One window per stop from the simulator's scripted hours, "
                      "with the reason the generator actually gave it. Built by "
                      "`fsmes jev labelled-set`; nothing here was asked of any "
                      "model."),
            "vocabulary": [list(reason) for reason in REASONS],
            "settings": {key: options.get(key) for key in sorted(options)},
            "runs": runs,
            "runs_total": len(runs),
            "totals": totals_of(records),
            "records": records}


def totals_of(records: list[dict]) -> dict:
    """Every count this set can state about itself, including the empty ones.

    Every label in the vocabulary appears, whether or not anything carries
    it: a label with no examples is a thing this set cannot measure, and it
    can only say so if it is printed as zero rather than left out.
    """
    by_label = {name: 0 for name in REASON_NAMES}
    for record in records:
        by_label[record["label"]] = by_label.get(record["label"], 0) + 1
    sizes = sorted(record["state_characters"] for record in records)
    return {
        "records": len(records),
        "by_label": by_label,
        "scripted": sum(1 for record in records if record["scripted"]),
        "not_scripted": sum(1 for record in records if not record["scripted"]),
        "observable": sum(1 for record in records if record["observable"]),
        "unobservable": sum(1 for record in records if not record["observable"]),
        "with_a_recorded_mes_verdict": sum(
            1 for record in records if record["mes_verdict"] is not None),
        "state_characters": {
            "smallest": sizes[0] if sizes else None,
            "median": sizes[len(sizes) // 2] if sizes else None,
            "largest": sizes[-1] if sizes else None,
            "total": sum(sizes),
        },
    }
