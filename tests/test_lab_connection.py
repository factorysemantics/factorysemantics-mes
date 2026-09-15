"""The lab measuring what the MES said about the minutes it could not see.

Pure functions, argued with here before they are argued with in a plant. The
subject is the *measurement*, not the MES: every one of these asks whether the
lab would report the right thing given what a run saw.
"""

import json

import pytest

from fsmes.integrations.opc import csv_replay
from fsmes.kernel.tags import MANIFEST_NAME
from fsmes.lab import measure
from fsmes.sim import generate
from fsmes.sim import truth as sim_truth


def _card(windows=((360, 600),), health=1.5, faults=()):
    return {
        "observation": {"agent_health_interval_s": health},
        "disconnects": [{"event": "disconnect", "window_sim_s": [a, b],
                         "scripted_seconds": round((b - a) / 20, 1)}
                        for a, b in windows],
        "faults": list(faults),
    }


def _watched(looks, every=1.0):
    return {"looks": looks, "looks_total": len(looks), "every_wall_seconds": every,
            "failures": {}}


def _look(second, connections, states=None, disconnected=None):
    return {"line_second": second, "connections": connections, "states": states or {},
            "watching": {"machines": len(connections),
                         "disconnected": disconnected if disconnected is not None
                         else sum(1 for v in connections.values() if v == "disconnected")}}


OEE = {"stations": [{"code": "LD01", "unknown_seconds": 12.0, "availability": 0.99}]}
MAP = {"LD": "LD01"}


def test_an_outage_every_machine_read_as_disconnected_says_so():
    looks = [_look(s, {"LD01": "disconnected"}) for s in (400, 450, 500)]
    out = measure.connection(_card(), _watched(looks), OEE, MAP, 20.0)
    assert out["outages"][0]["verdict"] == "read as disconnected"
    assert out["outages"][0]["machines_read_disconnected"] == ["LD01"]
    assert out["outages"][0]["health_said_disconnected_at_worst"] == 1


def test_a_machine_still_claiming_a_state_mid_outage_is_the_fault_this_exists_to_catch():
    """One look disagreeing with itself: the plant says it cannot see the
    machine and a screen says what the machine is doing."""
    looks = [_look(450, {"LD01": "disconnected"}, states={"LD01": "running"})]
    out = measure.connection(_card(), _watched(looks), OEE, MAP, 20.0)
    assert out["outages"][0]["verdict"] == "a machine kept claiming a state through the outage"
    assert out["outages"][0]["machines_still_claiming_a_state"] == ["LD01 (running)"]


def test_the_looks_before_the_agent_noticed_are_not_counted_as_a_lie():
    """A screen showing the last state it heard, in the seconds before the
    agent's health check came round, is the detection lag - not a claim."""
    looks = [_look(400, {"LD01": "connected"}, states={"LD01": "running"}),
             _look(450, {"LD01": "disconnected"}, states={})]
    out = measure.connection(_card(), _watched(looks), OEE, MAP, 20.0)
    assert out["outages"][0]["machines_still_claiming_a_state"] == []
    assert out["outages"][0]["verdict"] == "read as disconnected"


def test_nothing_reading_as_disconnected_is_reported_as_a_fault_not_as_silence():
    looks = [_look(s, {"LD01": "connected"}, states={"LD01": "running"}) for s in (400, 500)]
    out = measure.connection(_card(), _watched(looks), OEE, MAP, 20.0)
    assert out["outages"][0]["verdict"] == "nothing read as disconnected"


def test_an_outage_shorter_than_the_agents_health_check_is_unknown_not_missed():
    """The same refusal to accuse the scorer makes about a stop shorter than
    its own sample. At 20x a 1.5 s health check is 30 s of line time, so a
    twenty-second outage could not be seen by anything."""
    out = measure.connection(_card(windows=((360, 380),)),
                             _watched([_look(370, {"LD01": "connected"})]), OEE, MAP, 20.0)
    outage = out["outages"][0]
    assert outage["verdict"] == "unknown"
    assert "shorter than it could see" in outage["unknown_because"]
    assert outage["resolvable_at_this_speed"] is False


def test_an_outage_no_look_landed_in_is_unknown_with_which_silence_it_was():
    """Three ways for a window to have no looks in it, and the measurement
    says which - never a zero standing in for silence."""
    ended = measure.connection(_card(), _watched([_look(100, {"LD01": "connected"})]),
                               OEE, MAP, 20.0)
    assert ended["outages"][0]["verdict"] == "unknown"
    assert ended["outages"][0]["unknown_because"] == "the run ended before this window"

    late = measure.connection(_card(), _watched([_look(1400, {"LD01": "connected"})]),
                              OEE, MAP, 20.0)
    assert late["outages"][0]["unknown_because"] == "the watch had not started yet"


def test_a_withheld_run_makes_every_outage_unknown_and_says_why():
    out = measure.connection(_card(), _watched([_look(450, {"LD01": "disconnected"})]),
                             OEE, MAP, 20.0, reason="the harness fell behind its own sample")
    assert out["outages"][0]["verdict"] == "unknown"
    assert out["outages"][0]["unknown_because"] == "the harness fell behind its own sample"


def test_the_scripted_seconds_are_line_seconds_and_the_mes_seconds_are_converted():
    """The script is in line seconds and the MES counts wall ones. Mixing them
    is how a measurement reports a four-minute outage as twelve seconds."""
    out = measure.connection(_card(), _watched([_look(450, {"LD01": "disconnected"})]),
                             OEE, MAP, 20.0)
    assert out["scripted_line_seconds"] == 240.0          # 600 - 360
    assert out["after_the_run"]["stations"][0]["unknown_line_seconds"] == 240.0  # 12 s x 20


def test_less_unknown_time_than_was_scripted_is_named_as_the_fault_it_is():
    """More unknown time than scripted is the detection lag. Less is the MES
    accounting for minutes it could not see."""
    thin = {"stations": [{"code": "LD01", "unknown_seconds": 1.0, "availability": 0.99}]}
    out = measure.connection(_card(), _watched([_look(450, {"LD01": "disconnected"})]),
                             thin, MAP, 20.0)
    assert out["after_the_run"]["difference_line_seconds"] < 0
    assert "SHORTER" in out["after_the_run"]["difference_says"]


def test_a_run_with_no_outage_scripted_measures_nothing_and_says_nothing_wrong():
    out = measure.connection({"observation": {}, "disconnects": []}, _watched([]),
                             OEE, MAP, 20.0)
    assert out["outages"] == []
    assert out["scripted_outages"] == 0


# ------------------------------------------------- the generator and the replay

def test_a_disconnect_needs_no_station_because_it_belongs_to_no_machine():
    """Asking which machine a network outage happened to has no answer."""
    config = {"stations": [{"name": "LD", "rate_per_min": 60}], "duration_s": 600,
              "events": [{"type": "disconnect", "start": 100, "end": 200}]}
    assert generate._validate_line(config, "test") is config


def test_an_event_type_the_generator_does_not_know_is_still_refused_by_name():
    config = {"stations": [{"name": "LD"}], "duration_s": 600,
              "events": [{"type": "unplug", "start": 100, "end": 200}]}
    with pytest.raises(SystemExit) as raised:
        generate._validate_line(config, "test")
    assert "unknown event type" in str(raised.value)


def test_the_manifest_carries_the_windows_the_endpoint_is_shut_for(tmp_path):
    """In the manifest and not in the CSVs, because nothing about the line
    changes: the machines run on and the tables say what they always said."""
    config = {"stations": [{"name": "LD"}], "duration_s": 600,
              "events": [{"type": "disconnect", "start": 100, "end": 200}]}
    generate.write_manifest(config, tmp_path)
    manifest = json.loads((tmp_path / MANIFEST_NAME).read_text())
    assert manifest["disconnects"] == [{"start": 100, "end": 200}]


def test_a_disconnect_is_not_a_stop_of_any_kind():
    event = sim_truth.ScriptedEvent(type="disconnect", start_s=100, end_s=200)
    assert event.is_disconnect
    assert not event.is_fault and not event.planned and not event.is_idle


class _FakeServer:
    def __init__(self):
        self.calls = []

    async def stop(self):
        self.calls.append("stop")

    async def start(self):
        self.calls.append("start")


async def _follow(endpoint, ticks):
    for tick in ticks:
        await endpoint.follow(tick)


def test_the_replay_closes_its_endpoint_for_the_scripted_window_and_reopens_it():
    import asyncio

    server = _FakeServer()
    endpoint = csv_replay.Endpoint(server, [{"start": 100, "end": 200}])
    asyncio.run(_follow(endpoint, [99, 100, 150, 199, 200, 250]))
    assert server.calls == ["stop", "start"], "the socket opened or closed more than once"


def test_a_window_the_replay_is_already_inside_does_not_close_the_socket_twice():
    import asyncio

    server = _FakeServer()
    endpoint = csv_replay.Endpoint(server, [{"start": 100, "end": 200}])
    asyncio.run(_follow(endpoint, [120, 130, 140]))
    assert server.calls == ["stop"]


def test_a_replay_with_no_disconnect_scripted_never_touches_its_socket():
    import asyncio

    server = _FakeServer()
    endpoint = csv_replay.Endpoint(server, [])
    asyncio.run(_follow(endpoint, range(0, 300, 10)))
    assert server.calls == []
