"""The gauge-study film runs against a plant brought up by hand, so the
recorder's own port check never sees it. This test does: the port the scene
films and the ports the cutlery pack will listen on are none of the ports
the recorder refuses. The list is the recorder's, read from it, not a copy
(tests/test_one_list_of_ports_that_are_not_ours.py)."""

import importlib.util
import tomllib
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RECORDER = REPO / "scripts" / "record-scene.py"
SCENE = REPO / "scripts" / "scenes" / "film" / "04-gauge-or-process.toml"


def _recorder():
    spec = importlib.util.spec_from_file_location("record_scene_film_ports", RECORDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_film_and_its_plant_take_no_port_the_recorder_refuses():
    recorder = _recorder()
    scene = tomllib.loads(SCENE.read_text(encoding="utf-8"))
    base = urllib.parse.urlsplit(scene["base"])
    assert base.hostname == "127.0.0.1"
    ports = {base.port, *recorder.ports_of_pack(REPO / "labs" / "cutlery")}
    assert ports == {8040, 4844}
    taken = ports & set(recorder.PORTS_THAT_BELONG_TO_SOMETHING_ELSE)
    assert not taken, f"the film would bind {sorted(taken)}"


def test_the_film_writes_into_its_own_folder_and_nowhere_else():
    scene = tomllib.loads(SCENE.read_text(encoding="utf-8"))
    assert scene["out"].startswith("~/Work/gifs/strategy-gage-rr/")
    assert "simulated plant" in scene["badge"]
