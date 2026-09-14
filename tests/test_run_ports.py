"""Two ephemeral runs on one machine do not take the same port.

The fault this pins, seen on 2026-09-14: a two-plant experiment died with
*connection refused* on one of the runner's own reads while other ephemeral
plants were running on the same box; the same plan alone was clean. Probing a
port with a bind and then closing the socket says the port was free a moment
ago, which is a different claim from "this port is mine" - so two runs started
seconds apart both chose 8100, and the second plant came up on the port the
first one's API was about to take.

A port is claimed for the length of a run now, in a lock file the other runs
can see. These are the properties that makes that worth having.
"""

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fsmes.core.oplock import WriterBusy, claim_lock
from fsmes.sim import runner


@pytest.fixture(autouse=True)
def locks_of_their_own(tmp_path, monkeypatch):
    """A test run must never take a port from a real run on this machine."""
    monkeypatch.setattr(runner, "PORT_LOCKS", tmp_path / "ports")
    yield


def free_span(low: int, high: int, width: int) -> tuple[int, int]:
    """A few adjacent ports nothing on this machine is using at this moment.

    Found rather than assumed: the lab's ranges are shared with whatever else
    is running on the box, and while these tests were being written a real
    plant was listening on 8100. A test that asserts *which* port comes back
    has to start from ports that are actually free, or it is testing the
    machine rather than the code.
    """
    for start in range(low, high - width):
        if all(runner.bindable(port) for port in range(start, start + width + 1)):
            return (start, start + width)
    pytest.skip(f"no free span of {width + 1} ports in {low}-{high} on this machine")
    raise AssertionError                              # unreachable; skip raises


@pytest.fixture()
def span():
    return free_span(*runner.API_RANGE, 4)


def test_two_reservations_never_name_the_same_port():
    first, second = runner.reserve_port(8100, 8199), runner.reserve_port(8100, 8199)
    try:
        assert first.port != second.port
    finally:
        first.release()
        second.release()


def test_a_port_another_process_has_claimed_is_not_offered_to_this_one(tmp_path):
    """The whole point: the claim is visible to a *different* process, because
    two runs on one machine are two processes and not two threads."""
    script = (
        "import sys, time\n"
        "from fsmes.sim import runner\n"
        "runner.PORT_LOCKS = __import__('pathlib').Path(sys.argv[1])\n"
        "held = runner.reserve_port(8100, 8199)\n"
        "print(held.port, flush=True)\n"
        "sys.stdin.readline()\n"
        "held.release()\n"
    )
    other = subprocess.Popen([sys.executable, "-c", script, str(runner.PORT_LOCKS)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        theirs = int(other.stdout.readline().strip())
        mine = runner.reserve_port(8100, 8199)
        try:
            assert mine.port != theirs
        finally:
            mine.release()
    finally:
        other.stdin.write("\n")
        other.stdin.flush()
        other.wait(timeout=20)


def test_a_port_something_else_is_listening_on_is_skipped(span):
    """A standing plant, somebody's editor, the console. The bind probe still
    earns its keep for everything that is not one of these runs."""
    low, high = span
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", low))
        taken.listen(1)
        reserved = runner.reserve_port(low, high)
        try:
            assert reserved.port != low
        finally:
            reserved.release()
    assert not (runner.PORT_LOCKS / f"{low}.lock").exists(), (
        "a port that could not be bound must not be left claimed")


def test_a_lock_left_by_a_killed_run_is_reclaimed_rather_than_wedging_the_range(span):
    """Otherwise one killed run would take a port out of the range forever, and
    a range that quietly shrinks is worse than one that refuses."""
    import os

    low, high = span
    stale = claim_lock(runner.PORT_LOCKS / f"{low}.lock", runner.PORT_LOCK_STALE_S)
    when = time.time() - runner.PORT_LOCK_STALE_S - 60
    os.utime(stale, (when, when))
    reserved = runner.reserve_port(low, high)
    try:
        assert reserved.port == low
    finally:
        reserved.release()


def test_a_fresh_lock_is_not_reclaimed():
    claim_lock(runner.PORT_LOCKS / "8100.lock", runner.PORT_LOCK_STALE_S)
    with pytest.raises(WriterBusy):
        claim_lock(runner.PORT_LOCKS / "8100.lock", runner.PORT_LOCK_STALE_S)


def test_a_run_that_cannot_get_both_ports_keeps_neither(monkeypatch):
    """A run that took an API port and then found no OPC port would leave the
    first one claimed by a run that never started."""
    monkeypatch.setattr(runner, "OPC_RANGE", (4900, 4900))
    claim_lock(runner.PORT_LOCKS / "4900.lock", runner.PORT_LOCK_STALE_S)
    with pytest.raises(RuntimeError, match="No free OPC port"):
        runner.reserve_ports()
    assert [lock.name for lock in runner.PORT_LOCKS.glob("*.lock")] == ["4900.lock"], (
        "an API port was claimed by a run that never started")


def test_an_exhausted_range_says_how_many_are_held_and_how_many_are_in_use(span):
    """Two different problems - the machine is busy with runs, or something
    else is on the port - and a person needs to know which one they have."""
    low, _ = span
    claim_lock(runner.PORT_LOCKS / f"{low}.lock", runner.PORT_LOCK_STALE_S)
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", low + 1))
        taken.listen(1)
        with pytest.raises(RuntimeError) as raised:
            runner.reserve_port(low, low + 1, "API port")
    assert "1 held by other runs on this machine" in str(raised.value)
    assert "1 in use by something else" in str(raised.value)


# ------------------------------------------------- two runs at the same time

TINY_LINE = {
    "channel": "Tiny",
    "seed": 3,
    "duration_s": 60,
    "orders": [900],
    "buffers": {"capacity": 10, "initial": 5},
    "stations": [{"name": "Cut", "rate_per_min": 60, "scrap_pct": 0.0}],
    "events": [],
}

TAG_MAP = {"machines": [
    {"equipment": "CUT01", "object": "Cut", "cycle_seconds": 1.0, "analog": "Value",
     "order_tag": None,
     "state_map": {"0": "idle", "1": "running", "4": "down", "5": "setup"}},
]}


def _a_plant_that_will_not_seed(tmp_path: Path) -> tuple[dict, Path, Path]:
    """Everything `scored_run` reads before it starts anything."""
    from fsmes.sim.generate import generate

    tmp_path.mkdir(parents=True, exist_ok=True)
    line = tmp_path / "line.json"
    line.write_text(json.dumps(TINY_LINE), encoding="utf-8")
    generate(line, tmp_path / "out")
    tag_map = tmp_path / "tag_map.json"
    tag_map.write_text(json.dumps(TAG_MAP), encoding="utf-8")
    cfg = {"pack": str(tmp_path / "pack"), "tag_map": tag_map.name,
           "replay_dir": str(tmp_path / "out"), "env": {}}
    return cfg, line, tmp_path


def test_two_runs_started_at_once_get_different_ports_and_give_them_back(
        tmp_path, monkeypatch):
    """The failure as it happened: two runs on one box at the same moment.

    Neither plant is actually started. Each run is held at the instant it has
    claimed its ports, until the other one has claimed its own, and then the
    seeding step - the first thing `scored_run` does after claiming them - is
    made to fail. So what this asserts is exactly what went wrong that morning:
    the two runs hold four different ports at the same instant, and all four
    are handed back.

    Both ranges are spans nothing else on this machine is using, so a plant
    somebody else has running cannot make this pass or fail.
    """
    import threading

    monkeypatch.setattr(runner, "API_RANGE", free_span(*runner.API_RANGE, 3))
    monkeypatch.setattr(runner, "OPC_RANGE", free_span(*runner.OPC_RANGE, 3))
    monkeypatch.setattr(runner.plants, "fsmes_bin", lambda: sys.executable)
    monkeypatch.setattr(runner.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no plant here")))

    both_have_ports = threading.Barrier(2)
    taken: list[tuple[int, int]] = []
    errors: list[BaseException] = []
    guard = threading.Lock()
    real_reserve = runner.reserve_ports

    def hold_until_both_have_theirs():
        api, opc = real_reserve()
        with guard:
            taken.append((api.port, opc.port))
        both_have_ports.wait(timeout=30)
        return api, opc

    monkeypatch.setattr(runner, "reserve_ports", hold_until_both_have_theirs)

    # Both plants are prepared before either run starts, so what the threads
    # race for is the ports and nothing else.
    built = {name: _a_plant_that_will_not_seed(tmp_path / name)
             for name in ("tiny1", "tiny2")}

    def one(name: str) -> None:
        cfg, line, root = built[name]
        try:
            runner.scored_run(name, cfg, root, speed=60.0, line_json=line,
                              echo=lambda *a: None, triage_log=False)
        except Exception as exc:                    # the seeding step, as arranged
            with guard:
                errors.append(exc)

    threads = [threading.Thread(target=one, args=(name,)) for name in ("tiny1", "tiny2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert len(taken) == 2, f"only {len(taken)} run(s) got ports: {[repr(e) for e in errors]}"
    ports = [port for pair in taken for port in pair]
    assert len(set(ports)) == 4, f"two runs shared a port: {taken}"
    assert len(errors) == 2, f"both runs were supposed to fail at seeding, got {errors}"
    assert list(runner.PORT_LOCKS.glob("*.lock")) == [], (
        "both runs must give their ports back")
