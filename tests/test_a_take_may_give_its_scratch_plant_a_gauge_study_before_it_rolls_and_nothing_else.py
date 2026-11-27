"""A take may give its scratch plant a gauge study before it rolls, and nothing else.

`[plant] before_roll` is the recorder's way to put work on a scratch plant that
people do off the line and the simulated shift never does - a gauge study -
before the camera starts. Each entry is the words after `fsmes`; only commands
on a short list may run, the recorder adds the plant's own `--base`, and a
scene that names another base is refused. Class 4.4 is the first to use it.
No plant: the command is caught where the recorder would run it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CLASS_4_4 = REPO / "scripts" / "scenes" / "course" / "05-04-gauges-and-calibration.toml"


def scene_file(tmp_path: Path, before_roll: str) -> Path:
    path = tmp_path / "scene.toml"
    path.write_text(
        'name = "x"\n[[step]]\ndo = "hold"\nseconds = 1\n'
        f'\n[plant]\npack = "labs/cutlery"\nbefore_roll = {before_roll}\n',
        encoding="utf-8",
    )
    return path


def test_a_scene_without_it_gives_its_plant_nothing_before_it_rolls(runner):
    assert runner.plant_defaults({"pack": "labs/cutlery"})["before_roll"] == []


def test_a_gauge_study_is_a_command_a_take_may_run_before_it_rolls(runner, tmp_path):
    scene = runner.load_scene(scene_file(tmp_path, '[["sim-gage-study", "labs/cutlery/gage_studies.json", "all"]]'))

    assert scene["plant"]["before_roll"] == [["sim-gage-study", "labs/cutlery/gage_studies.json", "all"]]


@pytest.mark.parametrize(
    ("before_roll", "complaint"),
    [
        ('[["fleet", "stop", "cutlery"]]', "not a command a take may run before it rolls"),
        ('[["plant", "cutlery", "reset"]]', "not a command a take may run before it rolls"),
        ('"sim-gage-study labs/cutlery/gage_studies.json all"', "is a list of commands"),
        ('[[]]', "as a list of strings"),
        ('[["sim-gage-study", "x.json", "all", "--base", "http://127.0.0.1:8110"]]', "names no `--base`"),
        ('[["sim-gage-study", "x.json", "all", "--base=http://127.0.0.1:8110"]]', "names no `--base`"),
    ],
)
def test_anything_else_and_any_other_plant_is_refused_before_a_plant_starts(runner, tmp_path, before_roll, complaint):
    with pytest.raises(SystemExit) as refused:
        runner.load_scene(scene_file(tmp_path, before_roll))
    assert complaint in str(refused.value)


def scratch(runner) -> object:
    return runner.ScratchPlant("cutlery", Path("unused"), {"FSMES_PLANT_REGISTRY": "fleet.toml"}, {})


def test_it_runs_against_the_takes_own_plant_with_that_plants_registry(runner, monkeypatch):
    ran: list[dict] = []

    def run(argv, **how):
        ran.append({"argv": argv, **how})
        return subprocess.CompletedProcess(argv, 0, stdout="GS-GOOD-CAL matches its answer key\n", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", run)

    out = scratch(runner).before_roll(["sim-gage-study", "labs/cutlery/gage_studies.json", "all"],
                                      "http://127.0.0.1:8040")

    assert "answer key" in out
    (one,) = ran
    assert one["argv"] == [sys.executable, "-m", "fsmes", "sim-gage-study", "labs/cutlery/gage_studies.json", "all",
                           "--base", "http://127.0.0.1:8040"]
    assert one["env"] == {"FSMES_PLANT_REGISTRY": "fleet.toml"}
    assert one["cwd"] == str(runner.REPO)
    # Read as UTF-8 whatever the console's code page, as every child here is.
    assert one["encoding"] == "utf-8"


def test_a_command_that_fails_stops_the_take_before_anything_is_filmed(runner, monkeypatch):
    monkeypatch.setattr(runner.subprocess, "run", lambda argv, **how: subprocess.CompletedProcess(
        argv, 1, stdout="", stderr="no material UT-KNIFE"))

    with pytest.raises(SystemExit) as stopped:
        scratch(runner).before_roll(["sim-gage-study", "labs/cutlery/gage_studies.json", "all"], "http://127.0.0.1:8040")
    assert "failed before the take rolled" in str(stopped.value)
    assert "no material UT-KNIFE" in str(stopped.value)


def test_the_folder_says_what_was_run_before_the_camera_started(runner):
    scene = runner.load_scene(CLASS_4_4)
    page = runner.readme(scene, {
        "said": runner.script_lines(scene), "plant": "cutlery", "version": "0.4.2",
        "badge": "pre-alpha · simulated plant · v0.4.2", "base": scene["base"], "commit": "abc1234",
        "scene_file": CLASS_4_4.relative_to(REPO).as_posix(), "command": "python scripts/record-scene.py …",
        "when": "2026-11-27 17:00 CST", "gif_seconds": 30.0, "gif_bytes": 4_000_000,
        "gif_setting": "10 fps, 1100 wide",
    })

    assert ("**before the camera started**: `fsmes sim-gage-study labs/cutlery/gage_studies.json all`"
            " was run against the plant, off camera") in page
