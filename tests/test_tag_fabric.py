"""The tag fabric: what a machine publishes, and the one thing it accepts.

Phase 1 of the OPC suite. The load-bearing promise is the last one here: a
setpoint is a live, writable node, and writing it moves the reading it
drives. Everything the write-back story will be built on rests on that being
true of the simulation and not just of the diagram.
"""

import json

import pytest

from fsmes.integrations.opc import csv_replay
from fsmes.integrations.opc.tag_map import MachineMap
from fsmes.sim import generate

LINE = {
    "channel": "T", "dsn": "T", "seed": 3, "duration_s": 20,
    "orders": [1], "buffers": {"capacity": 4, "initial": 1},
    "stations": [{
        "name": "Washer", "rate_per_min": 60, "scrap_pct": 1.0,
        "analogs": [
            {"name": "WashTemp", "base": 70.0, "noise": 0.0, "decimals": 2,
             "unit": "degC",
             "setpoint": {"name": "WashTempSP", "min": 55.0, "max": 85.0,
                          "lag_s": 10.0}},
            {"name": "RinsePressure", "base": 3.0, "noise": 0.0, "decimals": 2},
        ],
    }],
    "events": [],
}


@pytest.fixture()
def line(tmp_path):
    config = tmp_path / "line.json"
    config.write_text(json.dumps(LINE), encoding="utf-8")
    out = tmp_path / "out"
    generate.generate(config, out, write_docs=False)
    return out


# ------------------------------------------------------------- the manifest

def test_the_generator_says_what_every_tag_is(line):
    """A screen that browses tags, a guard that bounds a write, and an agent
    proposing one all need this - and it is generated from the line, so it
    cannot drift out of step with the plant it describes."""
    tags = json.loads((line / "tags.json").read_text(encoding="utf-8"))["tables"]["Washer"]["tags"]

    assert tags["WashTemp"]["kind"] == "pv"
    assert tags["WashTemp"]["unit"] == "degC"
    assert tags["WashTemp"]["follows"] == "WashTempSP"
    assert tags["GoodCount"]["kind"] == "counter"
    assert tags["AlarmWord"]["kind"] == "alarm"
    assert tags["RunMinutes"]["unit"] == "min"


def test_only_setpoints_are_writable(line):
    """Everything a machine reports is a reading. The one thing it accepts is
    a command, and it carries the bounds it accepts it within."""
    tags = json.loads((line / "tags.json").read_text(encoding="utf-8"))["tables"]["Washer"]["tags"]
    writable = {name for name, meta in tags.items() if meta.get("writable")}

    assert writable == {"WashTempSP"}
    assert tags["WashTempSP"] == pytest.approx(tags["WashTempSP"])  # shape check
    assert tags["WashTempSP"]["min"] == 55.0
    assert tags["WashTempSP"]["max"] == 85.0
    assert tags["WashTempSP"]["initial"] == 70.0
    assert tags["WashTempSP"]["drives"] == "WashTemp"


def test_a_setpoint_is_not_a_generated_column(line):
    """It is commanded, not replayed. A column behind it would mean the file
    overwrote the engineer every second."""
    header = (line / "Washer.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
    assert "WashTemp" in header
    assert "WashTempSP" not in header


# ------------------------------------------------------------- the columns

def test_a_machine_publishes_what_a_real_one_publishes(line):
    header = (line / "Washer.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
    for tag in ("State", "GoodCount", "ScrapCount", "TotalCount", "AlarmWord",
                "CycleTimeMs", "RunMinutes", "ReadyBit", "WashTemp",
                "RinsePressure"):
        assert tag in header, f"a machine without {tag} is not a machine"


def test_total_counts_good_and_scrap(line):
    rows = list((line / "Washer.csv").read_text(encoding="utf-8").splitlines()[1:])
    header = (line / "Washer.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
    g, s, t = (header.index(x) for x in ("GoodCount", "ScrapCount", "TotalCount"))
    for row in rows:
        cells = row.split(",")
        assert int(cells[t]) == int(cells[g]) + int(cells[s])


def test_runtime_only_advances_while_running(line):
    """Maintenance plans trigger on runtime hours. A machine that counted
    idle time as runtime would over-maintain everything that sits still."""
    text = (line / "Washer.csv").read_text(encoding="utf-8").splitlines()
    header = text[0].split(",")
    st, rm = header.index("State"), header.index("RunMinutes")
    previous = 0.0
    for row in text[1:]:
        cells = row.split(",")
        now = float(cells[rm])
        if int(cells[st]) != 1:              # 1 == RUNNING
            assert now == pytest.approx(previous), "runtime moved while not running"
        previous = now


def test_the_alarm_word_is_ground_truth():
    """Bits come from the script the generator obeyed, so an analysis suite
    reading them is reading truth rather than a guess."""
    assert generate.alarm_word(generate.DOWN, "X", 5, {}, {}) & 1
    assert generate.alarm_word(generate.CHANGEOVER, "X", 5, {}, {}) & 8
    drifting = generate.alarm_word(
        generate.RUNNING, "X", 5, {"X": [{"start": 0, "end": 10}]}, {})
    assert drifting & 2
    assert generate.alarm_word(generate.RUNNING, "X", 5, {}, {}) == 0


# ------------------------------------------------------- the write-back path

class FakeNode:
    """Stands in for an OPC node so the physics can be tested without a
    server, a port, or a clock."""

    def __init__(self, value):
        self.value = value

    async def read_value(self):
        return self.value


@pytest.mark.asyncio
async def test_writing_a_setpoint_moves_the_reading(line):
    """The promise the whole write-back story rests on: command a new
    temperature and the process goes there."""
    meta = json.loads((line / "tags.json").read_text(encoding="utf-8"))["tables"]["Washer"]["tags"]["WashTempSP"]
    node = FakeNode(70.0)
    sp = csv_replay.Setpoint("WashTempSP", meta, node, period_s=1.0)

    assert await sp.step() == pytest.approx(0.0), "nobody wrote; nothing moves"

    node.value = 60.0                       # an approved recommendation lands
    first = await sp.step()
    assert first < 0, "the reading must start falling towards the new setpoint"
    assert first > -10.0, "and must not snap there - a washer has thermal mass"

    for _ in range(200):
        await sp.step()
    assert sp.actual_offset == pytest.approx(-10.0, abs=0.1), (
        "given time, the process reaches what it was told")


@pytest.mark.asyncio
async def test_a_setpoint_outside_its_bounds_cannot_drive_the_plant(line):
    """Bounds are enforced at the API and the agent before a write gets this
    far. Clamping again here costs nothing and means a value poked in by
    hand cannot drive the simulation somewhere impossible."""
    meta = json.loads((line / "tags.json").read_text(encoding="utf-8"))["tables"]["Washer"]["tags"]["WashTempSP"]
    node = FakeNode(500.0)
    sp = csv_replay.Setpoint("WashTempSP", meta, node, period_s=1.0)

    for _ in range(500):
        await sp.step()
    assert sp.actual_offset == pytest.approx(15.0, abs=0.1), (
        "clamped to max (85) minus initial (70), not 430")


@pytest.mark.asyncio
async def test_an_unreadable_setpoint_holds_rather_than_lurches(line):
    """A node read that fails must not move the plant."""
    meta = json.loads((line / "tags.json").read_text(encoding="utf-8"))["tables"]["Washer"]["tags"]["WashTempSP"]

    class Broken:
        async def read_value(self):
            raise RuntimeError("session lost")

    sp = csv_replay.Setpoint("WashTempSP", meta, Broken(), period_s=1.0)
    sp.actual_offset = -4.0
    assert await sp.step() == pytest.approx(-4.0)


# ------------------------------------------------------------- the tag map

def test_a_machine_map_subscribes_to_every_tag_it_lists():
    spec = MachineMap(equipment="WASH01", object="Washer", cycle_seconds=1.0,
                      analog="WashTemp",
                      extra_tags=("WashTempSP", "AlarmWord", "TotalCount"))
    assert spec.tags == ("State", "GoodCount", "ScrapCount", "WashTemp",
                         "WashTempSP", "AlarmWord", "TotalCount")


def test_listing_the_primary_analog_twice_does_not_subscribe_twice():
    spec = MachineMap(equipment="WASH01", object="Washer", cycle_seconds=1.0,
                      analog="WashTemp", extra_tags=("WashTemp", "AlarmWord"))
    assert spec.tags.count("WashTemp") == 1
