"""Build labs/megafactory/full/line.json: the full mega-factory.

    python3 labs/megafactory/full/build_config.py

Twenty lines from eight line *templates*, 108 stations, one plant. The
templates carry everything a line of that kind is: its stations and their
analogs, the script of what goes wrong on it, and - new here - the quality
characteristics its product is checked for, each with a measurement *kind*.
An instance of a template is the template with a suffix, its own seed and
its events shifted in time so twenty lines never break in lockstep.

The measurement kinds are the deliberate part. The plan's own open question
says the SPC module is built for variable data; a real factory's thirty-odd
checks include go/no-go gauges, pass/fail visuals, defect counts and coded
defect categories. Those are here on purpose, so what the product does with
them is measured rather than avoided (see seed_breadth.py for what it did).

Every line's `_meta` block is data for the seeders (init.py, seed_breadth.py)
and is ignored by the generator: underscore keys are not simulation input.
"""
from __future__ import annotations

import json
from pathlib import Path

DURATION_S = 3600
SEED = 7100

# One plant-wide changeover window on every line. The scorer binds a
# changeover to every machine in the timeline (a known factory limitation
# recorded in fsmes.sim.truth), so lines must share the window or a line's
# planned stop would be scored as another line's misclassified downtime.
CHANGEOVER = {"type": "changeover", "start": 3000, "end": 3060}

# Instance k of a template shifts its scripted times by this much, so the
# factory's breakdowns spread across the hour instead of stacking.
OFFSET_S = 211
MAX_BASE_S = 2500  # base event times stay below this so shifted ones stay < 3000


def _a(name: str, base: float, noise: float, unit: str, **extra) -> dict:
    analog = {"name": name, "base": base, "noise": noise, "unit": unit}
    analog.update(extra)
    return analog


def _sp(name: str, lo: float, hi: float, lag_s: float) -> dict:
    return {"setpoint": {"name": name, "min": lo, "max": hi, "lag_s": lag_s}}


# Measurement kinds and what each means for the seeder:
#   variable    a number against min/max          - what the product models
#   pass_fail   inspector says pass or fail        - attribute
#   go_nogo     a fixed-limit gauge, go or no-go   - attribute
#   count       defects per unit, an integer >= 0  - attribute (c/u-chart data)
#   categorical a defect code from a list          - nominal, unordered
#   ordinal     a graded scale (0 best .. N worst) - ordered, not continuous
def _var(name, unit, lo, hi, measured=None):
    return {"name": name, "kind": "variable", "unit": unit, "min": lo, "max": hi,
            **({"measured": measured} if measured else {})}


def _pf(name):
    return {"name": name, "kind": "pass_fail", "unit": ""}


def _gng(name):
    return {"name": name, "kind": "go_nogo", "unit": ""}


def _cnt(name, max_ok):
    return {"name": name, "kind": "count", "unit": "count", "max": max_ok}


def _cat(name, codes):
    return {"name": name, "kind": "categorical", "unit": "code", "codes": codes}


def _ord(name, worst_ok, top):
    return {"name": name, "kind": "ordinal", "unit": "grade", "max": worst_ok, "top": top}


# template -> dict(stations=[(name, rate, scrap, analogs)], events=[...],
#                  meta=dict(area, material, unit, characteristics))
TEMPLATES: dict[str, dict] = {
    "FILL": {
        "area": "Filling", "material": ("Bottled Product", "ea"),
        "stations": [
            ("Loader", 40, 0.3, [_a("InfeedRate", 40.0, 2.0, "bpm")]),
            ("Washer", 40, 0.2, [_a("WashTemp", 65.0, 1.0, "C", **_sp("WashTempSP", 55.0, 80.0, 90.0)),
                                 _a("RinsePressure", 2.4, 0.08, "bar")]),
            ("Filler", 38, 0.5, [_a("FillWeight", 500.0, 1.5, "g"), _a("FillTemp", 4.0, 0.3, "C")]),
            ("Capper", 38, 0.4, [_a("TorqueLevel", 12.0, 0.6, "Nm")]),
            ("Labeler", 38, 0.3, [_a("LabelTension", 3.0, 0.2, "N")]),
            ("Palletizer", 36, 0.2, [_a("StackHeight", 1.2, 0.05, "m")]),
        ],
        "events": [
            {"type": "drift", "station": "Washer", "analog": "WashTemp", "start": 1500, "end": 1800, "to": 74.0,
             "effects": [{"station": "Filler", "analog": "FillWeight", "offset": -9.0}]},
            {"type": "down", "station": "Capper", "start": 2400, "end": 2450},
            {"type": "micro_stops", "station": "Labeler"},
        ],
        "characteristics": [
            _var("fill_weight", "g", 494.0, 506.0, measured=("Filler", "FillWeight")),
            _var("cap_torque", "Nm", 8.0, 16.0),
            _var("net_content", "ml", 497.0, 503.0),
            _var("label_position", "mm", -1.5, 1.5),
            _var("brix", "Bx", 10.2, 10.8),
            _gng("cap_present"),
            _pf("visual_defect"),
        ],
    },
    "MACH": {
        "area": "Machining", "material": ("Machined Part", "ea"),
        "stations": [
            ("Saw", 22, 0.8, [_a("BladeLoad", 42.0, 2.5, "%")]),
            ("Mill", 18, 0.6, [_a("SpindleTemp", 55.0, 3.0, "C"), _a("SpindleLoad", 61.0, 4.0, "%")]),
            ("Deburr", 25, 0.4, [_a("CycleForce", 30.0, 1.5, "N")]),
            ("Inspect", 24, 0.1, [_a("GaugeReading", 10.0, 0.1, "mm")]),
            ("Pack", 24, 0.1, [_a("SealTemp", 120.0, 2.0, "C")]),
        ],
        "events": [
            {"type": "down", "station": "Mill", "start": 900, "end": 960},
            {"type": "drift", "station": "Saw", "start": 2000, "end": 2200, "to": 55.0},
            {"type": "micro_stops", "station": "Deburr"},
        ],
        "characteristics": [
            _var("bore_diameter", "mm", 9.85, 10.15, measured=("Inspect", "GaugeReading")),
            _var("surface_finish_ra", "um", 0.4, 3.2),
            _var("flatness", "mm", 0.0, 0.05),
            _var("hardness", "HRC", 38.0, 44.0),
            _gng("thread_gauge"),
            _cnt("burr_count", 0),
        ],
    },
    "ASSEM": {
        "area": "Assembly", "material": ("Assembled Unit", "ea"),
        "stations": [
            ("Kit", 30, 0.2, [_a("PickRate", 30.0, 1.0, "ppm")]),
            ("Assemble1", 28, 0.5, [_a("PressForce", 200.0, 8.0, "N")]),
            ("Assemble2", 28, 0.6, [_a("PressForce", 210.0, 8.0, "N")]),
            ("Torque", 27, 0.3, [_a("TorqueLevel", 8.0, 0.4, "Nm", **_sp("TorqueLevelSP", 5.0, 12.0, 20.0))]),
            ("Test", 27, 0.2, [_a("TestCurrent", 1.2, 0.05, "A"), _a("LeakRate", 0.8, 0.1, "sccm")]),
            ("Pack2", 27, 0.1, [_a("SealTemp", 118.0, 2.0, "C")]),
        ],
        "events": [
            {"type": "scrap_burst", "station": "Assemble2", "start": 1200, "end": 1260, "scrap_pct": 18},
            {"type": "down", "station": "Torque", "start": 2100, "end": 2200},
            {"type": "drift", "station": "Assemble1", "start": 400, "end": 700, "to": 235.0,
             "effects": [{"station": "Test", "analog": "LeakRate", "offset": 1.4}]},
        ],
        "characteristics": [
            _var("press_force_peak", "N", 180.0, 230.0),
            _var("torque_final", "Nm", 6.5, 9.5),
            _var("test_current", "A", 1.05, 1.35),
            _var("leak_rate", "sccm", 0.0, 1.5, measured=("Test", "LeakRate")),
            _pf("functional_test"),
            _pf("missing_part"),
            _cnt("scratch_count", 2),
        ],
    },
    "WELD": {
        "area": "Welding", "material": ("Weldment", "ea"),
        "stations": [
            ("Cut", 20, 0.4, [_a("FeedRate", 15.0, 0.8, "m/min")]),
            ("Weld1", 16, 0.9, [_a("ArcCurrent", 180.0, 6.0, "A"), _a("WireFeed", 7.5, 0.3, "m/min")]),
            ("Weld2", 16, 0.9, [_a("ArcCurrent", 185.0, 6.0, "A")]),
            ("Grind", 19, 0.5, [_a("MotorTemp", 48.0, 2.0, "C")]),
            ("Paint", 18, 0.3, [_a("BoothPressure", 1.0, 0.05, "bar")]),
        ],
        "events": [
            {"type": "drift", "station": "Weld1", "analog": "ArcCurrent", "start": 500, "end": 700, "to": 210.0},
            {"type": "down", "station": "Grind", "start": 1800, "end": 1850},
        ],
        "characteristics": [
            _var("weld_penetration", "mm", 2.0, 4.0),
            _var("weld_width", "mm", 5.0, 8.0),
            _var("leg_length", "mm", 5.5, 7.5),
            _var("tensile", "kN", 18.0, 30.0),
            _ord("porosity_grade", 1, 4),
            _pf("spatter"),
        ],
    },
    "EXTRUDE": {
        "area": "Extrusion", "material": ("Extruded Profile", "m"),
        "stations": [
            ("Extrude", 12, 0.7, [_a("MeltTemp", 210.0, 3.0, "C", **_sp("MeltTempSP", 190.0, 230.0, 120.0)),
                                  _a("ScrewSpeed", 45.0, 1.0, "rpm")]),
            ("Cool", 12, 0.2, [_a("WaterTemp", 18.0, 0.5, "C")]),
            ("Trim", 12, 0.4, [_a("BladeSpeed", 900.0, 20.0, "rpm")]),
            ("QC", 12, 0.1, [_a("Thickness", 2.0, 0.05, "mm")]),
            ("Pack3", 12, 0.1, [_a("SealTemp", 115.0, 2.0, "C")]),
        ],
        "events": [
            {"type": "down", "station": "Extrude", "start": 300, "end": 400},
            {"type": "drift", "station": "Extrude", "analog": "MeltTemp", "start": 1300, "end": 1600, "to": 226.0,
             "effects": [{"station": "QC", "analog": "Thickness", "offset": -0.25}]},
            {"type": "micro_stops", "station": "Trim"},
        ],
        "characteristics": [
            _var("thickness", "mm", 1.8, 2.2, measured=("QC", "Thickness")),
            _var("width", "mm", 49.5, 50.5),
            _var("moisture", "%", 0.0, 0.4),
            _var("melt_flow_index", "g/10min", 2.0, 4.0),
            _var("cut_length", "m", 5.98, 6.02),
            _cat("surface_defect_code", ["NONE", "DIE_LINE", "BUBBLE", "SCRATCH", "DISCOLOUR"]),
        ],
    },
    "PAINT": {
        "area": "Paint", "material": ("Painted Panel", "ea"),
        "stations": [
            ("Prep", 20, 0.3, [_a("BlastPressure", 6.0, 0.2, "bar")]),
            ("Prime", 20, 0.4, [_a("FilmBuild", 25.0, 1.5, "um")]),
            ("Topcoat", 20, 0.5, [_a("FilmBuild", 60.0, 3.0, "um"), _a("BoothHumidity", 55.0, 3.0, "%")]),
            ("Cure", 20, 0.1, [_a("OvenTemp", 160.0, 2.0, "C", **_sp("OvenTempSP", 140.0, 180.0, 180.0))]),
            ("Inspect", 20, 0.1, [_a("Gloss", 85.0, 2.0, "GU")]),
        ],
        "events": [
            {"type": "drift", "station": "Cure", "analog": "OvenTemp", "start": 800, "end": 1100, "to": 148.0,
             "effects": [{"station": "Inspect", "analog": "Gloss", "offset": -9.0}]},
            {"type": "down", "station": "Topcoat", "start": 2200, "end": 2260},
        ],
        "characteristics": [
            _var("dry_film_thickness", "um", 50.0, 70.0, measured=("Topcoat", "FilmBuild")),
            _var("gloss", "GU", 80.0, 90.0, measured=("Inspect", "Gloss")),
            _var("colour_delta_e", "dE", 0.0, 1.0),
            _cat("paint_defect_code", ["NONE", "RUN", "SAG", "DIRT", "ORANGE_PEEL", "FISHEYE"]),
            _ord("adhesion_grade", 1, 5),
        ],
    },
    "PACK": {
        "area": "Packing", "material": ("Shipping Case", "ea"),
        "stations": [
            ("Case", 30, 0.2, [_a("CaseRate", 30.0, 1.0, "cpm")]),
            ("Shrink", 30, 0.3, [_a("TunnelTemp", 180.0, 3.0, "C")]),
            ("Palletize", 28, 0.1, [_a("StackHeight", 1.4, 0.05, "m")]),
            ("Wrap", 28, 0.1, [_a("FilmTension", 4.0, 0.2, "N")]),
            ("Label", 28, 0.2, [_a("PrintDensity", 92.0, 1.5, "%")]),
            ("Ship", 26, 0.0, [_a("DockTemp", 21.0, 0.5, "C")]),
        ],
        "events": [
            {"type": "down", "station": "Shrink", "start": 1000, "end": 1070},
            {"type": "micro_stops", "station": "Label"},
            {"type": "drift", "station": "Label", "start": 1900, "end": 2100, "to": 80.0},
        ],
        "characteristics": [
            _var("case_weight", "kg", 11.8, 12.2),
            _var("seal_strength", "N", 12.0, 20.0),
            _var("pallet_height", "m", 1.3, 1.5),
            _gng("label_present"),
            _pf("barcode_scan"),
            _cnt("wrap_layers_short", 0),
        ],
    },
    "MOLD": {
        "area": "Moulding", "material": ("Moulded Housing", "ea"),
        "stations": [
            ("Dry", 15, 0.1, [_a("DryerTemp", 80.0, 1.0, "C")]),
            ("Mold", 15, 0.6, [_a("MeltTemp", 230.0, 2.5, "C", **_sp("MeltTempSP", 210.0, 250.0, 150.0)),
                               _a("InjectionPressure", 850.0, 15.0, "bar")]),
            ("Cool", 15, 0.1, [_a("MoldTemp", 40.0, 1.0, "C")]),
            ("Trim", 15, 0.3, [_a("BladeSpeed", 700.0, 15.0, "rpm")]),
            ("Inspect", 15, 0.1, [_a("PartWeight", 42.0, 0.3, "g")]),
        ],
        "events": [
            {"type": "drift", "station": "Mold", "analog": "MeltTemp", "start": 600, "end": 900, "to": 246.0,
             "effects": [{"station": "Inspect", "analog": "PartWeight", "offset": -1.1}]},
            {"type": "down", "station": "Dry", "start": 1500, "end": 1560},
            {"type": "scrap_burst", "station": "Trim", "start": 2300, "end": 2350, "scrap_pct": 12},
        ],
        "characteristics": [
            _var("part_weight", "g", 41.0, 43.0, measured=("Inspect", "PartWeight")),
            _var("dimension_a", "mm", 119.8, 120.2),
            _var("dimension_b", "mm", 79.85, 80.15),
            _pf("short_shot"),
            _pf("flash"),
            _cnt("sink_mark_count", 1),
            _cat("colour_match", ["OK", "LIGHT", "DARK", "STREAK"]),
        ],
    },
}

# How many lines of each kind. 3+3+3+2+2+2+2+3 = 20 lines,
# 18+15+18+10+10+10+12+15 = 108 stations.
INSTANCES = {"FILL": 3, "MACH": 3, "ASSEM": 3, "WELD": 2, "EXTRUDE": 2, "PAINT": 2, "PACK": 2, "MOLD": 3}


def _shift(event: dict, offset: int) -> dict:
    moved = json.loads(json.dumps(event))
    for key in ("start", "end", "at"):
        if key in moved:
            moved[key] = int(moved[key]) + offset
    return moved


def build() -> dict:
    lines = []
    for t_index, (template, count) in enumerate(INSTANCES.items()):
        spec = TEMPLATES[template]
        for k in range(1, count + 1):
            name = f"{template}{k}"
            offset = (k - 1) * OFFSET_S
            events = [_shift(e, offset) for e in spec["events"]]
            for e in events:
                for key in ("start", "end"):
                    if key in e and e[key] >= CHANGEOVER["start"]:
                        raise ValueError(f"{name}: {e['type']} {key}={e[key]} runs into the changeover window")
            events.append(dict(CHANGEOVER))
            mat_name, mat_unit = spec["material"]
            lines.append({
                "name": name,
                "seed": SEED + 100 * t_index + k,
                "duration_s": DURATION_S,
                "stations": [
                    {"name": s, "rate_per_min": rate, "scrap_pct": scrap, "analogs": analogs}
                    for s, rate, scrap, analogs in spec["stations"]
                ],
                "events": events,
                "_meta": {
                    "template": template,
                    "area": spec["area"],
                    "material": {"code": f"FG-{name}", "name": f"{mat_name} ({name})", "unit": mat_unit},
                    "characteristics": spec["characteristics"],
                },
            })
    return {
        "_what": f"The full mega-factory: {len(lines)} lines from {len(TEMPLATES)} templates, "
                 f"{sum(len(ln['stations']) for ln in lines)} stations, one plant. Built by "
                 "build_config.py - edit that, not this.",
        "lines": lines,
    }


def characteristic_kinds(config: dict) -> dict[str, set[str]]:
    """Distinct characteristic names by measurement kind, across the plant."""
    kinds: dict[str, set[str]] = {}
    for line in config["lines"]:
        for c in line["_meta"]["characteristics"]:
            kinds.setdefault(c["kind"], set()).add(c["name"])
    return kinds


if __name__ == "__main__":
    out = Path(__file__).parent / "line.json"
    config = build()
    out.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    kinds = characteristic_kinds(config)
    n_stations = sum(len(ln["stations"]) for ln in config["lines"])
    distinct = sum(len(v) for v in kinds.values())
    print(f"wrote {out} ({len(config['lines'])} lines, {n_stations} stations, "
          f"{distinct} distinct characteristics: "
          + ", ".join(f"{k} {len(v)}" for k, v in sorted(kinds.items())) + ")")
