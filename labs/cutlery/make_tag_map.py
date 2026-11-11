"""Derive labs/cutlery/tag_map.json from line.json - the mega-factory's
mechanical derivation and the same state map every lab plant uses, with one
change: each station's cycle_seconds comes from its own rate_per_min, rated
1 % faster than the station is set to run, to five significant figures,
rounded down.

Why down (2026-11-11): Mold runs 1.2 % above its line's rate and Degate
0.2 % below, on purpose, so the buffers do something. The mega-factory
rounds to three decimals, which put all three stations of a moulding line at
0.026 s - slower than Mold really runs - so a Mold counted more than its
rating allows and its Performance was withheld as over 100 %. A rating
rounded down is never slower than the station, so rate x cycle <= 60 s.

Why 1 % faster (Scott, 2026-11-11): a station rated at exactly its own speed
has no slack, and a fresh plant's first second or two of counts arrive before
the MES has recorded the machine running, so it flickered over 100 %. A real
plant runs a little under nameplate; rating each station 1 % above its set
speed makes Performance read about 99 % and keeps it off the line.

    python3 labs/cutlery/make_tag_map.py
    python3 labs/cutlery/make_tag_map.py <dir>     the line.json in <dir>, tag_map.json beside it
"""
import importlib.util
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Loaded by path under its own name: this file and the mega-factory's share
# a module name, and a bare import would find whichever is first on the path.
_spec = importlib.util.spec_from_file_location("mf_make_tag_map", HERE.parent / "megafactory" / "make_tag_map.py")
_mf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mf)


SIGNIFICANT_FIGURES = 5

# The rated speed is this much above the speed the station is set to run at,
# the way a real line runs a little under its nameplate. 0.99 means each
# station's rated cycle is 99 % of its actual cycle, so Performance reads
# about 99 % instead of sitting exactly on 100 %, where a fresh plant's
# start-up second tips it over (see the module docstring).
RATED_CYCLE_FRACTION = 0.99


def rated_cycle_seconds(rate_per_min: float) -> float:
    """60 / rate x RATED_CYCLE_FRACTION, to five significant figures, rounded
    down, so the rating is a little faster than the station runs (rate x
    cycle about 59.4 s, never above it)."""
    exact = 60.0 / rate_per_min * RATED_CYCLE_FRACTION
    decimals = SIGNIFICANT_FIGURES - 1 - math.floor(math.log10(exact))
    factor = 10 ** decimals
    cycle = math.floor(exact * factor) / factor
    # Floating point can land a hair above the exact value; step down once.
    if cycle * rate_per_min > 60.0 * RATED_CYCLE_FRACTION:
        cycle = (math.floor(exact * factor) - 1) / factor
    return round(cycle, max(decimals, 0))


def build(line_json: Path) -> dict:
    result = _mf.build(line_json)
    raw = json.loads(line_json.read_text(encoding="utf-8"))
    rates = {f"{line['name']}_{s['name']}": s.get("rate_per_min", 30)
             for line in raw["lines"] for s in line["stations"]}
    for machine in result["machines"]:
        machine["cycle_seconds"] = rated_cycle_seconds(rates[machine["equipment"]])
    result["_cycle_seconds"] = ("60 / each station's own rate_per_min x 0.99 (rated 1 % above the "
                                "speed it is set to run at), five significant figures, rounded down.")
    result["_comment"] = ("The cutlery plant, derived mechanically from line.json by "
                          "make_tag_map.py - see build_config.py for the plant itself.")
    return result


if __name__ == "__main__":
    where = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE
    result = build(where / "line.json")
    (where / "tag_map.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {where / 'tag_map.json'} ({len(result['machines'])} machines)")
