"""`fsmes scenario`: timed tag values written into an OPC UA server.

The file is read with its line numbers, so a mistake is named where it is.
The safety rule is code: a tag on a channel not declared Simulator or Memory
Based is refused, as is one the server shows has real equipment behind it, and
a read-only tag is reported and never written. Values go in as each tag's own
type, because Kepware refuses a wrong variant.
"""

import asyncio
import json
import socket
from pathlib import Path

import pytest
from asyncua import Client, Server, ua
from typer.testing import CliRunner

from fsmes.cli import app
from fsmes.integrations.opc import scenario as sc

EXAMPLES = Path(__file__).resolve().parents[1] / "labs" / "kepsim" / "scenarios"


def _parse(doc: dict) -> sc.Scenario:
    return sc.parse(json.dumps(doc, indent=2))


def _problems(text: str) -> list[str]:
    with pytest.raises(sc.ScenarioError) as caught:
        sc.parse(text)
    return caught.value.problems


# ------------------------------------------------------------------ the file


def test_a_broken_file_names_the_line_of_each_mistake():
    text = """{
  "tags": {"good": "ns=2;s=Sim.Dev.Good"},
  "drivers": {"Sim": "Simulator"},
  "steps": [
    {"at": "00:10", "tag": "good", "value": 1},
    {"at": "ten", "tag": "good", "value": 2},
    {"at": 20, "tag": "nope", "value": 3},
    {"at": 30, "tag": "good", "valu": 4}
  ]
}"""
    problems = _problems(text)
    assert any(p.startswith("line 6:") and "'ten' is not a time" in p for p in problems)
    assert any(p.startswith("line 7:") and "'nope' is not in `tags`" in p for p in problems)
    assert any(p.startswith("line 8:") and "unknown key 'valu'" in p for p in problems)
    assert not any(p.startswith("line 5:") for p in problems), "the good step is not blamed"


def test_a_file_that_is_not_json_names_the_line_it_broke_on():
    problems = _problems('{\n  "tags": {"a": "ns=2;s=X.Y.Z"},\n  "steps": [\n    {"at": 1,}\n  ]\n}')
    assert problems[0].startswith("line 4:")


def test_a_key_given_twice_is_a_mistake_not_a_silent_overwrite():
    problems = _problems('{\n  "tags": {"a": "ns=2;s=X.Y.Z"},\n  "steps": [\n'
                         '    {"at": 1, "tag": "a", "value": 1, "value": 2}\n  ]\n}')
    assert problems == ["line 4: 'value' is given twice in the same block"]


def test_a_ramp_climbs_from_its_start_to_its_end_in_whole_numbers_on_a_count():
    plan = _parse({"tags": {"good": "ns=2;s=Sim.D.Good"},
                   "steps": [{"at": "01:00", "tag": "good",
                              "ramp": {"from": 0, "to": 400, "over": 120, "every": 30}}]})
    assert [(w.at, w.value) for w in plan.writes] == [
        (60, 0), (90, 100), (120, 200), (150, 300), (180, 400)]
    assert all(isinstance(w.value, int) for w in plan.writes)


def test_a_product_visits_each_station_in_order_advance_seconds_apart():
    plan = _parse({
        "tags": {"a.good": "ns=2;s=S.A.Good", "a.id": "ns=2;s=S.A.Id", "b.good": "ns=2;s=S.B.Good",
                 "b.state": "ns=2;s=S.B.State"},
        "product": {"id": "CAN-{n:03d}", "units": 2, "every_s": 30, "advance_s": 10, "start": 5,
                    "stations": [{"station": "A", "count": "a.good", "id": "a.id"},
                                 {"station": "B", "count": "b.good", "state": "b.state",
                                  "running": 1, "after": 0}]}})
    rows = [(w.at, w.alias, w.increment if w.increment is not None else w.value) for w in plan.writes]
    assert rows == [
        (5, "a.id", "CAN-001"), (5, "a.good", 1),
        (15, "b.state", 1), (15, "b.good", 1), (25, "b.state", 0),
        (35, "a.id", "CAN-002"), (35, "a.good", 1),
        (45, "b.state", 1), (45, "b.good", 1), (55, "b.state", 0)]


def test_both_example_scenarios_in_the_lab_read_cleanly():
    files = sorted(EXAMPLES.glob("*.json"))
    assert len(files) == 2, f"two examples expected, found {len(files)}: {files}"
    for path in files:
        assert sc.load(path).writes, path


def test_check_without_a_server_says_what_is_wrong_and_exits_2(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text('{\n  "tags": {"a": "ns=2;s=X.Y.Z"},\n  "steps": [\n'
                      '    {"at": "1:xx", "tag": "a", "value": 1}\n  ]\n}', encoding="utf-8")
    result = CliRunner().invoke(app, ["scenario", "check", str(broken)])
    assert result.exit_code == 2
    assert "line 4:" in result.output and "not a time" in result.output


# --------------------------------------------------------------- the safety


def _tag(address: str) -> sc.Tag:
    return sc.Tag("t", address, 1)


def test_a_channel_declared_simulator_or_memory_based_may_be_written():
    assert sc.judge_driver(_tag("ns=2;s=Scratch.D.T"), {"Scratch": "Simulator"}) == ""
    assert sc.judge_driver(_tag("ns=2;s=Mem.D.T"), {"Mem": "memory-based"}) == ""


def test_a_channel_on_a_plc_driver_is_refused_with_its_name():
    reason = sc.judge_driver(_tag("ns=2;s=Press.PLC1.Speed"), {"Press": "Siemens TCP/IP Ethernet"})
    assert "'Press'" in reason and "Siemens TCP/IP Ethernet" in reason and "real equipment" in reason


def test_a_channel_nobody_declared_is_refused_rather_than_guessed():
    assert "not declared" in sc.judge_driver(_tag("ns=2;s=Unknown.D.T"), {})


def test_an_advanced_simulator_channel_is_refused_because_odbc_tags_cannot_be_written():
    assert "ODBC" in sc.judge_driver(_tag("ns=2;s=SimLine.LD.LD_csv_State"), {"SimLine": "Advanced Simulator"})


def test_a_numeric_node_id_has_no_channel_and_is_refused():
    assert "no channel" in sc.judge_driver(_tag("ns=2;i=13"), {"x": "Simulator"})


def test_a_value_becomes_the_tags_own_type_or_says_why_not():
    def target(vt, length=None):
        return sc.Target(_tag("ns=2;s=a.b.c"), variant=vt, array_length=length)

    assert sc.coerce(7, target(ua.VariantType.Int16)) == ua.Variant(7, ua.VariantType.Int16)
    assert sc.coerce(12, target(ua.VariantType.String)) == ua.Variant("12", ua.VariantType.String)
    assert sc.coerce(1, target(ua.VariantType.Boolean)) == ua.Variant(True, ua.VariantType.Boolean)
    assert sc.coerce([1, 2], target(ua.VariantType.Int16, 2)).Value == [1, 2]
    for value, vt, length, says in [(40000, ua.VariantType.Int16, None, "does not fit"),
                                    (-1, ua.VariantType.UInt32, None, "does not fit"),
                                    (1.5, ua.VariantType.Int32, None, "whole number"),
                                    ([1, 2, 3], ua.VariantType.Int16, 2, "holds 2 values"),
                                    (5, ua.VariantType.Int16, 4, "give a list")]:
        with pytest.raises(ValueError, match=says):
            sc.coerce(value, target(vt, length))


# ---------------------------------------------------- against a real server


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _server(endpoint: str, *, kepware: bool = False) -> tuple[Server, dict]:
    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    await server.register_namespace("KEPServerEX" if kepware else "urn:test:scenario")
    objects = server.nodes.objects
    nodes = {}
    for name, variant, writable in [
        ("Scratch.Dev.Lot", ua.Variant("", ua.VariantType.String), True),
        ("Scratch.Dev.Running", ua.Variant(False, ua.VariantType.Boolean), True),
        ("Scratch.Dev.State", ua.Variant(0, ua.VariantType.Int16), True),
        ("Scratch.Dev.Good", ua.Variant(100, ua.VariantType.UInt32), True),
        ("Scratch.Dev.Heads", ua.Variant([0, 0, 0, 0], ua.VariantType.Int16), True),
        ("Scratch.Dev.Temp", ua.Variant(20.0, ua.VariantType.Float), True),
        ("Scratch.Dev.Locked", ua.Variant(5, ua.VariantType.Int32), False),
        ("Press.PLC1.Speed", ua.Variant(10, ua.VariantType.Int32), True),
        ("Fake.PLC2.Speed", ua.Variant(10, ua.VariantType.Int32), True),
        ("Fake.PLC2._System._DeviceId", ua.Variant("192.168.1.10", ua.VariantType.String), False),
    ]:
        node = await objects.add_variable(ua.NodeId(name, 2), name, variant)
        if writable:
            await node.set_writable()
        nodes[name] = node
    return server, nodes


def _run(coro):
    return asyncio.run(coro)


TYPED = {
    "tags": {"lot": "ns=2;s=Scratch.Dev.Lot", "running": "ns=2;s=Scratch.Dev.Running",
             "state": "ns=2;s=Scratch.Dev.State", "good": "ns=2;s=Scratch.Dev.Good",
             "heads": "ns=2;s=Scratch.Dev.Heads", "temp": "ns=2;s=Scratch.Dev.Temp"},
    "drivers": {"Scratch": "Simulator"},
    "steps": [{"at": 0, "tag": "lot", "value": "LOT-7"},
              {"at": 0, "tag": "running", "value": True},
              {"at": 2, "tag": "state", "value": 3},
              {"at": 2, "tag": "heads", "value": [1, 2, 3, 4]},
              {"at": 4, "tag": "temp", "value": 61.5}],
    "product": {"advance_s": 1, "start": 6, "stations": [{"station": "Dev", "count": "good", "by": 5}]},
}


@pytest.mark.slow
def test_each_value_lands_at_its_time_as_the_tags_own_type():
    endpoint = f"opc.tcp://127.0.0.1:{_free_port()}/test"
    plan = _parse(TYPED)

    async def go():
        server, nodes = await _server(endpoint)
        async with server:
            async with Client(endpoint) as client:
                targets = await sc.inspect(client, plan)
                assert {a: t.problem for a, t in targets.items() if t.problem} == {}
                clock = [0.0]
                stamped = []

                async def sleep(seconds):
                    clock[0] += seconds

                def out(line):
                    stamped.append((clock[0], line.split(" set ")[1].split(" =")[0]))

                done = await sc.play(client, plan, targets, speed=2.0, out=out,
                                     now=lambda: clock[0], sleep=sleep)
            values = {name: await node.read_data_value() for name, node in nodes.items()}
        return done, stamped, values

    done, stamped, values = _run(go())
    assert done == 6
    # Played at 2x: the writes due at 0, 2, 4 and 6 s land at 0, 1, 2 and 3 s.
    assert stamped == [(0, "lot"), (0, "running"), (1, "state"), (1, "heads"), (2, "temp"), (3, "good")]
    expect = {"Scratch.Dev.Lot": ("LOT-7", ua.VariantType.String),
              "Scratch.Dev.Running": (True, ua.VariantType.Boolean),
              "Scratch.Dev.State": (3, ua.VariantType.Int16),
              "Scratch.Dev.Heads": ([1, 2, 3, 4], ua.VariantType.Int16),
              "Scratch.Dev.Temp": (61.5, ua.VariantType.Float),
              "Scratch.Dev.Good": (105, ua.VariantType.UInt32)}
    for name, (value, vt) in expect.items():
        assert (values[name].Value.Value, values[name].Value.VariantType) == (value, vt), name


@pytest.mark.slow
def test_a_tag_on_a_plc_driver_is_refused_and_nothing_at_all_is_written():
    endpoint = f"opc.tcp://127.0.0.1:{_free_port()}/test"
    plan = _parse({"tags": {"lot": "ns=2;s=Scratch.Dev.Lot", "speed": "ns=2;s=Press.PLC1.Speed"},
                   "drivers": {"Scratch": "Simulator", "Press": "Siemens TCP/IP Ethernet"},
                   "steps": [{"at": 0, "tag": "lot", "value": "LOT-1"},
                             {"at": 1, "tag": "speed", "value": 99}]})

    async def go():
        server, nodes = await _server(endpoint)
        async with server:
            async with Client(endpoint) as client:
                targets = await sc.inspect(client, plan)
                with pytest.raises(sc.ScenarioError) as caught:
                    await sc.play(client, plan, targets, sleep=lambda s: asyncio.sleep(0))
            return targets, caught.value, await nodes["Scratch.Dev.Lot"].read_value(), \
                await nodes["Press.PLC1.Speed"].read_value()

    targets, error, lot, speed = _run(go())
    assert targets["speed"].verdict == "refused" and "Siemens" in targets["speed"].problem
    assert "speed: refused" in str(error)
    assert (lot, speed) == ("", 10), "a refusal writes nothing, not even the allowed tags"


@pytest.mark.slow
def test_kepware_showing_a_network_device_overrules_a_simulator_declaration():
    endpoint = f"opc.tcp://127.0.0.1:{_free_port()}/test"
    plan = _parse({"tags": {"speed": "ns=2;s=Fake.PLC2.Speed"}, "drivers": {"Fake": "Simulator"},
                   "steps": [{"at": 0, "tag": "speed", "value": 1}]})

    async def go():
        server, _ = await _server(endpoint, kepware=True)
        async with server, Client(endpoint) as client:
            return await sc.inspect(client, plan)

    target = _run(go())["speed"]
    assert target.verdict == "refused"
    assert "192.168.1.10" in target.problem


@pytest.mark.slow
def test_a_read_only_tag_is_reported_and_not_written():
    endpoint = f"opc.tcp://127.0.0.1:{_free_port()}/test"
    plan = _parse({"tags": {"locked": "ns=2;s=Scratch.Dev.Locked", "missing": "ns=2;s=Scratch.Dev.Nope"},
                   "drivers": {"Scratch": "Simulator"},
                   "steps": [{"at": 0, "tag": "locked", "value": 9}, {"at": 0, "tag": "missing", "value": 1}]})

    async def go():
        server, nodes = await _server(endpoint)
        async with server:
            async with Client(endpoint) as client:
                targets = await sc.inspect(client, plan)
                lines = []
                bad = sc.report(targets, lines.append)
            return targets, bad, lines, await nodes["Scratch.Dev.Locked"].read_value()

    targets, bad, lines, locked = _run(go())
    assert targets["locked"].verdict == "read-only"
    assert targets["missing"].verdict == "missing"
    assert bad == 2 and lines[-1] == "2 tags: 0 can be written, 2 cannot."
    assert locked == 5


@pytest.mark.slow
def test_the_lab_product_walk_plays_into_its_own_rehearsal_server():
    endpoint = f"opc.tcp://127.0.0.1:{_free_port()}/rehearsal"
    plan = sc.load(EXAMPLES / "product-through-the-line.json")

    async def go():
        server = await sc.rehearsal_server(plan, endpoint)
        async with server, Client(endpoint) as client:
            targets = await sc.inspect(client, plan)
            await sc.play(client, plan, targets, speed=1000, out=lambda _: None)
            return {alias: await client.get_node(tag.address).read_value()
                    for alias, tag in plan.tags.items()}

    values = _run(go())
    assert values["pal.good"] == 3 and values["ld.good"] == 3
    assert values["qi.serial"] == "CAN-0003" and values["qi.pass"] is True
    assert values["ld.state"] == 0, "the loader goes back to 0 once the last can has left it"
