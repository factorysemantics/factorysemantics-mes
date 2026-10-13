"""The plant model, written out as a pack anybody can read before running it.

`to_pack` is the only way out of the model. It writes a format-1 pack - the
same format `docs/operate/packs.md` describes and the lab plants use, so a
generated plant and a hand-written one are the same kind of thing and are run
by the same commands:

    plant.toml          who this plant is, and what the shape could not say
    line.json           the line the generator replays
    tag_map.json        which tag is which, and how they are organised
    floor.json          how a number on a tag becomes a record somebody made
    masterdata/*.json   the equipment tree, the routings, the book, the words

Every number written here is arithmetic on a number the shape carried. Where
the model had to state something instead, `plant.toml` says so in
`[from_shape]` - `notes` for what was assumed, `dropped` for what the shape
held and the plant does not use. `fsmes shape plant` prints both.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from ..pack.check import protected_terms
from .model import Plant, Station

#: What the generator's State column means. The same mapping every pack uses:
#: starved and blocked are IDLE (the machine is healthy, it has nothing to do),
#: changeover is SETUP (planned, and must not count against availability), and
#: only a genuine fault is DOWN.
STATE_MAP = {"0": "idle", "1": "running", "2": "idle", "3": "idle", "4": "down", "5": "setup"}

#: Seconds an emergent starve or block must hold before the machine publishes
#: it. The same twenty the reference shift uses, and for the same reason: a
#: drive waiting a moment for the conveyor ahead has not stopped.
STATE_FILTER_S = 20

#: Line seconds between one counted stretch and the next, and between samples.
#: A quarter of an hour, which is what a plant with one inspector does.
EVERY_LINE_S = 900


def _json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _analogs(station: Station) -> list[dict]:
    """The tags the generator can replay: the analogs, and only those.

    A counter and a discrete are listed in the plant's `dropped` with a line
    each saying why - every station already publishes its own counters, and a
    boolean described by a mean is not a signal.
    """
    out = []
    for signal in station.signals:
        if signal.kind != "analog":
            continue
        entry: dict = {
            "name": signal.name,
            "base": round(signal.base, signal.decimals),
            "noise": round(signal.noise, 4),
            "decimals": signal.decimals,
        }
        if signal.unit:
            entry["unit"] = signal.unit
        if signal.running_only:
            entry["running_only"] = True
        out.append(entry)
    if not out:
        # A station the shape found no analog tag on still has to publish
        # something for the replay to have a column: its own cycle time,
        # which the generator writes anyway.
        out.append({"name": "CycleTime", "base": round(station.cycle_seconds * 1000.0, 1),
                    "noise": 0.0, "decimals": 1, "unit": "ms",
                    "_why": "The shape found no analog tag on this machine. This is its "
                            "own cycle time, so the station has a column; nothing here "
                            "was measured on the source system."})
    return out


def _events(plant: Plant) -> tuple[list[dict], list[str]]:
    """The stop script, laid out from the state mix the shape measured.

    The shape says what share of a machine's time went to each state and how
    long the median one lasted. That is enough to place stops without
    inventing any: share x duration is how many seconds of that state the
    shift should hold, the median length says how many stops that is, and they
    are spread evenly with a per-station offset so the whole line does not
    stop at once.

    `starved` and `blocked` are deliberately not scripted. They emerge from
    the buffer the person was asked for; scripting them as well would book the
    same loss twice.

    Returns the events and, beside them, one plain line per group saying where
    its numbers came from. The lines go next to the events in `line.json`
    rather than inside them: the generator reads the list, and a note is not
    an event.
    """
    by_state = {s.state: s for s in plant.states}
    events: list[dict] = []
    notes: list[str] = []
    chain = plant.chain()
    duration = plant.duration_s

    changeover = by_state.get("changeover")
    if changeover and changeover.share > 0:
        each = max(int(changeover.p50_seconds or 240), 30)
        count = max(round(changeover.share * duration / each), 1)
        # Spread through the shift, never in the first or last stretch: a
        # changeover at second zero has no run before it to change over from.
        step = duration // (count + 1)
        for i in range(1, count + 1):
            start = step * i
            events.append({"type": "changeover", "start": start, "end": start + each,
                           "_why": f"One of {count} changeovers. The shape says "
                                   f"{changeover.share:.1%} of machine time was changeover, "
                                   f"with a median of {changeover.p50_seconds:g} s."})

    down = by_state.get("down")
    if down and down.share > 0:
        each = max(int(down.p50_seconds or 60), 10)
        per_station = max(round(down.share * duration / each), 1)
        step = duration // (per_station + 1)
        for index, name in enumerate(chain):
            for i in range(1, per_station + 1):
                start = step * i + index * 137       # a different phase per machine
                if start + each >= duration:
                    continue
                events.append({"type": "down", "station": name,
                               "start": start, "end": start + each})
        notes.append(f"{per_station} breakdowns per machine, {each} s each: the shape "
                     f"says {down.share:.1%} of machine time was down, with a median of "
                     f"{down.p50_seconds:g} s. The phase is shifted 137 s per machine so "
                     "the line does not fault in unison.")

    stopped = by_state.get("stopped")
    if stopped and stopped.share > 0:
        low = max(int(stopped.p50_seconds * 0.7) or 4, 2)
        high = max(int(stopped.max_seconds or stopped.p50_seconds * 1.5), low + 1)
        middle = (low + high) / 2.0
        every = max(int(middle / stopped.share), 30)
        for name in chain:
            events.append({"type": "micro_stops", "station": name,
                           "every_s": [int(every * 0.8), int(every * 1.2)],
                           "duration_s": [low, high]})
        notes.append(f"Micro stops on every machine, {low}-{high} s, one every "
                     f"{int(every * 0.8)}-{int(every * 1.2)} s: the shape says "
                     f"{stopped.share:.1%} of machine time was a short stop with a median "
                     f"of {stopped.p50_seconds:g} s.")

    notes.append("Starved and blocked are not scripted: they emerge from the buffer size "
                 "the person was asked for, and scripting them too would book the same "
                 "loss twice. No fault, drift or scrap burst is scripted at all.")
    return events, notes


def _line_json(plant: Plant) -> dict:
    chain = plant.chain()
    stations = [plant.station(name) for name in chain]
    events, why = _events(plant)
    pretty = plant.name.replace("-", " ").replace("_", " ").title().replace(" ", "")
    return {
        "_what": f"{plant.label}. Built by `fsmes shape plant` from "
                 f"{plant.source.get('shape_file', 'a shape file')}, shape format "
                 f"{plant.shape_format}. Every figure here is arithmetic on a figure that "
                 "shape carried; nothing was read from a database and nothing left the "
                 "building to make it.",
        "_states": "State column values: 0 stopped, 1 running, 2 starved, 3 blocked, "
                   "4 down, 5 changeover.",
        "_flow": "The stations below are in the order the simulated line works them. "
                 + ("One straight line." if plant.is_one_straight_line else
                    "This plant is not one straight line: see [from_shape] in plant.toml "
                    "and routings.json in masterdata for its real shape."),
        "channel": f"{pretty}Shape",
        "dsn": f"{pretty}ShapeCSV",
        "seed": plant.seed,
        "duration_s": plant.duration_s,
        "state_filter_s": STATE_FILTER_S,
        "orders": [int(order.code.rsplit("-", 1)[-1]) for order in plant.orders],
        "buffers": {"capacity": plant.buffer, "initial": max(plant.buffer // 2, 1)},
        "_buffers": f"{plant.buffer} pieces between each pair of machines, half full at the "
                    "start. The one number nobody's MES records and the one this converter "
                    "asks for.",
        "stations": [
            {
                "name": station.name,
                "rate_per_min": round(station.rate_per_min, 2),
                "scrap_pct": round(station.scrap_pct, 3),
                "analogs": _analogs(station),
            }
            for station in stations if station is not None
        ],
        "_events": why,
        "events": events,
    }


def _tag_map(plant: Plant) -> dict:
    machines = []
    for name in plant.chain():
        station = plant.station(name)
        if station is None:
            continue
        analog = station.analog
        entry: dict = {
            "equipment": station.name,
            "object": station.name,
            "cycle_seconds": station.cycle_seconds,
            "analog": analog.name if analog else None,
            "order_tag": None,
            "state_map": dict(STATE_MAP),
        }
        if station.node:
            entry["group"] = list(station.node)
        machines.append(entry)
    grouped = [node for node in plant.nodes if node.path]
    return {
        "_comment": f"{plant.label}, served by this product's own OPC simulator from the "
                    "replay beside it. Objects and tag names match the CSV tables exactly. "
                    "Every machine is read-only: `order_tag` is null because a CSV replay "
                    "cannot be written to, and orders still flow because counter deltas "
                    "auto-start a released operation.",
        "_group": "`group` is the folders above a machine's own object in the address "
                  "space, outermost first. A machine with no `group` sits directly under "
                  "Objects, which is what one node per machine looks like and what every "
                  "pack written before grouped nodes says. "
                  + (f"This plant's shape measured {len(grouped)} grouped node(s): several "
                     "machines under one node, the way a Kepware channel or an Ignition "
                     "device folder holds a PLC's worth of machines."
                     if grouped else
                     "This plant's shape says one node per machine."),
        "_state_map": "The State column is a PLC-style integer. Mapping it is a site "
                      "decision: starved and blocked are IDLE, changeover is SETUP, and "
                      "only a genuine fault is DOWN.",
        "line": {
            "object": "Line",
            "publishes_order": "OrderId",
            "order_code": plant.orders[0].code.rsplit("-", 1)[0] + "-{value}"
            if plant.orders else "WO-{value}",
        },
        "machines": machines,
    }


def _floor_json(plant: Plant) -> dict:
    """How this plant's floor turns tags into records.

    Only the parts the shape can pay for. There is no `sampling` block,
    because a sample of five pieces needs this plant's own arithmetic for
    turning one tag into another characteristic and no shape file carries one;
    and there is no `maintenance` block, because the shape carries no
    maintenance table at all. Both are said out loud in plant.toml.
    """
    station = plant.station(plant.chain()[0])
    analog = station.analog if station else None
    interval = max((s.sample_interval_s for st in plant.stations for s in st.signals), default=0.0)
    characteristics: dict = {}
    if analog is not None:
        characteristics[analog.name] = {
            "spread": round(max(analog.noise / 3.0, 10.0 ** -analog.decimals), analog.decimals + 2),
            "_spread": "How much two readings of the same piece disagree on the gauge. A "
                       "third of the tag's own noise: the shape measures the tag, and the "
                       "instrument is the smaller part of what it measured.",
            "gauges": [{"gauge": "GA_01", "share": 1}],
        }
    defects = [
        {"code": d.code, "weight": max(round(d.share * 100), 1)}
        for d in plant.dispositions
    ] or [{"code": "sent_back", "weight": 1}]
    taken = protected_terms()
    mix = [
        {"reason": kind.code if kind.code not in taken else f"{kind.code}_stop",
         "share": round(kind.share_of_stops, 4)}
        for kind in plant.stops if kind.share_of_stops > 0
    ]
    floor: dict = {
        "_what": "How a number on a tag becomes a measurement, a count or a named stop on "
                 f"{plant.label}. Written by `fsmes shape plant`.",
        "planning": {"keep_planned": 3,
                     "_keep_planned": "Three planned orders behind whatever is on the line, "
                                      "so the book does not empty while somebody is looking "
                                      "at it. Copied from this plant's own book in rotation."},
        "measurement": {
            "stale_after_s": max(int(interval * 6) or 60, 60),
            "_stale_after_s": f"A process value older than this is not a reading. Six of "
                              f"this plant's own sample intervals ({interval:g} s as the "
                              "shape measured them), with sixty seconds as the floor.",
            "default_spread": 0.0,
            "characteristics": characteristics,
        },
        "inspection": {
            "sent_back": {
                "every_line_s": EVERY_LINE_S,
                "at": {"equipment": plant.chain()[-1]},
                "_at": "The last machine in the flow: what it put through and what it sent "
                       "back, differenced off its own counters. Nothing here invents "
                       "production.",
                "lot_ref": "{tail}-Q{n:02d}",
                "defects": defects,
                "_defects": "The bins rejects are sorted into, with how common each is. "
                            "These are the dispositions the source MES recorded, in the "
                            "proportions it recorded them - this plant's own defect words "
                            "are not in a shape file and are not invented here.",
            }
        },
        "nonconformances": {
            "per_pass": 6,
            "leave_open": 3,
            "close_after_disposition": True,
            "by_severity": {
                "major": {"disposition": (plant.dispositions[0].code
                                          if plant.dispositions else "scrap"),
                          "reason": "Out of specification badly enough that somebody looked "
                                    "at it now. The disposition is the one the source "
                                    "system used most often."},
                "minor": {"disposition": (plant.dispositions[-1].code
                                          if plant.dispositions else "rework"),
                          "reason": "Out of specification and worked through in the normal "
                                    "course of the shift."},
            },
            "otherwise": {"disposition": "use_as_is",
                          "reason": "Released as is on the shift supervisor's name."},
        },
        "stops": {
            "micro_stop_under_s": 60,
            "micro_stop": "micro_stop" if any(k.code == "micro_stop" for k in plant.stops)
            else (mix[0]["reason"] if mix else "unexplained"),
            "longer_than_a_micro_stop": mix[0]["reason"] if mix else "unexplained",
            "changeover": "changeover" if any(k.code == "changeover" for k in plant.stops)
            else (mix[0]["reason"] if mix else "unexplained"),
            "mix": mix,
            "_mix": "The reason categories this plant's operators pick from, in the shares "
                    "the source system recorded them in. The floor works through them in "
                    "turn, so a shift's stops come out in these proportions.",
            "leave_unlabelled": round(plant.unlabelled_share, 4),
            "_leave_unlabelled": f"{plant.unlabelled_share:.1%} of stops are left with "
                                 "nobody having said why, because that is the share the "
                                 "source system had no reason recorded against. The screens "
                                 "say so under each one.",
        },
        "calibration": {"when": "overdue"},
    }
    return floor


def _masterdata(plant: Plant) -> dict[str, list[dict]]:
    equipment: list[dict] = [
        {"code": plant.enterprise, "name": plant.enterprise, "level": "enterprise"},
        {"code": plant.site, "name": plant.site, "level": "site", "parent": plant.enterprise},
    ]
    for area in plant.areas:
        equipment.append({"code": area.code, "name": area.name, "level": "area",
                          "parent": area.parent or plant.site})
    cells = {(s.area, s.cell) for s in plant.stations if s.cell}
    for area_code, cell in sorted(cells):
        equipment.append({"code": cell, "name": f"Cell {cell}", "level": "work_center",
                          "parent": area_code})
    for station in plant.stations:
        equipment.append({
            "code": station.name,
            "name": f"Machine {station.name}",
            "level": "work_unit",
            "parent": station.cell or station.area,
            "_rate": f"{station.rate_per_min:g} a minute and {station.scrap_pct:g}% scrap, "
                     "as the shape measured this machine. The rated cycle time is not "
                     "repeated here: it is read from the tag map, so OEE performance is "
                     "computed against the line that generated the data.",
        })

    materials = [
        {"code": routing.material, "name": f"Product {routing.material}", "unit": "ea",
         "type": "finished"}
        for routing in plant.routings
    ]
    routings = [
        {"code": routing.code, "name": routing.name, "material": routing.material,
         "operations": [{"seq": (i + 1) * 10, "name": f"Operation {i + 1}", "equipment": step}
                        for i, step in enumerate(routing.steps)]}
        for routing in plant.routings
    ]
    work_orders = [
        {"code": order.code, "material": order.material, "quantity": order.quantity,
         "priority": order.priority, "release": order.release,
         "due_in_hours": order.due_in_hours}
        for order in plant.orders
    ]
    shifts = [{"code": s.code, "name": s.name, "starts": s.starts, "ends": s.ends,
               "days": "1111111"} for s in plant.shifts]
    # A reason code may not be one of the product's own words: two plants whose
    # `quality` means different things are two plants a fleet view is comparing
    # as though they meant the same (decision 0022 clause 3, and `fsmes pack
    # check` refuses it). The shape's stop categories are generic words and
    # some of them collide, so a colliding one keeps its name on the screen and
    # takes `_stop` on its code.
    taken = protected_terms()
    downtime_reasons = [
        {"code": kind.code if kind.code not in taken else f"{kind.code}_stop",
         "name": kind.name, "description": kind.description}
        for kind in plant.stops
    ]
    if not any(r["code"] == "micro_stop" for r in downtime_reasons):
        downtime_reasons.append({
            "code": "micro_stop", "name": "Micro stop",
            "description": "A stop short enough that whoever was standing there cleared it "
                           "and carried on. The shape measured these as a share of machine "
                           "time; the category is this product's own word for them."})
    severities = [
        {"code": "major", "name": "Major",
         "description": "Out of specification badly enough that somebody should look now. "
                        "This is what a point beyond three sigma opens."},
        {"code": "minor", "name": "Minor",
         "description": "Out of specification and workable through the shift. This is what "
                        "a recorded check outside its limits, or any other control rule, "
                        "opens."},
    ]

    station = plant.station(plant.chain()[0])
    analog = station.analog if station else None
    specs: list[dict] = []
    gauges: list[dict] = []
    if analog is not None:
        half = plant.spec_sigma * analog.noise
        specs.append({
            "material": plant.routings[0].material,
            "characteristic": analog.name,
            "unit": analog.unit,
            "min": round(analog.base - half, analog.decimals),
            "max": round(analog.base + half, analog.decimals),
            "_band": f"{plant.spec_sigma} sigma either side of the tag's own base. See "
                     "[from_shape] in plant.toml for where that number came from.",
        })
        gauges.append({
            "code": "GA_01", "name": f"Gauge at {station.name}", "kind": "gauge",
            "location": station.name, "resolution": 10.0 ** -analog.decimals,
            "interval_days": 90, "warn_days": 14, "calibrated_days_ago": 7,
        })
    specs.append({
        "material": plant.routings[0].material,
        "characteristic": "sent_back",
        "counted": True,
        "_counted": "A count of pieces sent back out of pieces looked at, not a "
                    "measurement: its chart is a p chart. The shape says what share of "
                    "checks failed and what the source system did with them, which is "
                    "exactly a count and a disposition.",
    })

    return {
        "equipment": equipment,
        "materials": materials,
        "routings": routings,
        "work_orders": work_orders,
        "shifts": shifts,
        "downtime_reasons": downtime_reasons,
        "nc_severities": severities,
        "quality_specs": specs,
        "gauges": gauges,
    }


def _plant_toml(plant: Plant, *, requires: str) -> str:
    def q(text: str) -> str:
        return json.dumps(str(text))

    lines = [
        f"# {plant.label}",
        "#",
        "# Written by `fsmes shape plant` from "
        f"{plant.source.get('shape_file', 'a shape file')} (shape format "
        f"{plant.shape_format}).",
        "#",
        "# Nothing in this pack was read from a database. The shape file was the only",
        "# input: counts, shares, distributions and patterns, with no order number, lot,",
        "# serial, person, customer or free-text note in it. What the shape could not say",
        "# is in [from_shape] below - `asked` for the one number a person was asked for,",
        "# `notes` for what had to be stated, and `dropped` for what the shape carried",
        "# that this plant does not use.",
        "",
        "[pack]",
        "format = 1",
        f"requires = {q(requires)}",
        "",
        "[plant]",
        f"name       = {q(plant.name)}",
        f"label      = {q(plant.label)}",
        f"timezone   = {q(plant.timezone)}",
        'profile    = "laptop"',
        f"enterprise = {q(plant.enterprise)}",
        f"site       = {q(plant.site)}",
        "",
        "[serve]",
        'api_host     = "127.0.0.1"',
        "api_port     = 8010",
        f'opc_endpoint = "opc.tcp://127.0.0.1:4841/fsmes/{plant.name}"',
        "simulate     = true",
        "",
        "[files]",
        'tag_map    = "tag_map.json"',
        'replay_dir = "out"',
        'masterdata = "masterdata"',
        'floor      = "floor.json"',
        "",
        "[oee]",
        "coverage_floor = 0.8",
        "",
        "[erp]",
        'mode = "off"',
        "",
        "[quality]",
        "hold_rules = [1, 2, 3, 4, 5]",
        "major_rules = [1, 5]",
        "",
        "[from_shape]",
        f"shape_file   = {q(plant.source.get('shape_file', ''))}",
        f"shape_format = {plant.shape_format}",
        f"written_by   = {q(plant.source.get('written_by', 'not stated'))}",
        f"source_dialect = {q(plant.source.get('source_dialect', 'not stated'))}",
        f"window_hours = {plant.source.get('window_hours', 0)}",
        f"leak_check   = {q(plant.source.get('leak_check', 'not stated'))}",
        f"stations     = {len(plant.stations)}",
        f"areas        = {len(plant.areas)}",
        f"routings     = {len(plant.routings)}",
        f"one_straight_line = {str(plant.is_one_straight_line).lower()}",
        f"grouped_tag_nodes = {len([n for n in plant.nodes if n.path])}",
        "",
        "# The one number nobody's MES records, so the one a person is asked for.",
        "asked = [",
        f"  {{ what = \"buffer between each pair of machines\", pieces = {plant.buffer} }},",
        "]",
        "",
        "# What the shape could not say, and what this plant states instead.",
        "notes = [",
    ]
    lines += [f"  {q(note)}," for note in plant.notes]
    lines += [
        "]",
        "",
        "# What the shape carried that this plant does not use, and why. One line each.",
        "dropped = [",
    ]
    for label, why in plant.dropped:
        lines.append(f"  {{ field = {q(label)}, why = {q(why)} }},")
    lines += ["]", ""]
    return "\n".join(lines)


def requires_for(version: str) -> str:
    """What a pack built by this release should say it needs.

    `[pack] requires` is a comparison, not a version - `fsmes pack check`
    refuses a bare `0.3.0` - and the honest comparison for a pack this
    converter wrote is "at least the release that wrote it": every figure in
    it is written by code of this version, and an older fsmes has never seen
    the `[from_shape]` section.
    """
    return f">={version}"


def to_pack(
    plant: Plant,
    out: str | Path,
    *,
    requires: str | None = None,
    echo: Callable[[str], None] = print,
) -> Path:
    """Write one plant as a pack folder. Returns the folder."""
    from fsmes import __version__

    requires = requires or requires_for(__version__)
    out = Path(out)
    (out / "masterdata").mkdir(parents=True, exist_ok=True)
    (out / "plant.toml").write_text(_plant_toml(plant, requires=requires), encoding="utf-8")
    _json(out / "line.json", _line_json(plant))
    _json(out / "tag_map.json", _tag_map(plant))
    _json(out / "floor.json", _floor_json(plant))
    for kind, rows in _masterdata(plant).items():
        _json(out / "masterdata" / f"{kind}.json", rows)
    echo(f"wrote {out}")
    return out
