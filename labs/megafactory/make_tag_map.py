"""Derive tag_map.json for the mega-factory from line.json, mechanically.

Every existing plant's tag_map.json is hand-authored (see
labs/multiplant/machining/tag_map.json) because `equipment`, `object` and
`cycle_seconds` carry business meaning a mechanical reader cannot invent for
a real PLC. For a *simulated* factory description they are entirely
derivable - the same site convention both existing tag maps already use
(state_map, cycle_seconds = 60 / rate_per_min) - so this script does that
derivation instead of hand-transcribing 27 stations. If line.json ever
changes, re-run this rather than hand-editing the output.

Run from the repo root: python3 labs/megafactory/make_tag_map.py
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from fsmes.sim.generate import _prefix_line, station_analogs  # noqa: E402

# Same site decision as bottling and machining: starved/blocked are IDLE,
# changeover is SETUP (planned, must not count against availability), only
# a genuine fault is DOWN.
STATE_MAP = {"0": "idle", "1": "running", "2": "idle", "3": "idle", "4": "down", "5": "setup"}


def build(line_json: Path) -> dict:
    raw = json.loads(line_json.read_text(encoding="utf-8"))
    machines = []
    for entry in raw["lines"]:
        prefixed = _prefix_line(entry["name"], entry)
        for station in prefixed["stations"]:
            primary = station_analogs(station)[0].get("name", "Value")
            rate = station.get("rate_per_min", 30)
            machines.append({
                "equipment": station["name"],
                "object": station["name"],
                "cycle_seconds": round(60.0 / rate, 3),
                "analog": primary,
                # A replayed CSV cannot be written to; production still
                # books because a counter delta auto-starts a released
                # operation - same read-only convention as machining.
                "order_tag": None,
                "state_map": STATE_MAP,
            })
    return {
        "_comment": "Mega-factory scale spike, derived mechanically from "
                    "line.json by make_tag_map.py - see build_config.py for "
                    "the factory description itself.",
        "_cycle_seconds": "60 / rate_per_min, same rule as every other plant.",
        "machines": machines,
        "_tags": "Each machine subscribes to everything its line publishes; "
                 "filled in from the generated manifest at load time.",
    }


if __name__ == "__main__":
    here = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
    out = here / "tag_map.json"
    result = build(here / "line.json")
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(result['machines'])} machines)")
