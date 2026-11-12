"""The cutlery plant at a pace a person can watch (the crew's lab, 2026-11-10).

`labs/cutlery/build_config.py --scale 0.01` is the same plant - every station,
attribute, stop and changeover - with every rate a hundredth of the
customer's. The scale is a knob the lab turns; the default is 1, so a scored
run builds the full-rate plant exactly as it did.

Since 2026-11-12 the committed `line.json` and `tag_map.json` are the
hundredth-scale build: they ship in the wheel as `fsmes demo cutlery`, the
plant a person can watch (`masterdata/README.md`).
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

CUTLERY = Path(__file__).resolve().parents[1] / "labs" / "cutlery"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"cutlery_{name}", CUTLERY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_config = _module("build_config")


def _rates(config: dict) -> dict[str, float]:
    return {f"{line['name']}_{s['name']}": s["rate_per_min"]
            for line in config["lines"] for s in line["stations"]}


def test_the_committed_line_json_is_the_hundredth_scale_build_byte_for_byte():
    committed = (CUTLERY / "line.json").read_text(encoding="utf-8")
    assert json.dumps(build_config.build(0.01), indent=2) + "\n" == committed


def test_a_hundredth_scale_is_the_same_plant_with_every_rate_a_hundredth():
    full, slow = build_config.build(), build_config.build(0.01)
    full_rates, slow_rates = _rates(full), _rates(slow)
    assert list(slow_rates) == list(full_rates)
    assert len(slow_rates) == 83
    for station, rate in full_rates.items():
        assert slow_rates[station] == pytest.approx(rate / 100, abs=0.006), station
    # Everything but the rates is the same plant: stops, changeover, attributes, buffers.
    for a, b in zip(full["lines"], slow["lines"], strict=True):
        assert a["events"] == b["events"]
        assert a.get("buffers") == b.get("buffers")
        assert [s.get("inspection") for s in a["stations"]] == [s.get("inspection") for s in b["stations"]]
    assert slow["_plant"]["pieces_per_type_per_day"] == 100_000
    assert slow["_plant"]["pallets_per_day"] == 416
    assert slow["_plant"]["scale"] == 0.01
    assert "scale" not in full["_plant"]


def _build(where: Path, *scale: str) -> subprocess.CompletedProcess:
    made = subprocess.run([sys.executable, str(CUTLERY / "build_config.py"), *scale, "--out", str(where)],
                          capture_output=True, text=True, encoding="utf-8", check=True)
    subprocess.run([sys.executable, str(CUTLERY / "make_tag_map.py"), str(where)],
                   capture_output=True, text=True, encoding="utf-8", check=True)
    return made


def test_the_lab_builds_its_slow_plant_into_its_own_folder_and_leaves_the_checkout_alone(tmp_path):
    before = (CUTLERY / "line.json").read_bytes(), (CUTLERY / "tag_map.json").read_bytes()
    made = _build(tmp_path, "--scale", "0.01")
    assert "scale 0.01" in made.stdout
    _build(tmp_path / "full")
    line = json.loads((tmp_path / "line.json").read_text(encoding="utf-8"))
    tags = json.loads((tmp_path / "tag_map.json").read_text(encoding="utf-8"))
    assert line["_plant"]["scale"] == 0.01
    assert len(tags["machines"]) == 83
    # A slower station takes longer per piece: the tag map's ideal cycle follows the rate.
    full = {m["equipment"]: m["cycle_seconds"]
            for m in json.loads((tmp_path / "full" / "tag_map.json").read_text(encoding="utf-8"))["machines"]}
    slow = {m["equipment"]: m["cycle_seconds"] for m in tags["machines"]}
    assert slow["FORK1_Mark"] == pytest.approx(full["FORK1_Mark"] * 100, rel=0.01)
    assert ((CUTLERY / "line.json").read_bytes(), (CUTLERY / "tag_map.json").read_bytes()) == before


def test_the_scale_comes_from_the_environment_when_no_flag_says_it(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTLERY_SCALE", "0.01")
    target = build_config.main(["--out", str(tmp_path)])
    assert json.loads(target.read_text(encoding="utf-8"))["_plant"]["pieces_per_type_per_day"] == 100_000


@pytest.mark.parametrize("said", ["0", "-1", "2", "fast"])
def test_a_scale_that_is_not_a_slower_rate_is_refused_with_a_reason(tmp_path, said):
    with pytest.raises(SystemExit) as refused:
        build_config.main(["--scale", said, "--out", str(tmp_path)])
    assert "scale" in str(refused.value)
    assert not (tmp_path / "line.json").exists()
