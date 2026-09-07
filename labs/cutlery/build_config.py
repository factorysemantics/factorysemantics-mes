"""Build labs/cutlery/line.json: the cutlery plant that gives sixty million
things a day their own identity, each one judged by a vision station.

    python3 labs/cutlery/build_config.py

The customer's spec (2026-09-06): ten million forks, ten million spoons, ten
million knives, ten million plates, ten million stacks and ten million wraps
a day - sixty million unique ids. Every utensil gets an automated vision
inspection judging four attributes, published as one OPC group; a good piece
goes to a stack, which gets its own inspection and group; a stack and a plate
become a wrap; wraps go on pallets; dimensional checks every fifteen minutes
roll up into a certificate of analysis per pallet.

The plant:

    FORK1-3, SPOON1-3, KNIFE1-3   nine moulding lines, 2,315 pieces/min each,
                                  Mold -> Degate -> Mark, where the laser
                                  marker's vision station judges and serialises
    STACK01-32                    thirty-two stackers, a fork + spoon + knife each,
                                  217 stacks/min, own vision group, two per cell
    WRAP01-16                     sixteen wrappers, a stack onto a plate, 434/min
                                  (two stackers to a wrapper: a stack takes twice
                                  as long as a wrap), one per cell
    PAL1-4                        four palletizers, 240 wraps a pallet, one per
                                  group of four wrappers

Every number derives from PIECES_PER_TYPE_PER_DAY. The generator simulates
the lines independently; the flow between them - which pieces a stack took,
which stack a wrap took, which wraps a pallet took - is carried by the
replay's inspection groups (fsmes.integrations.opc.csv_replay.Flow), so a
stack only ever contains pieces a marker judged good.

Each line's `_meta` block and each station's `inspection` block are data for
init.py, the replay and the agent; underscore keys are not simulation input.
"""
from __future__ import annotations

import json
from pathlib import Path

DURATION_S = 3600
SEED = 9200

PIECES_PER_TYPE_PER_DAY = 10_000_000
UTENSILS = ("FORK", "SPOON", "KNIFE")
LINES_PER_TYPE = 3
STACKERS = 32
WRAPPERS = 16                      # two stackers feed one wrapper
PALLETIZERS = 4                    # four wrappers feed one palletizer
WRAPS_PER_PALLET = 240

MINUTES_PER_DAY = 24 * 60
PIECES_PER_MIN_PER_LINE = PIECES_PER_TYPE_PER_DAY / LINES_PER_TYPE / MINUTES_PER_DAY   # 2,314.8
STACKS_PER_DAY = PIECES_PER_TYPE_PER_DAY                                              # one of each per stack
STACKS_PER_MIN_PER_STACKER = STACKS_PER_DAY / STACKERS / MINUTES_PER_DAY               # 217.0  (0.28 s)
WRAPS_PER_MIN_PER_WRAPPER = STACKS_PER_DAY / WRAPPERS / MINUTES_PER_DAY                # 434.0  (0.14 s)
PALLETS_PER_MIN_PER_PALLETIZER = STACKS_PER_DAY / WRAPS_PER_PALLET / PALLETIZERS / MINUTES_PER_DAY  # 7.2

# One plant-wide changeover window on every line: the scorer binds a
# changeover to every machine in the timeline, so lines must share it.
CHANGEOVER = {"type": "changeover", "start": 3000, "end": 3060}

# The four attributes each vision station judges, per kind of station.
# nominal / min / max are the judgement; sigma is the process's own spread,
# set so that almost every unit the line calls good passes, and a unit the
# line scrapped fails on one of them.
PIECE_ATTRIBUTES = {
    "FORK": [
        {"name": "Length", "unit": "mm", "nominal": 165.0, "min": 164.0, "max": 166.0, "sigma": 0.25},
        {"name": "TineGap", "unit": "mm", "nominal": 3.2, "min": 2.9, "max": 3.5, "sigma": 0.07},
        {"name": "Flash", "unit": "mm", "nominal": 0.05, "min": 0.0, "max": 0.2, "sigma": 0.03},
        {"name": "Gloss", "unit": "GU", "nominal": 82.0, "min": 75.0, "max": 90.0, "sigma": 1.8},
    ],
    "SPOON": [
        {"name": "Length", "unit": "mm", "nominal": 160.0, "min": 159.0, "max": 161.0, "sigma": 0.25},
        {"name": "BowlDepth", "unit": "mm", "nominal": 9.0, "min": 8.5, "max": 9.5, "sigma": 0.12},
        {"name": "Flash", "unit": "mm", "nominal": 0.05, "min": 0.0, "max": 0.2, "sigma": 0.03},
        {"name": "Gloss", "unit": "GU", "nominal": 82.0, "min": 75.0, "max": 90.0, "sigma": 1.8},
    ],
    "KNIFE": [
        {"name": "Length", "unit": "mm", "nominal": 175.0, "min": 174.0, "max": 176.0, "sigma": 0.25},
        {"name": "EdgeStraightness", "unit": "mm", "nominal": 0.1, "min": 0.0, "max": 0.4, "sigma": 0.07},
        {"name": "Flash", "unit": "mm", "nominal": 0.05, "min": 0.0, "max": 0.2, "sigma": 0.03},
        {"name": "Gloss", "unit": "GU", "nominal": 82.0, "min": 75.0, "max": 90.0, "sigma": 1.8},
    ],
}
STACK_ATTRIBUTES = [
    {"name": "Count", "unit": "pieces", "nominal": 3.0, "min": 3.0, "max": 3.0, "sigma": 0.0},
    {"name": "Alignment", "unit": "mm", "nominal": 0.5, "min": 0.0, "max": 2.0, "sigma": 0.3},
    {"name": "Orientation", "unit": "deg", "nominal": 0.0, "min": -5.0, "max": 5.0, "sigma": 1.2},
    {"name": "Height", "unit": "mm", "nominal": 12.0, "min": 11.0, "max": 13.0, "sigma": 0.25},
]
WRAP_ATTRIBUTES = [
    {"name": "SealIntegrity", "unit": "%", "nominal": 99.0, "min": 95.0, "max": 100.0, "sigma": 0.9},
    {"name": "FilmTension", "unit": "N", "nominal": 2.6, "min": 2.0, "max": 3.2, "sigma": 0.15},
    {"name": "PlatePresent", "unit": "bool", "nominal": 1.0, "min": 1.0, "max": 1.0, "sigma": 0.0},
    {"name": "Weight", "unit": "g", "nominal": 21.5, "min": 20.5, "max": 22.5, "sigma": 0.25},
]

# The dimensional checks a person records every fifteen minutes - the
# characteristics the certificate of analysis states a Cpk for. Five per
# utensil, on the finished piece, in the units a gauge reads.
DIMENSIONAL_SPECS = {
    "FORK": [("length_mm", "mm", 164.0, 166.0, 165.0), ("width_mm", "mm", 24.0, 26.0, 25.0),
             ("thickness_mm", "mm", 1.9, 2.3, 2.1), ("weight_g", "g", 3.6, 4.2, 3.9),
             ("tine_gap_mm", "mm", 2.9, 3.5, 3.2)],
    "SPOON": [("length_mm", "mm", 159.0, 161.0, 160.0), ("width_mm", "mm", 33.0, 35.0, 34.0),
              ("thickness_mm", "mm", 1.9, 2.3, 2.1), ("weight_g", "g", 4.4, 5.0, 4.7),
              ("bowl_depth_mm", "mm", 8.5, 9.5, 9.0)],
    "KNIFE": [("length_mm", "mm", 174.0, 176.0, 175.0), ("width_mm", "mm", 17.0, 19.0, 18.0),
              ("thickness_mm", "mm", 1.9, 2.3, 2.1), ("weight_g", "g", 4.0, 4.6, 4.3),
              ("edge_straightness_mm", "mm", 0.0, 0.4, 0.1)],
}


def _a(name: str, base: float, noise: float, unit: str, **extra) -> dict:
    analog = {"name": name, "base": base, "noise": noise, "unit": unit}
    analog.update(extra)
    return analog


def _sp(name: str, lo: float, hi: float, lag_s: float) -> dict:
    return {"setpoint": {"name": name, "min": lo, "max": hi, "lag_s": lag_s}}


def _station(name: str, rate: float, scrap: float, analogs: list[dict], inspection: dict | None = None) -> dict:
    station = {"name": name, "rate_per_min": round(rate, 2), "scrap_pct": scrap, "analogs": analogs}
    if inspection:
        station["inspection"] = inspection
    return station


def moulding_line(utensil: str, k: int, index: int) -> dict:
    """Line k of a utensil's three. Mold runs a little faster than the marker
    and Degate a little slower, so the buffer after Mold drains and the one
    before the marker fills - buffers that do something, on purpose."""
    name = f"{utensil}{k}"
    r = PIECES_PER_MIN_PER_LINE
    melt = {"FORK": 214.0, "SPOON": 218.0, "KNIFE": 222.0}[utensil]
    down_at = 600 + index * 240
    return {
        "name": name, "seed": SEED + index, "duration_s": DURATION_S,
        "buffers": {"capacity": 3000, "initial": 600},
        "stations": [
            _station("Mold", r * 1.012, 0.4, [_a("MeltTemp", melt, 2.5, "C", **_sp("MeltTempSP", 200.0, 235.0, 120.0)),
                                             _a("CycleTime", 8.2, 0.15, "s")]),
            _station("Degate", r * 0.998, 0.2, [_a("BladeWear", 12.0 + k * 3, 0.8, "%")]),
            _station("Mark", r, 0.15, [_a("LaserPower", 18.0, 0.4, "W"), _a("VisionScore", 0.97, 0.01, "")],
                     inspection={"kind": "piece", "prefix": f"{utensil[0]}{k}", "material": f"UT-{utensil}",
                                 "attributes": PIECE_ATTRIBUTES[utensil]}),
        ],
        "events": [
            {"type": "down", "station": "Mold", "start": down_at, "end": down_at + 90},
            {"type": "micro_stops", "station": "Mark", "every_s": [240, 420], "duration_s": [6, 14]},
            CHANGEOVER,
        ],
        "_meta": {
            "kind": "utensil", "area": "Moulding", "utensil": utensil,
            "material": {"code": f"UT-{utensil}", "name": f"{utensil.title()} (PP)", "unit": "ea"},
            "raw": {"code": "RAW-PP", "name": "Polypropylene resin", "unit": "kg"},
            "marker": f"{name}_Mark",
            "dimensional_specs": DIMENSIONAL_SPECS[utensil],
        },
    }


def stacker_line(i: int) -> dict:
    name = f"STACK{i:02d}"
    cell = (i - 1) // 2 + 1
    events = [CHANGEOVER]
    if i % 8 == 3:
        events.insert(0, {"type": "down", "station": "Stacker", "start": 1500 + i * 30, "end": 1560 + i * 30})
    if i % 2:
        events.insert(0, {"type": "micro_stops", "station": "Stacker", "every_s": [300, 500], "duration_s": [5, 12]})
    return {
        "name": name, "seed": SEED + 100 + i, "duration_s": DURATION_S,
        "stations": [
            _station("Stacker", STACKS_PER_MIN_PER_STACKER, 0.3,
                     [_a("GripForce", 9.0, 0.4, "N"), _a("VisionScore", 0.98, 0.01, "")],
                     inspection={"kind": "stack", "prefix": f"ST{i:02d}", "material": "STACK-3",
                                 "takes": ["UT-FORK", "UT-SPOON", "UT-KNIFE"], "members": 3, "cell": cell,
                                 "attributes": STACK_ATTRIBUTES}),
        ],
        "events": events,
        "_meta": {"kind": "stacker", "area": "Stacking", "cell": cell,
                  "material": {"code": "STACK-3", "name": "Fork, spoon and knife stack", "unit": "ea"},
                  "station": f"{name}_Stacker"},
    }


def wrapper_line(i: int) -> dict:
    name = f"WRAP{i:02d}"
    group = (i - 1) // 4 + 1
    events = [{"type": "micro_stops", "station": "Wrapper", "every_s": [200, 400], "duration_s": [4, 10]}, CHANGEOVER]
    if i in (2, 11):
        events.insert(0, {"type": "down", "station": "Wrapper", "start": 2100 + i * 20, "end": 2220 + i * 20})
    return {
        "name": name, "seed": SEED + 200 + i, "duration_s": DURATION_S,
        "stations": [
            _station("Wrapper", WRAPS_PER_MIN_PER_WRAPPER, 0.5,
                     [_a("SealTemp", 138.0, 1.5, "C", **_sp("SealTempSP", 120.0, 160.0, 60.0)),
                      _a("FilmTension", 2.6, 0.12, "N")],
                     inspection={"kind": "wrap", "prefix": f"W{i:02d}", "material": "WRAP", "members": 1,
                                 "cell": i, "group": group,
                                 "plate": {"prefix": f"PLT{i:02d}", "material": "RAW-PLATE"},
                                 "attributes": WRAP_ATTRIBUTES}),
        ],
        "events": events,
        "_meta": {"kind": "wrapper", "area": "Wrapping", "cell": i, "group": group,
                  "material": {"code": "WRAP", "name": "Wrapped cutlery set on a plate", "unit": "ea"},
                  "plate": {"code": "RAW-PLATE", "name": "Paper plate", "unit": "ea"},
                  "station": f"{name}_Wrapper"},
    }


def pallet_line(g: int) -> dict:
    name = f"PAL{g}"
    return {
        "name": name, "seed": SEED + 300 + g, "duration_s": DURATION_S,
        "buffers": {"capacity": 8, "initial": 2},
        "stations": [
            _station("Palletizer", PALLETS_PER_MIN_PER_PALLETIZER * 1.02, 0.0, [_a("StackHeight", 1.6, 0.03, "m")],
                     inspection={"kind": "pallet", "prefix": f"PL{g}", "material": "PALLET",
                                 "members": WRAPS_PER_PALLET, "group": g, "attributes": []}),
            _station("StretchWrap", PALLETS_PER_MIN_PER_PALLETIZER * 1.02, 0.0, [_a("FilmTension", 3.1, 0.1, "N")]),
        ],
        "events": [CHANGEOVER],
        "_meta": {"kind": "palletizer", "area": "Palletizing", "group": g,
                  "material": {"code": "PALLET", "name": "Outbound pallet of wrapped sets", "unit": "ea"},
                  "station": f"{name}_Palletizer", "wraps_per_pallet": WRAPS_PER_PALLET},
    }


def build() -> dict:
    lines = []
    index = 0
    for utensil in UTENSILS:
        for k in range(1, LINES_PER_TYPE + 1):
            lines.append(moulding_line(utensil, k, index))
            index += 1
    lines += [stacker_line(i) for i in range(1, STACKERS + 1)]
    lines += [wrapper_line(i) for i in range(1, WRAPPERS + 1)]
    lines += [pallet_line(g) for g in range(1, PALLETIZERS + 1)]
    ids_per_day = PIECES_PER_TYPE_PER_DAY * 3 + STACKS_PER_DAY * 3 + STACKS_PER_DAY // WRAPS_PER_PALLET
    return {
        "_what": (f"A cutlery plant giving {ids_per_day:,} things a day their own id, each judged by a vision "
                  f"station: {len(UTENSILS) * LINES_PER_TYPE} moulding lines, {STACKERS} stackers, {WRAPPERS} "
                  f"wrappers, {PALLETIZERS} palletizers. Built by build_config.py; the flow between lines is "
                  "carried by the replay's inspection groups."),
        "_plant": {
            "pieces_per_type_per_day": PIECES_PER_TYPE_PER_DAY,
            "pieces_per_day": PIECES_PER_TYPE_PER_DAY * 3,
            "stacks_per_day": STACKS_PER_DAY,
            "plates_per_day": STACKS_PER_DAY,
            "wraps_per_day": STACKS_PER_DAY,
            "pallets_per_day": STACKS_PER_DAY // WRAPS_PER_PALLET,
            "ids_per_day": ids_per_day,
            "inspection_events_per_day": PIECES_PER_TYPE_PER_DAY * 3 + STACKS_PER_DAY * 2,
            "wraps_per_pallet": WRAPS_PER_PALLET,
            "stacker_cycle_s": round(60 / STACKS_PER_MIN_PER_STACKER, 3),
            "wrapper_cycle_s": round(60 / WRAPS_PER_MIN_PER_WRAPPER, 3),
            "pallet_cycle_s": round(60 / PALLETS_PER_MIN_PER_PALLETIZER, 1),
        },
        "lines": lines,
    }


if __name__ == "__main__":
    here = Path(__file__).parent
    config = build()
    (here / "line.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    stations = sum(len(ln["stations"]) for ln in config["lines"])
    print(f"wrote {here / 'line.json'}: {len(config['lines'])} lines, {stations} stations; {config['_plant']}")
