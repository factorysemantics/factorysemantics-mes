"""A stopped plant's ports are back before `stop` returns.

2026-10-21, PR #184: every Windows cell of this project's CI waited about two
seconds after `fsmes demo <plant> stop` for the plant's socket to disappear,
and every Linux cell waited zero. CI learned to wait. The product did not - so
on a Windows PC, which is what a plant PC is, this happened:

    > fsmes demo bottling stop
      bottling stopped (5 process(es)).
    > fsmes demo bottling
      not starting bottling: 127.0.0.1:8010 is already in use, and that is the
      port the dashboard and the API needs.
      what is on it: another program on this PC ... Stop that program

Nothing was on it. The program it named was the plant that had just been
stopped, and it no longer existed. The person is told to go and stop something
that is already gone, about their own plant, on their first evening.

So the wait lives on the way down, in `fsmes.plant.stop`, where what is
holding the port is known to have been ours. A start that waited for a port to
close would be a start that talks itself past somebody else's program, which
is the one thing that check exists to catch.

These tests run on either platform. The socket that outlives its process is a
Windows behaviour and nobody on this project owns a Windows machine, so what
is pinned here is the waiting - a port that is still answering is waited for,
one that is free costs nothing and says nothing, and one that never closes is
said out loud instead of hanging the command. The `wheel-plant` CI job stops a
real plant on a real Windows runner and starts it again in the same breath.
"""

from __future__ import annotations

import socket
import threading
import time

from fsmes import firstrun
from fsmes import plant as plants

#: One simulated plant on its own: an API port and a replay of its own.
ONE_PLANT = {"label": "Bottling", "simulate": True, "api_port": 8010,
             "env": {"MES_OPC_ENDPOINT": "opc.tcp://127.0.0.1:4841/fsmes/bottling"}}

#: The same plant as two nodes on one box: the replay belongs to the plant,
#: never to a node of it.
TWO_NODES = {**ONE_PLANT, "nodes": [{"id": "a", "api_port": 8010},
                                    {"id": "b", "api_port": 8011}],
             "env": {"MES_REDUNDANCY": "true",
                     "MES_OPC_ENDPOINT": "opc.tcp://127.0.0.1:4841/fsmes/bottling"}}


def _port_says(monkeypatch, answers) -> list[tuple[str, int]]:
    """Make the one port question in this product answer from a script.

    Returns the list of ports it was asked about, in order, so a test can hold
    that nothing was asked twice over and nothing was asked at all when there
    was nothing to wait for.
    """
    asked: list[tuple[str, int]] = []

    def asking(host, port, **_):
        asked.append((host, port))
        return answers(len(asked))

    monkeypatch.setattr(plants, "answers", asking)
    monkeypatch.setattr(plants.time, "sleep", lambda _s: None)
    return asked


def test_a_stop_whose_ports_are_already_free_says_nothing_about_them(monkeypatch):
    """Which is every stop on Linux, and most of them on Windows."""
    asked = _port_says(monkeypatch, lambda _n: False)
    said: list[str] = []

    plants.ports_are_back("bottling", ONE_PLANT, echo=said.append)

    assert said == [], f"nothing waited, so there is nothing to say: {said}"
    assert asked == [("127.0.0.1", 8010), ("127.0.0.1", 4841)], (
        "one connect per port and no more; this runs on the way out of every stop")


def test_a_stop_holds_the_command_until_its_port_has_gone_quiet(monkeypatch):
    # Answering for the first three asks, gone on the fourth: the dashboard
    # port is still closing, and the OPC port was free all along.
    _port_says(monkeypatch, lambda n: n <= 3)
    said: list[str] = []

    plants.ports_are_back("bottling", ONE_PLANT, echo=said.append)

    assert len(said) == 1, f"the port that was free is not worth a line: {said}"
    assert "127.0.0.1:8010 (the dashboard and the API) was still closing" in said[0]
    assert "it is free after" in said[0], (
        "a person who waited is told what they waited for and for how long")


def test_a_port_that_never_closes_is_said_out_loud_rather_than_waited_on_for_ever(
        monkeypatch):
    """The grace period is a grace period: when it passes, `stop` says which
    port is still there and returns, rather than hanging the command."""
    _port_says(monkeypatch, lambda _n: True)
    said: list[str] = []

    plants.ports_are_back("bottling", ONE_PLANT, echo=said.append, ceiling=0.3)

    assert len(said) == 2, f"both ports were still answering: {said}"
    assert "127.0.0.1:8010 (the dashboard and the API) is still closing" in said[0]
    assert "127.0.0.1:4841 (the OPC server) is still closing" in said[1]
    assert "give it a moment and start it again" in said[0], (
        "the only thing left to do is the thing to say; there is nothing to stop")


def test_a_socket_that_outlives_its_process_is_waited_for_and_then_let_go():
    """The same wait against a real socket rather than a scripted answer.

    A listening socket held open and closed from another thread is this
    machine's nearest thing to what Windows does by itself: the process is
    gone and the port still answers. What is asserted is the state - the port
    was answering, then it was not, and the command came back after that -
    never a clock.
    """
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    port = held.getsockname()[1]
    cfg = {"simulate": False, "api_port": port}
    closing = threading.Timer(0.3, held.close)
    closing.start()
    said: list[str] = []
    try:
        plants.ports_are_back("bottling", cfg, echo=said.append)
    finally:
        closing.cancel()
        held.close()

    assert len(said) == 1 and "it is free after" in said[0], (
        f"the wait ended because the socket went, not because it timed out: {said}")
    assert f"127.0.0.1:{port}" in said[0]
    assert not plants.answers("127.0.0.1", port), (
        "the command returned while the port was still answering, which is the bug")


def test_the_ports_a_stop_waits_for_are_the_ones_its_own_processes_bound():
    """Only ever ours. A plant that says `simulate = false` has a PLC for a
    machine layer: nothing here started it, and a stop that waited for it
    would wait eight seconds for a port that is somebody else's job."""
    assert plants.listening_on(ONE_PLANT) == [
        ("the dashboard and the API", "127.0.0.1", 8010),
        ("the OPC server", "127.0.0.1", 4841)]

    assert plants.listening_on({**ONE_PLANT, "simulate": False}) == [
        ("the dashboard and the API", "127.0.0.1", 8010)], "the PLC is not ours to wait for"

    assert plants.listening_on(TWO_NODES, "b") == [
        ("the dashboard and the API", "127.0.0.1", 8011)], (
        "one node stopped is one port to wait for: its own, and not the plant's replay, "
        "which the other node is still reading")

    assert plants.listening_on(TWO_NODES, plants.MACHINES) == [
        ("the OPC server", "127.0.0.1", 4841)], (
        "the machine layer is the plant's rather than a node's, and it is the last "
        "thing a stop puts down")

    assert plants.listening_on(ONE_PLANT, plants.MACHINES) == [], (
        "a one-node plant runs its replay as one of its own processes; there is no "
        "shared machine layer to stop, so there is no port here to wait for")

    assert plants.listening_on({"simulate": True, "api_port": 8010, "env": {}}) == [
        ("the dashboard and the API", "127.0.0.1", 8010)], (
        "a pack that states no endpoint states no port, and an address nobody wrote "
        "down is not guessed at")


def test_a_plant_says_it_stopped_first_and_waits_for_its_ports_after(monkeypatch, tmp_path):
    """The order on screen. *Stopped* is the answer to what was asked; the
    wait is a thing that happened on the way out, and reading it first would
    make a plant that stopped cleanly look like one that did not."""
    monkeypatch.setattr(plants, "running_pids", lambda root, key: [4321])
    monkeypatch.setattr(plants, "_end", lambda pids: None)
    _port_says(monkeypatch, lambda n: n == 1)
    said: list[str] = []

    plants.stop("bottling", {"simulate": False, "api_port": 8010}, tmp_path, echo=said.append)

    assert said[0] == "  bottling stopped (1 process(es))."
    assert "127.0.0.1:8010 (the dashboard and the API) was still closing" in said[1]


def test_a_plant_that_was_not_running_waits_for_nothing(monkeypatch, tmp_path):
    """A port answering while none of this plant's processes is alive is
    somebody else's port, and `stop` has nothing to wait for."""
    monkeypatch.setattr(plants, "running_pids", lambda root, key: [])
    asked = _port_says(monkeypatch, lambda _n: True)
    said: list[str] = []

    plants.stop("bottling", ONE_PLANT, tmp_path, echo=said.append)

    assert said == ["  bottling is not running."]
    assert asked == [], "nothing of ours was holding a port, so no port was asked about"


def test_the_wait_is_a_grace_period_rather_than_a_measurement():
    """`PORT_GRACE` is read by `stop` and asserted by nothing else: no test
    here holds a clock, because a shared runner makes every absolute bound
    flaky and says nothing about the code. What it has to be is comfortably
    more than the two seconds every Windows cell measured on 2026-10-21, and
    short enough that a person does not think the command has hung."""
    assert 4 <= plants.PORT_GRACE <= 15


def test_the_stop_and_the_start_ask_the_same_question_of_a_port(monkeypatch):
    """One spelling of *is something listening there*. It is a connect and
    never a bind, which is the whole reason it can see a socket that is still
    closing, and the start check in `fsmes.firstrun` asks through it - so a
    second spelling here would be one that drifts away from the first."""
    called: list[tuple[str, int]] = []
    monkeypatch.setattr(plants, "answers",
                        lambda host, port, **_: called.append((host, port)) or False)
    monkeypatch.setattr(plants.time, "sleep", lambda _s: None)

    plants.ports_are_back("bottling", {"simulate": False, "api_port": 8010},
                          echo=lambda _l: None)
    assert firstrun.answers("127.0.0.1", 4841) is False

    assert called == [("127.0.0.1", 8010), ("127.0.0.1", 4841)]


def test_waiting_for_a_port_costs_nothing_when_there_is_no_port_to_wait_for():
    """A plant whose configuration names no API port - which is what a cfg
    that could not be read looks like - is not a plant with a port of zero."""
    started = time.monotonic()
    said: list[str] = []
    plants.ports_are_back("bottling", {"simulate": False}, echo=said.append)
    assert said == [] and time.monotonic() - started < 1
