"""What the plant actually did, read from the line description.

`line.json` is not documentation - it is the input the generator obeyed, so
its events are ground truth by construction. Each event carries a window in
*simulated* seconds; converting that to wall-clock is the scorer's job,
because only the scorer knows the replay speed and when the run started.

A factory description (`{"lines": [...]}`, see `fsmes.sim.generate`) is
flattened the same way `generate_factory` merges it for replay: each line's
stations and events are namespaced `<line>_<name>` before being read, so the
truth lines up with the manifest and tag map the factory's CSVs actually
produced. Reuses `generate._prefix_line` rather than re-implementing the
namespacing rule a second time, which is exactly the kind of drift that bit
the old hand-transcribed tag maps.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ScriptedEvent:
    """One thing the generator was told to do to the line."""

    type: str                     # changeover | down | drift | micro_stops
    start_s: float | None         # simulated seconds from the start of the hour
    end_s: float | None
    station: str | None = None    # generator's name for it, e.g. "Mill"
    equipment: str | None = None  # the MES's code for it, e.g. "MILL01"
    detail: dict = field(default_factory=dict)

    @property
    def planned(self) -> bool:
        """A changeover is a *planned* stop and must never count as downtime.

        This is the single mapping decision with real consequences: calling a
        planned stop downtime silently destroys every availability figure the
        plant reports.
        """
        return self.type == "changeover"

    @property
    def is_fault(self) -> bool:
        return self.type == "down"


def _station_to_equipment(tag_map_path: Path) -> dict[str, str]:
    """The generator names stations ("Mill"); the MES codes them ("MILL01").

    The tag map already holds both, so the mapping is read rather than
    guessed - a guessed one would silently score the wrong machine.
    """
    data = json.loads(tag_map_path.read_text(encoding="utf-8"))
    return {m["object"]: m["equipment"] for m in data.get("machines", [])}


def _scripted_event(raw: dict, mapping: dict[str, str]) -> ScriptedEvent:
    """One `events[]` entry, already station-namespaced if it came from a
    factory line, turned into ground truth."""
    station = raw.get("station")
    return ScriptedEvent(
        type=raw["type"],
        start_s=raw.get("start"),
        end_s=raw.get("end"),
        station=station,
        # A line-wide event (a changeover) has no station and maps to
        # no single machine; that is meaningful, not missing data. In a
        # factory, "line-wide" still means "no single machine" - see the
        # note on multi-line changeovers below.
        equipment=mapping.get(station) if station else None,
        detail={k: v for k, v in raw.items()
                if k not in {"type", "start", "end", "station"}
                and not k.startswith("_")},
    )


def load_truth(line_json: Path, tag_map: Path) -> dict:
    """Parse a line - or factory - description into the timeline the MES
    should have seen.

    **Known limitation for factories, not fixed here:** a changeover event
    with no `station` scores as "every machine in the timeline must avoid
    calling this downtime" (`fsmes.sim.score`), which is correct for a single
    line but too broad for a factory line whose changeover should only bind
    that line's own stations. Workable today by giving every line in a
    factory the same changeover window (a synchronized, plant-wide break) or
    by avoiding changeover events in per-line scripts; scoping a changeover
    to the line that scripted it is real work, tracked as a Phase 3 finding
    rather than solved here.
    """
    line = json.loads(Path(line_json).read_text(encoding="utf-8"))
    mapping = _station_to_equipment(Path(tag_map))

    if "lines" in line:
        from fsmes.sim.generate import _prefix_line

        stations: list[str] = []
        events: list[ScriptedEvent] = []
        durations: set = set()
        seeds: list = []
        for entry in line["lines"]:
            prefixed = _prefix_line(entry["name"], entry)
            durations.add(prefixed.get("duration_s"))
            seeds.append(prefixed.get("seed"))
            stations.extend(s["name"] for s in prefixed.get("stations", []))
            events.extend(_scripted_event(raw, mapping) for raw in prefixed.get("events", []))
        if len(durations) > 1:
            raise ValueError(
                f"{line_json}: lines disagree on duration_s ({sorted(durations)}); "
                "a factory run needs one duration for every line."
            )
        return {
            "channel": line.get("channel"),
            "duration_s": durations.pop() if durations else None,
            "seed": seeds,
            "stations": stations,
            "equipment": [mapping.get(s) for s in stations],
            "events": events,
        }

    return {
        "channel": line.get("channel"),
        "duration_s": line.get("duration_s"),
        "seed": line.get("seed"),
        "stations": [s["name"] for s in line.get("stations", [])],
        "equipment": [mapping.get(s["name"]) for s in line.get("stations", [])],
        "events": [_scripted_event(raw, mapping) for raw in line.get("events", [])],
    }
