"""A sweep varies the line, so every variant must replay its own data.

`fsmes sweep` generated a fresh hour of line data per variant and then started
each plant from the compiled configuration with one key swapped. `replay_dir`
moved; `MES_REPLAY_DIR`, which is compiled out of `plant.toml` and is the one
the replay process actually reads, did not. Every variant replayed the pack's
original hour, and a sweep that printed three identical rows was not a line
that did not care about the knob - it was one run, read three times.

Nothing here starts a plant. The question is which directory the replay is
pointed at, and that is settled before any process is forked.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fsmes import plant as plants
from fsmes.cli import app
from fsmes.pack import fleet, repoint
from fsmes.pack import format as fmt
from fsmes.sim.generate import generate

LINE = {
    "channel": "Tiny",
    "seed": 3,
    "duration_s": 120,
    "orders": [900, 901],
    "buffers": {"capacity": 10, "initial": 5},
    "stations": [
        {"name": "Cut", "rate_per_min": 60, "scrap_pct": 0.0},
        {"name": "Pack", "rate_per_min": 55, "scrap_pct": 0.0},
    ],
    "events": [{"type": "down", "station": "Cut", "start": 30, "end": 60}],
}

PLANT_TOML = """
[pack]
format = 1
requires = ">=0.1.2"

[plant]
name = "tiny"
label = "A tiny plant"
timezone = "UTC"

[serve]
api_host = "127.0.0.1"
api_port = 9099
simulate = true

[files]
tag_map = "tag_map.json"
replay_dir = "out"
masterdata = "masterdata"
"""

TAG_MAP = {
    "machines": [
        {"equipment": "CUT01", "object": "Cut", "cycle_seconds": 1.0, "analog": "Value",
         "order_tag": None,
         "state_map": {"0": "idle", "1": "running", "2": "idle", "3": "idle",
                       "4": "down", "5": "setup"}},
        {"equipment": "PACK01", "object": "Pack", "cycle_seconds": 1.0, "analog": "Value",
         "order_tag": None,
         "state_map": {"0": "idle", "1": "running", "2": "idle", "3": "idle",
                       "4": "down", "5": "setup"}},
    ],
}

EQUIPMENT = [
    {"code": "TINY", "name": "Tiny", "level": "enterprise"},
    {"code": "T1", "name": "Tiny Site", "level": "site", "parent": "TINY"},
    {"code": "TA", "name": "Tiny Area", "level": "area", "parent": "T1"},
    {"code": "TLINE", "name": "Tiny Line", "level": "work_center", "parent": "TA"},
    {"code": "CUT01", "name": "Cut 01", "level": "work_unit", "parent": "TLINE"},
    {"code": "PACK01", "name": "Pack 01", "level": "work_unit", "parent": "TLINE"},
]


def _pack(directory: Path) -> Path:
    """A pack whose line can be swept: a description, a tag map, master data,
    and the generated data a pack normally points at."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "plant.toml").write_text(PLANT_TOML, encoding="utf-8")
    (directory / "tag_map.json").write_text(json.dumps(TAG_MAP), encoding="utf-8")
    (directory / "line.json").write_text(json.dumps(LINE), encoding="utf-8")
    data = directory / "masterdata"
    data.mkdir(exist_ok=True)
    (data / "equipment.json").write_text(json.dumps(EQUIPMENT), encoding="utf-8")
    generate(directory / "line.json", directory / "out", write_docs=False)
    return directory


def _fleet(root: Path) -> Path:
    """A repository-shaped root: a fleet file listing one pack."""
    where = root / "labs" / "multiplant"
    _pack(where / "tiny")
    (where / "fleet.toml").write_text('packs = ["tiny"]\n', encoding="utf-8")
    return root


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.delenv(plants.REGISTRY_ENV, raising=False)
    plants.environment.cache_clear()
    return _fleet(tmp_path)


def _card(plant: str) -> dict:
    return {"plant": plant, "seed": 3, "duration_s": 120, "speed": 60.0,
            "metrics": {"breakdown_recall": 1.0, "planned_stop_misclassified": 0,
                        "faults_scored": 1, "faults_scripted": 1},
            "faults": [{"lag_sim_seconds": 2.0}]}


def test_each_swept_variant_replays_the_data_generated_for_it(root, monkeypatch):
    # What the replay process would be told, per variant, read off the same
    # environment `fsmes.sim.runner` hands the processes it starts.
    pointed_at: list[str] = []

    def fake_scored_run(name, cfg, where, speed=60.0, line_json=None, echo=print, **kw):
        env = plants.plant_env(name, cfg, where, speed)
        pointed_at.append(env["MES_REPLAY_DIR"])
        card = _card(name)
        card["replay_dir"] = env["MES_REPLAY_DIR"]
        return card

    monkeypatch.setattr("fsmes.sim.runner.scored_run", fake_scored_run)
    monkeypatch.setattr("fsmes.sim.store.record", lambda *a, **k: 1)

    result = CliRunner().invoke(
        app, ["sweep", "tiny", "-k", "seed=11,22", "--root", str(root)])
    assert result.exit_code == 0, result.output

    assert len(pointed_at) == 2, result.output
    first, second = (Path(p) for p in pointed_at)
    # Two variants, two directories - and neither of them the pack's own.
    assert first != second
    assert (root / "labs" / "multiplant" / "tiny" / "out") not in (first, second)

    # And the two directories hold different hours, so the comparison is of
    # two runs rather than of one run read twice.
    def rows(where: Path) -> str:
        return "".join(sorted(p.read_text(encoding="utf-8")
                              for p in sorted(where.glob("*.csv"))))

    assert rows(first) and rows(second)
    assert rows(first) != rows(second)


def test_the_sweeps_table_names_the_data_each_variant_replayed(root, monkeypatch):
    def fake_scored_run(name, cfg, where, speed=60.0, line_json=None, echo=print, **kw):
        card = _card(name)
        card["replay_dir"] = plants.plant_env(name, cfg, where, speed)["MES_REPLAY_DIR"]
        return card

    monkeypatch.setattr("fsmes.sim.runner.scored_run", fake_scored_run)
    monkeypatch.setattr("fsmes.sim.store.record", lambda *a, **k: 1)
    out = root / "sweep.json"

    result = CliRunner().invoke(
        app, ["sweep", "tiny", "-k", "seed=11,22", "--root", str(root), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "data replayed:" in result.output

    written = json.loads(out.read_text(encoding="utf-8"))
    dirs = [row["replay_dir"] for row in written["runs"]]
    assert len(set(dirs)) == 2
    for directory in dirs:
        # Printed, not just recorded: the reader of the table is who needs it.
        assert directory in result.output


def test_pointing_a_pack_at_new_data_moves_the_setting_the_replay_actually_reads(root, tmp_path):
    pack_dir = root / "labs" / "multiplant" / "tiny"
    elsewhere = tmp_path / "another-hour"
    generate(pack_dir / "line.json", elsewhere, write_docs=False)

    cfg = fleet.compile_pack(fmt.read(pack_dir))
    # The way the sweep used to do it. `replay_dir` moves and the plant does
    # not: this assertion is the bug, pinned so it cannot come back quietly.
    patched = dict(cfg, replay_dir=str(elsewhere))
    assert plants.plant_env("tiny", patched, root)["MES_REPLAY_DIR"] != elsewhere.as_posix()

    copy, _ = repoint.pointed_at(fmt.read(pack_dir), tmp_path / "copy", elsewhere)
    rebuilt = fleet.compile_pack(fmt.read(copy))
    assert rebuilt["replay_dir"] == elsewhere.as_posix()
    assert plants.plant_env("tiny", rebuilt, root)["MES_REPLAY_DIR"] == elsewhere.as_posix()
    # The pack on disk is untouched, so a sweep leaves the plant as it found it.
    assert fleet.compile_pack(fmt.read(pack_dir))["replay_dir"] == (pack_dir / "out").as_posix()
