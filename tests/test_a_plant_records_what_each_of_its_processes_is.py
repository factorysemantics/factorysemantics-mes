"""A pid on its own is not something a person can act on.

On 2026-10-13 at 07:56 the first Windows user read `fsmes demo bottling
status` and sent back the whole of what it told him: "Says 2 PID are alive,
7964 and 3940. But it can't talk to them." Two numbers. Nothing in the product
knew which two processes those were, because the only record of a plant's
processes was the order of the pids in its pid file - and order is exactly
what a half-dead plant has lost.

So `start` now writes what each pid is beside the pid file, in the same
breath, from the one table that also starts them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from fsmes import plant as plants


class Fake:
    """A `Popen` that starts nothing and hands out pids in launch order."""

    given: ClassVar[list[int]] = []

    def __init__(self, *args, **kwargs) -> None:
        Fake.given.append(7000 + len(Fake.given))
        self.pid = Fake.given[-1]
        self.stdin = type("P", (), {"close": lambda self: None})()


@pytest.fixture
def a_plant_that_starts_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    Fake.given = []
    monkeypatch.setattr(plants.subprocess, "Popen", Fake)
    monkeypatch.setattr(plants, "fsmes_bin", lambda: "fsmes")
    monkeypatch.setattr(plants, "running_pids", lambda root, name: [])
    monkeypatch.setattr(plants, "plant_env", lambda *a, **k: {})
    monkeypatch.setattr(plants.time, "sleep", lambda seconds: None)
    (tmp_path / "data").mkdir()
    return tmp_path


def test_a_simulating_plant_is_five_processes_and_says_what_each_one_is(a_plant_that_starts_nothing):
    root = a_plant_that_starts_nothing
    plants.start("bottling", {"simulate": True, "api_port": 8010}, root, echo=lambda line: None)

    recorded = json.loads(plants.procs_file(root, "bottling").read_text(encoding="utf-8"))
    assert [row["role"] for row in recorded] == [
        "the OPC server the machines answer on",
        "the reader that writes the machines into the MES",
        "the dashboard and the API",
        "the simulated floor",
        "the log sink",
    ], "the five processes of a simulating plant, in the order they are started"
    assert [row["pid"] for row in recorded] == \
        [int(p) for p in plants.pid_file(root, "bottling").read_text().split()], \
        "the roles and the pid file must name the same processes, in the same order"


def test_a_plant_that_only_serves_records_the_two_processes_it_is(a_plant_that_starts_nothing):
    """`simulate = false` costs the machine one API process and a sink; the
    record says two, not five with three missing."""
    root = a_plant_that_starts_nothing
    plants.start("cutlery", {"simulate": False, "api_port": 9030}, root, echo=lambda line: None)

    recorded = json.loads(plants.procs_file(root, "cutlery").read_text(encoding="utf-8"))
    assert [row["role"] for row in recorded] == ["the dashboard and the API", "the log sink"]


def test_reading_the_processes_back_says_which_are_alive_and_where_their_logs_are(
        a_plant_that_starts_nothing, monkeypatch):
    root = a_plant_that_starts_nothing
    plants.start("bottling", {"simulate": True, "api_port": 8010}, root, echo=lambda line: None)
    pids = [int(p) for p in plants.pid_file(root, "bottling").read_text().split()]
    gone = pids[2]  # the API, which is the one anything would need to answer
    monkeypatch.setattr(plants, "_alive", lambda pid: pid != gone)

    seen = plants.processes_in(plants.pid_file(root, "bottling").parent, "bottling", logs=root / "logs" / "bottling")
    assert len(seen) == 5, "every process the pid file names is reported, alive or gone"
    missing = [p for p in seen if not p.alive]
    assert [p.role for p in missing] == ["the dashboard and the API"]
    assert Path(missing[0].log) == root / "logs" / "bottling" / "api.jsonl", (
        "the log a person is sent to read is the log of the process that is gone")
    assert [p.pid for p in seen if p.alive] == [p for p in pids if p != gone]


def test_a_plant_started_before_the_roles_were_recorded_does_not_guess_them(tmp_path, monkeypatch):
    """A plant left running by 0.3.0 has a pid file and no record of roles.

    Unknown is not zero: all five pids are still reported, and each one says
    its role was not recorded rather than being handed the role that happens
    to sit in that position of the table today.
    """
    data = plants.pid_file(tmp_path, "bottling").parent
    (data / "bottling.pids").write_text("7964\n3940\n", encoding="utf-8")
    monkeypatch.setattr(plants, "_alive", lambda pid: True)

    seen = plants.processes_in(data, "bottling")
    assert [p.pid for p in seen] == [7964, 3940]
    assert [p.role for p in seen] == ["", ""]
    assert all("not recorded" in p.said for p in seen)


def test_stopping_a_plant_takes_the_record_of_its_processes_with_it(
        a_plant_that_starts_nothing, monkeypatch):
    root = a_plant_that_starts_nothing
    plants.start("bottling", {"simulate": True, "api_port": 8010}, root, echo=lambda line: None)
    assert plants.procs_file(root, "bottling").is_file()

    monkeypatch.setattr(plants, "running_pids", lambda r, n: [7000])
    monkeypatch.setattr(plants, "_alive", lambda pid: False)
    monkeypatch.setattr(plants.os, "kill", lambda pid, sig: None)
    plants.stop("bottling", {"api_port": 8010}, root, echo=lambda line: None)

    assert not plants.procs_file(root, "bottling").exists(), (
        "a record of processes that are gone is a record that misleads the next status")
    assert not plants.pid_file(root, "bottling").exists()


def test_a_plant_with_no_pid_file_reports_no_processes_rather_than_raising(tmp_path):
    assert plants.processes_in(plants.pid_file(tmp_path, "bottling").parent, "bottling") == []


def test_every_process_a_plant_is_made_of_has_a_role_and_a_log_of_its_own():
    """The table is read by `status`, so two processes sharing a role would
    print the same sentence twice and send a person to one log for both."""
    specs = [*plants.processes({"simulate": True}), plants.SINK]
    roles = [s.role for s in specs]
    logs = [s.log for s in specs]
    assert all(roles) and len(set(roles)) == len(roles), roles
    assert all(logs) and len(set(logs)) == len(logs), logs
