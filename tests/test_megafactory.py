"""The full mega-factory (labs/megafactory/full): the shape the scale plan
asked for, the measurement kinds it refuses to dodge, and the two small
harness hooks that let a registry carry a scenario's own post-boot script.
"""

import importlib.util
import json
from pathlib import Path

import pytest

FULL = Path("labs/megafactory/full")
SPIKE = Path("labs/megafactory")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build_config():
    return _load(FULL / "build_config.py", "mf_build_config")


@pytest.fixture(scope="module")
def seed_breadth():
    return _load(FULL / "seed_breadth.py", "mf_seed_breadth")


@pytest.fixture(scope="module")
def factory(build_config):
    return build_config.build()


def test_the_full_factory_is_the_size_the_plan_asked_for(factory, tmp_path):
    from fsmes.sim.generate import load_factory_config
    from fsmes.sim.truth import load_truth

    make_tag_map = _load(SPIKE / "make_tag_map.py", "mf_make_tag_map")
    line = tmp_path / "line.json"
    line.write_text(json.dumps(factory), encoding="utf-8")
    tag_map = tmp_path / "tag_map.json"
    tag_map.write_text(json.dumps(make_tag_map.build(line)), encoding="utf-8")

    lines = load_factory_config(line)          # every line passes the single-line rules
    stations = sum(len(cfg["stations"]) for _, cfg in lines)
    assert len(lines) >= 20 and stations >= 100

    truth = load_truth(line, tag_map)
    assert len(truth["equipment"]) == stations
    downs = [e for e in truth["events"] if e.type == "down"]
    assert len(downs) >= 20, "enough scripted breakdowns that recall is a real fraction"
    assert len({e.equipment for e in downs}) == len(downs), "each breakdown on its own machine"
    # One plant-wide changeover: the scorer binds a changeover to every machine
    # (fsmes.sim.truth's known factory limitation), so the lines share it.
    windows = {(e.start_s, e.end_s) for e in truth["events"] if e.type == "changeover"}
    assert len(windows) == 1
    # Instances of a template do not break in lockstep.
    fills = [e for e in downs if e.equipment.startswith("FILL")]
    assert len({e.start_s for e in fills}) == len(fills)


def test_measurement_kinds_span_variable_and_attribute_data(build_config, factory, seed_breadth):
    kinds = build_config.characteristic_kinds(factory)
    assert sum(len(v) for v in kinds.values()) >= 30
    assert {"variable", "pass_fail", "go_nogo", "count", "categorical", "ordinal"} <= set(kinds)
    assert len(kinds["variable"]) < sum(len(v) for v in kinds.values()) * 0.7, \
        "a real factory's checks are not mostly variable data"

    specs = seed_breadth.specs_for(factory)
    verdicts = {s["kind"]: s["verdict"] for s in specs}
    assert verdicts["variable"].startswith("represented")
    assert verdicts["pass_fail"].startswith("encoded")
    assert verdicts["count"].startswith("encoded")
    assert verdicts["categorical"].startswith("flattened")
    # What the product is actually sent: a float against limits, nothing else.
    assert set(specs[0]["body"]) == {"unit", "min_value", "max_value"}
    categorical = next(s for s in specs if s["kind"] == "categorical")
    assert categorical["body"]["min_value"] is None and categorical["body"]["max_value"] is None


def test_the_workforce_is_at_least_two_hundred(seed_breadth, factory):
    people = seed_breadth.build_people(factory)
    assert len(people) >= 200
    assert len({p["code"] for p in people}) == len(people)
    assert len({p["role"] for p in people}) >= 5
    assert {p["shift"] for p in people} == {"DAY", "SWING", "NIGHT"}


def test_a_registry_post_boot_script_starts_beside_the_plant(tmp_path, monkeypatch):
    """`post_boot` in a registry entry is a script the plant runs once the
    API is up - the same hook scored_run offers in code, as data."""
    from fsmes import plant as plants

    launched: list[list[str]] = []

    class FakeProc:
        pid = 4242

        def __init__(self, cmd, **kwargs):
            launched.append(list(cmd))

    monkeypatch.setattr(plants.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(plants.time, "sleep", lambda *_: None)
    monkeypatch.setattr(plants, "fsmes_bin", lambda: "fsmes")
    monkeypatch.setattr(plants, "running_pids", lambda *_: [])
    monkeypatch.setenv("FSMES_PLANT_REGISTRY", str(tmp_path / "reg.toml"))
    (tmp_path / "reg.toml").write_text(
        f'[environment]\ndata_dir = "{(tmp_path / "data").as_posix()}"\n[plants]\n', encoding="utf-8")
    plants.environment.cache_clear()

    cfg = {"api_host": "127.0.0.1", "api_port": 8031, "opc_port": 4844, "tag_map": "t.json",
           "replay_dir": "out", "init": "init.py", "post_boot": "labs/x/seed.py"}
    plants.start("x", cfg, tmp_path, echo=lambda *_: None)
    scripts = [cmd[1] for cmd in launched if cmd[0] != "fsmes"]
    assert scripts == ["labs/x/seed.py"]
    assert [cmd[1] for cmd in launched if cmd[0] == "fsmes"] == [
        "run-opc-sim", "run-opc-agent", "run-api", "run-operations"]

    launched.clear()
    del cfg["post_boot"]
    plants.start("x", cfg, tmp_path, echo=lambda *_: None)
    assert len(launched) == 4, "a plant without one is unchanged"
    plants.environment.cache_clear()


def test_the_mcp_servers_follow_fsmes_root(tmp_path, monkeypatch):
    import importlib

    import fsmes.mcp_server as product
    import fsmes.sim.mcp_server as sim

    monkeypatch.setenv("FSMES_ROOT", str(tmp_path))
    try:
        expected = tmp_path.resolve()
        assert expected == importlib.reload(product).ROOT
        assert expected == importlib.reload(sim).ROOT
    finally:
        monkeypatch.delenv("FSMES_ROOT")
        importlib.reload(product)
        importlib.reload(sim)


def test_a_factory_scorecard_can_be_recorded(tmp_path):
    """A factory's seed is one per line. The results store once refused the
    list and took every factory scored through the CLI or the sim MCP down
    at the moment of recording - after the run itself had succeeded."""
    from fsmes.sim import store

    card = {"plant": "megafactory", "speed": 30.0, "seed": [7101, 7102, 7103], "duration_s": 3600,
            "metrics": {"breakdown_recall": 1.0, "faults_scored": 20, "faults_scripted": 20,
                        "planned_stop_misclassified": 0},
            "faults": [], "triage": {"findings": [], "worst": None}}
    run_id = store.record(card, path=tmp_path / "runs.db")
    stored = store.get(run_id, path=tmp_path / "runs.db")
    assert stored["seed"] == "7101,7102,7103"
