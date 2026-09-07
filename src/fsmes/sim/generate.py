"""Generate a production line as CSVs from one JSON description.

The line is data, not code: stations, rates, buffers and the whole script of
things going wrong live in a config file, and this module never changes.
Editing that config toward a different plant is the entire customization
story - which is what lets a new plant arrive without touching src/.

Vendored from LineSim, the standalone work tool, and extended here with
multiple analogs per station. LineSim stays stdlib-only and separate because
it has to copy onto a work machine as a folder; this copy is part of the
product, because principle 6 says the simulator is a component and not test
scaffolding.

Deterministic: the same seed gives byte-identical output.

A config also has a **factory form**: a top-level `"lines": [...]` array
instead of a top-level `"stations"` list. Each entry is an ordinary
single-line config (same `stations`/`buffers`/`events`/`orders`/
`duration_s`/`seed` fields) plus a required `"name"`. Station names and any
station reference inside `events`/`effects` are namespaced
`"{line_name}_{station_name}"` before validation, so every line still
enforces the single-line rules (unique names, no bare `"Line"`, effects
naming a real analog) with no cross-line collisions possible - lines are
simulated independently by the same `simulate()` used for one line, and only
merged at the output stage. A plant is never told "multi-line" exists as a
concept beyond that: it just sees more stations and one manifest, which is
the same trick that let a new single-line plant arrive as data before.
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import sys
from pathlib import Path

from fsmes.kernel.tags import COUNTER_TAGS, MANIFEST_NAME

STOPPED, RUNNING, STARVED, BLOCKED, DOWN, CHANGEOVER = 0, 1, 2, 3, 4, 5
STATE_WORDS = {0: "stopped", 1: "running", 2: "starved", 3: "blocked", 4: "down", 5: "changeover"}


# ------------------------------------------------------------------ config
def _read_json(path: Path) -> dict:
    """Read and parse a config file, complaining usefully. Shared by the
    single-line and factory loaders so both fail the same friendly way."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"No config at {path}. Start from line.minimal.json or line.example.json.")
    except json.JSONDecodeError as exc:
        sys.exit(
            f"{path} is not valid JSON: {exc}\n"
            "(Trailing comma? Excel quote? A JSON checker will point at it.)"
        )


def _validate_line(config: dict, label: str) -> dict:
    """The single-line sanity checks, extracted so a factory's lines can
    each be run through exactly the same rules as a standalone plant."""
    stations = config.get("stations") or []
    if not stations:
        sys.exit(f"{label}: no stations. A line needs at least one.")
    names = [s.get("name", "") for s in stations]
    for name in names:
        if not name or not all(c.isalnum() or c == "_" for c in name):
            sys.exit(f"{label}: station name {name!r} — use letters/digits/underscore only "
                     f"(it becomes a Kepware device name and a file name).")
    if len(set(names)) != len(names):
        sys.exit(f"{label}: duplicate station names.")
    if "Line" in names:
        sys.exit(f"{label}: 'Line' is reserved for the line-level table.")

    duration = int(config.get("duration_s", 3600))
    for event in config.get("events", []):
        kind = event.get("type")
        if kind not in ("down", "drift", "scrap_burst", "changeover",
                        "counter_reset", "micro_stops"):
            sys.exit(f"{label}: unknown event type {kind!r}.")
        station = event.get("station")
        if kind != "changeover" and station not in names:
            sys.exit(f"{label}: event {kind!r} names station {station!r}, which is not in stations.")
        for key in ("start", "end", "at"):
            if key in event and not 0 <= int(event[key]) <= duration:
                sys.exit(f"{label}: event {kind!r} {key}={event[key]} is outside 0..{duration}.")
        # An effect moves an analog on *another* station for the event's
        # window - the physics between machines that a real line has and a
        # per-station script cannot express. Each must name a real analog.
        for effect in event.get("effects", []):
            target = effect.get("station")
            if target not in names:
                sys.exit(f"{label}: event {kind!r} effect names station {target!r}, which is not in stations.")
            analogs = [a.get("name", "Value") for a in station_analogs(stations[names.index(target)])]
            if effect.get("analog") not in analogs:
                sys.exit(f"{label}: event {kind!r} effect names analog {effect.get('analog')!r} on {target}, "
                         f"which has {', '.join(analogs) or 'none'}.")
    return config


def load_config(path: Path) -> dict:
    """Read and sanity-check a single-line description, complaining usefully."""
    return _validate_line(_read_json(path), str(path))


def _prefix_line(line_name: str, line_cfg: dict) -> dict:
    """Namespace every station name (and every reference to one) with the
    line name, so lines simulated independently can be merged into one
    manifest with no collisions - `Mill` in `LineA` and `Mill` in `LineB`
    become `LineA_Mill` and `LineB_Mill`, two unrelated OPC objects."""
    prefixed = copy.deepcopy(line_cfg)
    prefixed.pop("name", None)
    for station in prefixed.get("stations", []):
        station["name"] = f"{line_name}_{station['name']}"
    for event in prefixed.get("events", []):
        if event.get("station"):
            event["station"] = f"{line_name}_{event['station']}"
        for effect in event.get("effects", []):
            effect["station"] = f"{line_name}_{effect['station']}"
    return prefixed


def load_factory_config(path: Path) -> list[tuple[str, dict]]:
    """A factory description: several lines, each namespaced and validated
    on its own. Returns `[(line_name, prefixed_and_validated_config), ...]`
    in file order, which is also simulation order (each line is otherwise
    independent - only `generate_factory` merges their output)."""
    raw = _read_json(path)
    lines_raw = raw.get("lines") or []
    if not lines_raw:
        sys.exit(f"{path}: no lines. A factory needs at least one line.")
    seen: set[str] = set()
    result: list[tuple[str, dict]] = []
    for entry in lines_raw:
        name = entry.get("name")
        if not name or not all(c.isalnum() or c == "_" for c in name):
            sys.exit(f"{path}: line name {name!r} — use letters/digits/underscore only.")
        if name in seen:
            sys.exit(f"{path}: duplicate line name {name!r}.")
        seen.add(name)
        result.append((name, _validate_line(_prefix_line(name, entry), f"{path} (line {name!r})")))
    return result


def in_win(t: int, start: int, end: int) -> bool:
    return start <= t < end


# ---------------------------------------------------------------- simulate
def station_analogs(station: dict) -> list[dict]:
    """Every analog on a station, in column order.

    Real machines expose more than one signal, and the one that drifts before
    a failure is rarely the only one on the box. `analogs: [...]` is the
    general form; a single `analog: {...}` is still accepted because most
    stations have exactly one and the noise of a list would not earn itself.
    """
    if station.get("analogs"):
        return list(station["analogs"])
    if station.get("analog"):
        return [station["analog"]]
    return [{"name": "Value", "base": 0.0, "noise": 0.0}]


def station_setpoints(station: dict) -> list[dict]:
    """The setpoints a station exposes, one per analog that declares one.

    A setpoint is deliberately NOT a generated column. Everything else here
    is replayed from a file, which is exactly what a setpoint must not be:
    it is the one value an engineer (or an approved recommendation) writes
    *into* the plant, so it lives as a live, writable node and the process
    value moves relative to it. See `csv_replay`.
    """
    out = []
    for analog in station_analogs(station):
        spec = analog.get("setpoint")
        if not spec:
            continue
        out.append({
            "name": spec.get("name", f"{analog.get('name', 'Value')}SP"),
            "drives": analog.get("name", "Value"),
            "initial": float(analog.get("base", 0.0)),
            "min": float(spec["min"]),
            "max": float(spec["max"]),
            # How long the process takes to reach a new setpoint. A washer
            # heats over minutes; pretending it snaps would make the
            # write-back demo a lie about physics.
            "lag_s": float(spec.get("lag_s", 60.0)),
            "unit": analog.get("unit", spec.get("unit", "")),
            "decimals": int(analog.get("decimals", 2)),
        })
    return out


def alarm_word(state: int, name: str, t: int, drifts: dict, bursts: dict) -> int:
    """A PLC's alarm bits, from the script rather than from guesswork.

    Real machines expose a packed word, and an analysis suite that cannot
    read one is not talking to real plants. These bits are ground truth: the
    generator obeyed the same events the scorer reads.
    """
    word = 0
    if state == DOWN:
        word |= 1                                   # bit 0: fault
    if any(in_win(t, int(d["start"]), int(d["end"])) for d in drifts.get(name, [])):
        word |= 2                                   # bit 1: process value drifting
    if any(in_win(t, int(b["start"]), int(b["end"])) for b in bursts.get(name, [])):
        word |= 4                                   # bit 2: quality excursion
    if state == CHANGEOVER:
        word |= 8                                   # bit 3: planned stop
    return word


def _targets(event: dict, analogs: list[dict]) -> set[str]:
    """Which analogs an event moves.

    An event may name one (`"analog": "MotorTemp"`). Unnamed, it moves the
    first - correct for the single-analog stations that are the common case,
    and the reason a multi-analog station must be explicit about which signal
    is the precursor.
    """
    named = event.get("analog")
    if named:
        return {named}
    return {analogs[0]["name"]} if analogs else set()


def simulate(config: dict) -> dict[str, list[list]]:
    """One looping pass of the line -> rows per table. Pure function of the config."""
    rng = random.Random(config.get("seed", 0))
    duration = int(config.get("duration_s", 3600))
    stations = config["stations"]
    n = len(stations)
    capacity = int(config.get("buffers", {}).get("capacity", 20))
    initial = int(config.get("buffers", {}).get("initial", capacity // 2))
    orders = list(config.get("orders") or [1])

    # --- unpack the event script into per-tick lookups -------------------
    downs: dict[str, list[tuple[int, int]]] = {}      # station -> windows of DOWN
    stops: dict[str, list[tuple[int, int]]] = {}      # station -> windows of STOPPED (micro)
    drifts: dict[str, list[dict]] = {}                # station -> analog ramps
    bursts: dict[str, list[dict]] = {}                # station -> scrap bursts
    changeovers: list[tuple[int, int]] = []
    resets: dict[str, list[int]] = {}                 # station -> reset times
    effects: dict[str, list[dict]] = {}               # target station -> analog offsets from elsewhere

    for event in config.get("events", []):
        kind, station = event["type"], event.get("station")
        for effect in event.get("effects", []):
            effects.setdefault(effect["station"], []).append(
                {"analog": effect["analog"], "offset": float(effect["offset"]),
                 "start": int(event["start"]), "end": int(event["end"])})
        if kind == "changeover":
            changeovers.append((int(event["start"]), int(event["end"])))
        elif kind == "down":
            downs.setdefault(station, []).append((int(event["start"]), int(event["end"])))
        elif kind == "drift":
            drifts.setdefault(station, []).append(event)
        elif kind == "scrap_burst":
            bursts.setdefault(station, []).append(event)
        elif kind == "counter_reset":
            resets.setdefault(station, []).append(int(event["at"]))
        elif kind == "micro_stops":
            # Pre-roll the random micro-stops so the run stays deterministic.
            every_lo, every_hi = event.get("every_s", [90, 150])
            dur_lo, dur_hi = event.get("duration_s", [8, 18])
            t = 60
            windows = []
            while t < duration - 30:
                start = t + rng.randint(int(every_lo), int(every_hi))
                windows.append((start, start + rng.randint(int(dur_lo), int(dur_hi))))
                t = start
            stops.setdefault(station, []).extend(
                w for w in windows if not any(in_win(w[0], c0, c1) for c0, c1 in changeovers)
            )

    # --- the line itself -------------------------------------------------
    buffers = [initial] * (n - 1)      # buffer[i] sits after station i
    good = [0] * n
    scrap = [0] * n
    run_seconds = [0] * n              # what each machine reports as its runtime
    accum = [0.0] * n                  # fractional-parts accumulator
    order_index = 0

    rows: dict[str, list[list]] = {s["name"]: [] for s in stations}
    rows["Line"] = []

    for t in range(duration):
        changing = any(in_win(t, c0, c1) for c0, c1 in changeovers)
        # A changeover window that just ended advances to the next order.
        if order_index < len(orders) - 1 and any(t == c1 for _, c1 in changeovers):
            order_index += 1

        states = [RUNNING] * n
        produced = [0] * n
        scrapped = [0] * n

        # Last station first, so freed buffer space propagates upstream
        # within the same tick (classic serial-line update order).
        for i in range(n - 1, -1, -1):
            station = stations[i]
            name = station["name"]
            if changing:
                states[i] = CHANGEOVER
                continue
            if any(in_win(t, a, b) for a, b in downs.get(name, [])):
                states[i] = DOWN
                continue
            if any(in_win(t, a, b) for a, b in stops.get(name, [])):
                states[i] = STOPPED
                continue
            if i < n - 1 and buffers[i] >= capacity:
                states[i] = BLOCKED       # no space downstream
                continue
            if i > 0 and buffers[i - 1] <= 0:
                states[i] = STARVED       # nothing upstream (station 0 pulls from raw)
                continue

            accum[i] += float(station["rate_per_min"]) / 60.0
            take = int(accum[i])
            if take <= 0:
                continue  # still RUNNING, just mid-cycle
            accum[i] -= take
            if i > 0:
                take = min(take, buffers[i - 1])
                buffers[i - 1] -= take
            if i < n - 1:
                take = min(take, capacity - buffers[i])

            scrap_pct = float(station.get("scrap_pct", 0.0))
            for burst in bursts.get(name, []):
                if in_win(t, int(burst["start"]), int(burst["end"])):
                    scrap_pct = float(burst["scrap_pct"])
            bad = sum(1 for _ in range(take) if rng.random() < scrap_pct / 100.0)
            ok = take - bad
            if i < n - 1:
                buffers[i] += ok
            produced[i], scrapped[i] = take, bad
            good[i] += ok
            scrap[i] += bad

        # Scripted counter resets — the "counts wrong" drill, on demand.
        for i, station in enumerate(stations):
            if t in resets.get(station["name"], []):
                good[i] = 0
                scrap[i] = 0

        # --- analogs and rows -------------------------------------------
        for i, station in enumerate(stations):
            name = station["name"]
            analogs = station_analogs(station)
            values = []
            for analog in analogs:
                signal = analog.get("name", "Value")
                value = float(analog.get("base", 0.0))
                for drift in drifts.get(name, []):
                    if signal not in _targets(drift, analogs):
                        continue
                    a, b = int(drift["start"]), int(drift["end"])
                    if in_win(t, a, b):
                        frac = (t - a) / max(b - a, 1)
                        value = value + (float(drift["to"]) - value) * frac
                for burst in bursts.get(name, []):
                    if signal not in _targets(burst, analogs):
                        continue
                    in_burst = in_win(t, int(burst["start"]), int(burst["end"]))
                    if in_burst and "analog_offset" in burst:
                        value += float(burst["analog_offset"])
                # What another station's event does to this one. No alarm bit
                # is raised here: the symptom shows, the cause is elsewhere,
                # and finding it is the agent's job.
                for effect in effects.get(name, []):
                    if effect["analog"] == signal and in_win(t, effect["start"], effect["end"]):
                        value += effect["offset"]
                if analog.get("running_only") and states[i] != RUNNING:
                    value = 0.0
                else:
                    value += rng.gauss(0.0, float(analog.get("noise", 0.0)))
                values.append(round(value, int(analog.get("decimals", 2))))

            cycle_ms = (
                round(60000.0 / float(station["rate_per_min"]) + rng.gauss(0, 15))
                if states[i] == RUNNING
                else 0
            )
            if states[i] == RUNNING:
                run_seconds[i] += 1
            rows[name].append([
                t, states[i], good[i], scrap[i], good[i] + scrap[i],
                alarm_word(states[i], name, t, drifts, bursts),
                cycle_ms,
                # Runtime the machine reports itself. The maintenance module
                # already computes this from state history; a plant whose
                # PLCs publish it should be believed rather than recomputed,
                # and the two disagreeing is a finding worth having.
                round(run_seconds[i] / 60.0, 2),
                # Ready is not running: a machine can be willing and starved.
                0 if states[i] in (DOWN, CHANGEOVER) else 1,
                *values,
            ])

        rows["Line"].append([t, orders[order_index], 0 if changing else 1, good[-1]])

    return rows


# ------------------------------------------------------------------ output
def write_output(config: dict, rows: dict[str, list[list]], out: Path,
                  line_tables: tuple[str, ...] = ("Line",)) -> None:
    """Write CSVs + schema.ini for `config["stations"]` plus one summary
    table per name in `line_tables` (default: the single `"Line"` table a
    standalone line produces; a factory passes one per line, e.g.
    `("LineA_Line", "LineB_Line")`, since each line has its own order
    stream and no single-plant `"Line"` table would mean anything)."""
    out.mkdir(parents=True, exist_ok=True)
    headers: dict[str, list[str]] = {}
    for station in config["stations"]:
        signals = [a.get("name", "Value") for a in station_analogs(station)]
        headers[station["name"]] = [
            "TSec", "State", "GoodCount", "ScrapCount", "TotalCount", "AlarmWord",
            "CycleTimeMs", "RunMinutes", "ReadyBit", *signals,
        ]
    for name in line_tables:
        headers[name] = ["TSec", "OrderId", "OrderActive", "LineGoodCount"]

    for table, header in headers.items():
        with (out / f"{table}.csv").open("w", encoding="ascii", newline="\n") as f:
            f.write(",".join(header) + "\n")
            for row in rows[table]:
                f.write(",".join(str(v) for v in row) + "\n")

    # schema.ini pins column types so the ODBC text driver never guesses wrong.
    int_cols = {"TSec", "State", "GoodCount", "ScrapCount", "CycleTimeMs",
                "OrderId", "OrderActive", "LineGoodCount"}
    lines = []
    for table, header in headers.items():
        lines.append(f"[{table}.csv]")
        lines.append("Format=CSVDelimited")
        lines.append("ColNameHeader=True")
        for index, column in enumerate(header, 1):
            lines.append(f"Col{index}={column} {'Long' if column in int_cols else 'Double'}")
        lines.append("")
    (out / "schema.ini").write_text("\n".join(lines), encoding="ascii")


def write_manifest(config: dict, out: Path) -> Path:
    """Describe every tag: what kind it is, its unit, and whether anything
    may write to it.

    This is the file that makes the rest of the OPC suite possible. A screen
    that lets an engineer browse tags, a guard that refuses a write outside
    bounds, and an agent proposing an adjustment all need to know that
    WashTempSP is a setpoint in degrees with a floor and a ceiling, and that
    WashTemp is a reading nobody may write. Generated from the line
    description, so it can never drift out of step with the plant it
    describes (principle 7 - the plant is config).
    """
    tables: dict[str, dict] = {}
    for station in config["stations"]:
        tags: dict[str, dict] = {
            "State": {"kind": "state", "writable": False,
                      "note": "raw PLC integer; the tag map maps it to an MES state"},
            "CycleTimeMs": {"kind": "pv", "unit": "ms", "writable": False},
            "RunMinutes": {"kind": "pv", "unit": "min", "writable": False,
                           "note": "runtime the machine reports; maintenance "
                                   "plans trigger on runtime hours"},
            "ReadyBit": {"kind": "state", "writable": False,
                         "note": "1 when the machine is able to run, whether "
                                 "or not it currently is"},
        }
        for counter in COUNTER_TAGS:
            tags[counter] = {"kind": "counter", "writable": False}
        inspection = station_inspection(station)
        if inspection:
            # The group a vision station publishes for every unit it judges.
            # The agent subscribes to all of these at full rate and takes
            # them together; none is history.
            tags["InspSeq"] = {"kind": "inspection", "writable": False,
                               "note": "the station's own event counter; a gap is a missed group"}
            tags["InspSerial"] = {"kind": "inspection", "writable": False, "note": "the unit judged"}
            tags["InspPass"] = {"kind": "inspection", "writable": False,
                                "note": "0 = passed; bit i set = attribute i failed"}
            tags["InspMembers"] = {"kind": "inspection", "writable": False,
                                   "note": "for a stack, wrap or pallet: the serials it now contains, comma separated"}
            for a in inspection.get("attributes", []):
                tags[f"Insp_{a['name']}"] = {"kind": "inspection", "unit": a.get("unit", ""), "writable": False,
                                              "min": a["min"], "max": a["max"], "nominal": a["nominal"]}
        tags["AlarmWord"] = {
            "kind": "alarm", "writable": False,
            "bits": {"0": "fault", "1": "process value drifting",
                     "2": "quality excursion", "3": "planned stop"}}

        setpoints = {s["drives"]: s for s in station_setpoints(station)}
        for analog in station_analogs(station):
            name = analog.get("name", "Value")
            entry = {"kind": "pv", "unit": analog.get("unit", ""),
                     "writable": False,
                     "nominal": float(analog.get("base", 0.0))}
            if name in setpoints:
                entry["follows"] = setpoints[name]["name"]
            tags[name] = entry
        for spec in station_setpoints(station):
            tags[spec["name"]] = {
                "kind": "sp", "unit": spec["unit"], "writable": True,
                "min": spec["min"], "max": spec["max"],
                "initial": spec["initial"], "lag_s": spec["lag_s"],
                "drives": spec["drives"], "decimals": spec["decimals"]}
        tables[station["name"]] = {"tags": tags}
        if inspection:
            tables[station["name"]]["inspection"] = inspection

    manifest = {
        "_what": "Every tag this line exposes, generated from its line.json. "
                 "Read by the OPC replay (which tags are live and writable), "
                 "the tag browser, and the write guard.",
        "tables": tables,
    }
    path = out / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def station_inspection(station: dict) -> dict | None:
    """A station's vision inspection, if it has one - what it judges and
    what it emits. Validated here so the replay and the agent can trust it.

        "inspection": {"kind": "piece" | "stack" | "wrap" | "pallet",
                       "prefix": "F", "material": "UT-FORK",
                       "attributes": [{"name": "Length", "unit": "mm",
                                       "nominal": 160.0, "min": 159.0, "max": 161.0, "sigma": 0.3}, ...],
                       "members": 3 (stack) | 1 (wrap) | 240 (pallet),
                       "takes": ["UT-FORK", "UT-SPOON", "UT-KNIFE"] (what a stack draws its members from),
                       "plate": {"prefix": "PLT", "material": "RAW-PLATE"} (wrap)}
    """
    spec = station.get("inspection")
    if not spec:
        return None
    if spec.get("kind") not in ("piece", "stack", "wrap", "pallet"):
        sys.exit(f"station {station.get('name')!r}: inspection kind {spec.get('kind')!r} is not "
                 f"piece, stack, wrap or pallet.")
    attributes = spec.get("attributes") or []
    for a in attributes:
        for key in ("name", "nominal", "min", "max"):
            if key not in a:
                sys.exit(f"station {station.get('name')!r}: inspection attribute {a} lacks {key!r}.")
    return spec


def _scenario_rows(config: dict) -> list[tuple[str, str]]:
    """The `(when, event)` pairs for one line's scripted events, in file
    order. Shared by `write_scenario` (one line) and `write_factory_scenario`
    (several), so the formatting can't drift between them."""
    def mmss(seconds: int) -> str:
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    out: list[tuple[str, str]] = []
    for event in config.get("events", []):
        kind = event["type"]
        if kind == "micro_stops":
            out.append(("throughout",
                        f"{event['station']}: micro-stops "
                        f"{event.get('duration_s', [8, 18])}s every "
                        f"~{event.get('every_s', [90, 150])}s"))
        elif kind == "counter_reset":
            out.append((mmss(int(event['at'])), f"{event['station']}: counters reset to 0"))
        elif kind == "changeover":
            out.append((f"{mmss(int(event['start']))}-{mmss(int(event['end']))}",
                        "changeover (whole line)"))
        else:
            what = {"down": "DOWN", "drift": f"analog drifts to {event.get('to')}",
                    "scrap_burst": f"scrap burst {event.get('scrap_pct')}%"}[kind]
            for effect in event.get("effects", []):
                what += (f"; meanwhile {effect['station']}.{effect['analog']} "
                         f"{float(effect['offset']):+g} with no alarm of its own")
            out.append((f"{mmss(int(event['start']))}-{mmss(int(event['end']))}",
                        f"{event['station']}: {what}"))
    return out


def write_scenario(config: dict, out_dir: Path) -> None:
    """A human timeline of the scripted events, for running exercises against."""
    lines = [
        "# Scenario timeline",
        "",
        f"One looping {config.get('duration_s', 3600)}s pass, 1 row/second, seed "
        f"{config.get('seed', 0)}. `TSec` in every table is sim-time — line any "
        "observation up against this list.",
        "",
        "| when | event |",
        "|---|---|",
    ]
    for when, what in _scenario_rows(config):
        lines.append(f"| {when} | {what} |")
    lines.append("")
    lines.append("Every loop the file wraps and all counters snap back to zero — "
                 "a free counter-reset drill each pass.")
    lines.append("")
    lines.append("States: 0 stopped · 1 running · 2 starved · 3 blocked · 4 down · 5 changeover")
    (out_dir / "scenario.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_factory_scenario(lines: list[tuple[str, dict]], out_dir: Path) -> None:
    """The same timeline as `write_scenario`, one line at a time, with each
    row naming which line it belongs to - a factory's scenario doc is
    several lines' worth of independent scripts, not one merged timeline."""
    duration = lines[0][1].get("duration_s", 3600) if lines else 3600
    doc = [
        "# Scenario timeline (factory)",
        "",
        f"{len(lines)} lines, each a looping {duration}s pass, 1 row/second. "
        "`TSec` in every table is sim-time — line any observation up against "
        "this list. Station and line-summary table names are all prefixed "
        "`<line>_...`, matching the manifest.",
        "",
        "| line | when | event |",
        "|---|---|---|",
    ]
    for name, cfg in lines:
        for when, what in _scenario_rows(cfg):
            doc.append(f"| {name} | {when} | {what} |")
    doc.append("")
    doc.append("Every loop each line's file wraps and its counters snap back to "
                "zero independently — lines are simulated one at a time and do "
                "not share a clock beyond a common `duration_s`.")
    doc.append("")
    doc.append("States: 0 stopped · 1 running · 2 starved · 3 blocked · 4 down · 5 changeover")
    (out_dir / "scenario.md").write_text("\n".join(doc) + "\n", encoding="utf-8")


def generate(config_path: Path, out: Path, write_docs: bool = True) -> dict:
    """Generate a line - or a whole factory of lines - from its description.
    The importable entry point.

    Tests used to reach the old hardcoded generator through importlib because
    it was a script rather than a module. The simulator is product code now,
    so callers just import it.

    Dispatches on shape: a top-level `"lines"` array means a factory
    (`generate_factory`); a top-level `"stations"` list means the original
    single line. Callers never need to know which they handed it - a plant's
    registry entry just points `replay_dir` at whichever this wrote.
    """
    raw = _read_json(Path(config_path))
    if "lines" in raw:
        return generate_factory(config_path, out, write_docs)
    config = load_config(Path(config_path))
    rows = simulate(config)
    write_output(config, rows, Path(out))
    write_manifest(config, Path(out))
    if write_docs:
        write_scenario(config, Path(out))
    return config


def generate_factory(config_path: Path, out: Path, write_docs: bool = True) -> dict:
    """A factory of several independently-scripted lines, merged into one
    manifest and one set of CSVs so the rest of the product (tag map, OPC
    replay, manifest-driven screens) sees "a plant with more stations" and
    nothing new. Each line is simulated on its own by the unmodified
    `simulate()` - lines never interact physically, only their output is
    merged - which keeps the deterministic-seed guarantee per line intact.
    """
    lines = load_factory_config(Path(config_path))
    combined_rows: dict[str, list[list]] = {}
    line_tables: list[str] = []
    all_stations: list[dict] = []

    for name, cfg in lines:
        rows = simulate(cfg)
        line_table = f"{name}_Line"
        combined_rows[line_table] = rows.pop("Line")
        line_tables.append(line_table)
        combined_rows.update(rows)
        all_stations.extend(cfg["stations"])

    merged = {"stations": all_stations}
    write_output(merged, combined_rows, Path(out), line_tables=tuple(line_tables))
    write_manifest(merged, Path(out))
    if write_docs:
        write_factory_scenario(lines, Path(out))
    return {"lines": [name for name, _ in lines], "stations": [s["name"] for s in all_stations]}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the simulated line (or factory of lines) as CSVs for Kepware.")
    parser.add_argument("config", nargs="?", default="line.example.json",
                        help="line, or factory, description (JSON)")
    parser.add_argument("--out", default=None,
                        help="output folder (default: out/ beside this script)")
    args = parser.parse_args()

    config_path = Path(args.config)
    out = Path(args.out) if args.out else Path(__file__).resolve().parent / "out"
    raw = _read_json(config_path)

    if "lines" in raw:
        result = generate_factory(config_path, out)
        n_stations = len(result["stations"])
        print(f"{config_path.name}: {len(result['lines'])} lines, {n_stations} stations -> {out}")
        return

    config = load_config(config_path)
    rows = simulate(config)
    generate(config_path, out)

    total = sum(len(v) for v in rows.values())
    print(f"{config_path.name}: {len(rows)} tables x {config.get('duration_s', 3600)} rows "
          f"({total} rows total) -> {out}")
    print(f"Channel '{config.get('channel', 'SimLine')}', DSN '{config.get('dsn', 'LineSimCSV')}'. "
          f"Next: setup_dsn.ps1 (elevated), then kepware_setup.py.")


if __name__ == "__main__":
    main()
