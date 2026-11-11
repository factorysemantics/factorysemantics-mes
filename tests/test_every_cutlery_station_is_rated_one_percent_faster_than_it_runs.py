"""Every cutlery station is rated 1 % faster than it runs (the crew's lab, 2026-11-11).

Mold runs 1.2 % above its moulding line's rate and Degate 0.2 % below, on
purpose. A tag map that rated all three at the line's 0.026 s - the cycle
rounded up - let every Mold count more than its rating allows, and the OEE
rule withholds a Performance above 100 % (decision 0026), so nine machines
had no OEE. Rated at exactly its own speed, a station still flickered over
100 % on a fresh plant: its first second of counts arrives before the MES has
recorded it running. So each station is rated 1 % above the speed it is set
to run at (Scott, the same day), as a real line runs a little under its
nameplate: rate x rated cycle is about 59.4 s - Performance about 99 % -
and never 60 s. At the customer's rate and at the lab's hundredth.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

CUTLERY = Path(__file__).resolve().parents[1] / "labs" / "cutlery"

# 99 % of 60 s, less what five significant figures rounded down can take off.
LOWEST, HIGHEST = 59.39, 59.40


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"cutlery_{name}", CUTLERY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rates(config: dict) -> dict[str, float]:
    return {f"{line['name']}_{s['name']}": s["rate_per_min"]
            for line in config["lines"] for s in line["stations"]}


def _stations_off_the_margin(config: dict, tag_map: dict) -> list[str]:
    rates = _rates(config)
    cycles = {m["equipment"]: m["cycle_seconds"] for m in tag_map["machines"]}
    assert set(cycles) == set(rates)
    assert len(cycles) == 83
    return [f"{name}: {rates[name]}/min x {cycles[name]} s = {rates[name] * cycles[name]:.4f} s"
            for name in rates if not LOWEST <= rates[name] * cycles[name] <= HIGHEST]


def test_the_committed_tag_map_rates_every_station_one_percent_faster_than_it_runs():
    config = json.loads((CUTLERY / "line.json").read_text(encoding="utf-8"))
    tag_map = json.loads((CUTLERY / "tag_map.json").read_text(encoding="utf-8"))
    assert _stations_off_the_margin(config, tag_map) == []


@pytest.mark.parametrize("scale", [1.0, 0.01])
def test_a_tag_map_derived_at_any_scale_rates_every_station_one_percent_faster_than_it_runs(tmp_path, scale):
    build_config, make_tag_map = _module("build_config"), _module("make_tag_map")
    build_config.main(["--scale", str(scale), "--out", str(tmp_path)])
    config = json.loads((tmp_path / "line.json").read_text(encoding="utf-8"))
    tag_map = make_tag_map.build(tmp_path / "line.json")
    assert _stations_off_the_margin(config, tag_map) == []


def test_each_station_of_a_moulding_line_is_rated_from_its_own_speed_not_the_lines():
    tag_map = json.loads((CUTLERY / "tag_map.json").read_text(encoding="utf-8"))
    fork1 = {m["equipment"]: m["cycle_seconds"] for m in tag_map["machines"] if m["equipment"].startswith("FORK1_")}
    assert fork1["FORK1_Mold"] < fork1["FORK1_Mark"] < fork1["FORK1_Degate"]
