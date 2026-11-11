"""No cutlery station runs faster than its rated cycle (the crew's lab, 2026-11-11).

Mold runs 1.2 % above its moulding line's rate and Degate 0.2 % below, on
purpose. A tag map that rated all three at the line's 0.026 s - the cycle
rounded up - let every Mold count more than its rating allows, and the OEE
rule withholds a Performance above 100 % (decision 0026), so nine machines
had no OEE. Each station's rated cycle times its own rate must be at most
60 s: at the customer's rate and at the lab's hundredth.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

CUTLERY = Path(__file__).resolve().parents[1] / "labs" / "cutlery"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"cutlery_{name}", CUTLERY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rates(config: dict) -> dict[str, float]:
    return {f"{line['name']}_{s['name']}": s["rate_per_min"]
            for line in config["lines"] for s in line["stations"]}


def _stations_rated_slower_than_they_run(config: dict, tag_map: dict) -> list[str]:
    rates = _rates(config)
    cycles = {m["equipment"]: m["cycle_seconds"] for m in tag_map["machines"]}
    assert set(cycles) == set(rates)
    assert len(cycles) == 83
    return [f"{name}: {rates[name]}/min x {cycles[name]} s = {rates[name] * cycles[name]:.4f} s"
            for name in rates if rates[name] * cycles[name] > 60.0]


def test_the_committed_tag_map_rates_no_station_slower_than_it_runs():
    config = json.loads((CUTLERY / "line.json").read_text(encoding="utf-8"))
    tag_map = json.loads((CUTLERY / "tag_map.json").read_text(encoding="utf-8"))
    assert _stations_rated_slower_than_they_run(config, tag_map) == []


@pytest.mark.parametrize("scale", [1.0, 0.01])
def test_a_tag_map_derived_at_any_scale_rates_no_station_slower_than_it_runs(tmp_path, scale):
    build_config, make_tag_map = _module("build_config"), _module("make_tag_map")
    build_config.main(["--scale", str(scale), "--out", str(tmp_path)])
    config = json.loads((tmp_path / "line.json").read_text(encoding="utf-8"))
    tag_map = make_tag_map.build(tmp_path / "line.json")
    assert _stations_rated_slower_than_they_run(config, tag_map) == []


def test_each_station_of_a_moulding_line_is_rated_at_its_own_speed_within_a_thousandth():
    config = json.loads((CUTLERY / "line.json").read_text(encoding="utf-8"))
    tag_map = json.loads((CUTLERY / "tag_map.json").read_text(encoding="utf-8"))
    rates = _rates(config)
    for machine in tag_map["machines"]:
        exact = 60.0 / rates[machine["equipment"]]
        assert exact * 0.999 <= machine["cycle_seconds"] <= exact, machine["equipment"]
    fork1 = {m["equipment"]: m["cycle_seconds"] for m in tag_map["machines"] if m["equipment"].startswith("FORK1_")}
    assert fork1["FORK1_Mold"] < fork1["FORK1_Mark"] < fork1["FORK1_Degate"]
