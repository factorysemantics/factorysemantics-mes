"""Build labs/megafactory/line.json: a 5-line, 27-station factory config.

Run once from the repo root:  python3 labs/megafactory/build_config.py

Deliberately data, not a hand-typed JSON blob - loops over line/station
definitions below so the shape stays legible and it is trivial to add a
sixth line later for the full 100-machine build.
"""
import json
from pathlib import Path

DURATION_S = 3600
SEED = 4242

# (line_name, [(station_name, rate_per_min, scrap_pct, analogs), ...])
LINES = [
    ("FILL", [
        ("Loader", 40, 0.3, [{"name": "InfeedRate", "base": 40.0, "noise": 2.0, "unit": "bpm"}]),
        ("Washer", 40, 0.2, [{"name": "WashTemp", "base": 65.0, "noise": 1.0, "unit": "C",
                              "setpoint": {"name": "WashTempSP", "min": 55.0, "max": 80.0, "lag_s": 90.0}}]),
        ("Filler", 38, 0.5, [{"name": "FillWeight", "base": 500.0, "noise": 1.5, "unit": "g"}]),
        ("Capper", 38, 0.4, [{"name": "TorqueLevel", "base": 12.0, "noise": 0.6, "unit": "Nm"}]),
        ("Labeler", 38, 0.3, [{"name": "LabelTension", "base": 3.0, "noise": 0.2, "unit": "N"}]),
        ("Palletizer", 36, 0.2, [{"name": "StackHeight", "base": 1.2, "noise": 0.05, "unit": "m"}]),
    ]),
    ("MACH", [
        ("Saw", 22, 0.8, [{"name": "BladeLoad", "base": 42.0, "noise": 2.5, "unit": "%"}]),
        ("Mill", 18, 0.6, [{"name": "SpindleTemp", "base": 55.0, "noise": 3.0, "unit": "C"}]),
        ("Deburr", 25, 0.4, [{"name": "CycleForce", "base": 30.0, "noise": 1.5, "unit": "N"}]),
        ("Inspect", 24, 0.1, [{"name": "GaugeReading", "base": 10.0, "noise": 0.1, "unit": "mm"}]),
        ("Pack", 24, 0.1, [{"name": "SealTemp", "base": 120.0, "noise": 2.0, "unit": "C"}]),
    ]),
    ("ASSEM", [
        ("Kit", 30, 0.2, [{"name": "PickRate", "base": 30.0, "noise": 1.0, "unit": "ppm"}]),
        ("Assemble1", 28, 0.5, [{"name": "PressForce", "base": 200.0, "noise": 8.0, "unit": "N"}]),
        ("Assemble2", 28, 0.6, [{"name": "PressForce", "base": 210.0, "noise": 8.0, "unit": "N"}]),
        ("Torque", 27, 0.3, [{"name": "TorqueLevel", "base": 8.0, "noise": 0.4, "unit": "Nm",
                              "setpoint": {"name": "TorqueLevelSP", "min": 5.0, "max": 12.0, "lag_s": 20.0}}]),
        ("Test", 27, 0.2, [{"name": "TestCurrent", "base": 1.2, "noise": 0.05, "unit": "A"}]),
        ("Pack2", 27, 0.1, [{"name": "SealTemp", "base": 118.0, "noise": 2.0, "unit": "C"}]),
    ]),
    ("WELD", [
        ("Cut", 20, 0.4, [{"name": "FeedRate", "base": 15.0, "noise": 0.8, "unit": "m/min"}]),
        ("Weld1", 16, 0.9, [{"name": "ArcCurrent", "base": 180.0, "noise": 6.0, "unit": "A"}]),
        ("Weld2", 16, 0.9, [{"name": "ArcCurrent", "base": 185.0, "noise": 6.0, "unit": "A"}]),
        ("Grind", 19, 0.5, [{"name": "MotorTemp", "base": 48.0, "noise": 2.0, "unit": "C"}]),
        ("Paint", 18, 0.3, [{"name": "BoothPressure", "base": 1.0, "noise": 0.05, "unit": "bar"}]),
    ]),
    ("EXTRUDE", [
        ("Extrude", 12, 0.7, [{"name": "MeltTemp", "base": 210.0, "noise": 3.0, "unit": "C",
                               "setpoint": {"name": "MeltTempSP", "min": 190.0, "max": 230.0, "lag_s": 120.0}}]),
        ("Cool", 12, 0.2, [{"name": "WaterTemp", "base": 18.0, "noise": 0.5, "unit": "C"}]),
        ("Trim", 12, 0.4, [{"name": "BladeSpeed", "base": 900.0, "noise": 20.0, "unit": "rpm"}]),
        ("QC", 12, 0.1, [{"name": "Thickness", "base": 2.0, "noise": 0.05, "unit": "mm"}]),
        ("Pack3", 12, 0.1, [{"name": "SealTemp", "base": 115.0, "noise": 2.0, "unit": "C"}]),
    ]),
]

# (line_name, [event, ...]) - kept modest: enough variety to exercise every
# metric the scorer names (planned-stop misclassification, breakdown recall,
# detection lag) without hand-authoring dozens of events per line.
EVENTS = {
    "FILL": [
        {"type": "drift", "station": "Washer", "start": 1500, "end": 1800, "to": 74.0,
         "effects": [{"station": "Filler", "analog": "FillWeight", "offset": -9.0}]},
        {"type": "down", "station": "Capper", "start": 2400, "end": 2450},
        {"type": "changeover", "start": 3000, "end": 3060},
    ],
    "MACH": [
        {"type": "down", "station": "Mill", "start": 900, "end": 960},
        {"type": "drift", "station": "Saw", "start": 2000, "end": 2200, "to": 55.0},
        {"type": "micro_stops", "station": "Deburr"},
    ],
    "ASSEM": [
        {"type": "scrap_burst", "station": "Assemble2", "start": 1200, "end": 1260, "scrap_pct": 18},
        {"type": "down", "station": "Torque", "start": 2600, "end": 2700},
        {"type": "changeover", "start": 3200, "end": 3260},
    ],
    "WELD": [
        {"type": "drift", "station": "Weld1", "start": 500, "end": 700, "to": 210.0},
        {"type": "down", "station": "Grind", "start": 1800, "end": 1850},
    ],
    "EXTRUDE": [
        {"type": "down", "station": "Extrude", "start": 300, "end": 400},
        {"type": "micro_stops", "station": "Trim"},
        {"type": "changeover", "start": 2800, "end": 2860},
    ],
}


def build() -> dict:
    lines = []
    for name, stations in LINES:
        line_cfg = {
            "name": name,
            "seed": SEED,
            "duration_s": DURATION_S,
            "stations": [
                {"name": sname, "rate_per_min": rate, "scrap_pct": scrap, "analogs": analogs}
                for sname, rate, scrap, analogs in stations
            ],
            "events": EVENTS.get(name, []),
        }
        lines.append(line_cfg)
    return {
        "_what": "The mega-factory scale spike: 5 lines, 27 stations, one "
                 "shared workforce and quality system. Built by "
                 "build_config.py rather than hand-typed - see full/build_config.py "
                 "for the 108-station factory built from line templates.",
        "lines": lines,
    }


if __name__ == "__main__":
    out = Path(__file__).parent / "line.json"
    out.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    n_stations = sum(len(s) for _, s in LINES)
    print(f"wrote {out} ({len(LINES)} lines, {n_stations} stations)")
