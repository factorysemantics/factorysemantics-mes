"""A registry entry may serve without simulating."""
from __future__ import annotations

from fsmes import plant as plants


def test_a_plant_that_says_simulate_false_starts_the_api_alone(tmp_path, monkeypatch):
    """The public demo of a plant that was measured: its database holds the
    hour, the API serves it, and no replay, agent or floor starts - so a
    restart of the unit cannot bring the line back."""
    launched: list[list[str]] = []

    class FakeProc:
        pid = 4242

        def poll(self):
            return None

    def fake_popen(args, **kwargs):
        launched.append([a for a in args if isinstance(a, str)])
        return FakeProc()

    monkeypatch.setattr(plants.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(plants, "fsmes_bin", lambda: "fsmes")
    monkeypatch.setattr(plants, "running_pids", lambda root, name: [])
    monkeypatch.setattr(plants.time, "sleep", lambda s: None)
    cfg = {"api_port": 9999, "opc_port": 4999, "tag_map": "t.json", "replay_dir": "out", "simulate": False,
           "init": "init.py", "post_boot": "driver.py"}
    plants.start("demo", cfg, tmp_path, echo=lambda *a: None)
    assert [a[1] for a in launched] == ["run-api"], "the API alone; no replay, agent, floor or driver"

    launched.clear()
    cfg["simulate"] = True
    plants.start("demo", cfg, tmp_path, echo=lambda *a: None)
    assert [a[1] for a in launched] == ["run-opc-sim", "run-opc-agent", "run-api", "run-operations", "driver.py"]
    assert plants.simulates({}) is True, "a registry that says nothing simulates, as every plant did before"
