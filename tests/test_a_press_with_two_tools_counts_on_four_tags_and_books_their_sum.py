"""A machine that counts on more than one tag, from the PLC to the booking.

A press with two tools, A and B, runs both at once. Its PLC keeps a good and
a scrap counter per tool and no total, and either tool can stop while the
other runs on. A machine with several outfeeds can count the same way. Nothing in
the MES could say that before: a machine was one GoodCount and one ScrapCount.

What these tests hold, on an invented two-station toy (a feeder and the press
`PRESS-1` with tools `A` and `B`):

- the tag map names the counters a machine counts on, and the agent books
  their sum, each tag's increase taken from its own last value;
- the generator can play a machine with sides, one side stopping while the
  other runs, two machines standing side by side in one stage, defect kinds
  that add up to the scrap, and identifier tags that step;
- the replay serves those identifiers as text, and serves a map that
  addresses its tags by node id at exactly those node ids.

Nothing here listens on a port and nothing reads a wall clock.
"""

from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path

import pytest
from asyncua import ua
from sqlalchemy import select

from fsmes.config import Settings
from fsmes.domain import Equipment, EquipmentLevel, ProductionLog, TagValue
from fsmes.integrations.opc import agent, csv_replay
from fsmes.integrations.opc.tag_map import load_manifest, load_tag_map
from fsmes.sim import generate as gen

FEED = {"name": "FEED", "rate_per_min": 30, "scrap_pct": 0.0}
PRESS = {"name": "PRESS1", "rate_per_min": 12, "scrap_pct": 5.0,
         "sides": {"names": ["A", "B"],
                   "counters": {"Good_{side}": "good", "Scrap_{side}": "scrap"},
                   "state": "State_{side}"}}


def a_line(*stations: dict, events: list | None = None, seconds: int = 1200) -> dict:
    return {"channel": "Toy", "dsn": "ToyCSV", "seed": 3, "duration_s": seconds,
            "state_filter_s": 15, "orders": [1], "buffers": {"capacity": 4, "initial": 2},
            "stations": [dict(s) for s in stations], "events": events or []}


def play(tmp_path: Path, line: dict) -> dict[str, list[dict]]:
    """Generate the line and read every table back, numbers as numbers."""
    path = tmp_path / "line.json"
    path.write_text(json.dumps(line), encoding="utf-8")
    out = tmp_path / "out"
    gen.generate(path, out, write_docs=False)
    tables: dict[str, list[dict]] = {"_out": out}
    for table in out.glob("*.csv"):
        with table.open(encoding="utf-8", newline="") as f:
            tables[table.stem] = [{k: (int(v) if v.lstrip("-").isdigit() else v)
                                   for k, v in row.items()} for row in csv.DictReader(f)]
    return tables


def a_map(tmp_path: Path, **machine) -> Path:
    entry = {"equipment": "PRESS-1", "object": "PRESS1", "cycle_seconds": 5.0,
             "order_tag": None, "analog": "Value",
             "counters": {"good": ["Good_A", "Good_B"], "scrap": ["Scrap_A", "Scrap_B"]}}
    entry.update(machine)
    path = tmp_path / "tag_map.json"
    path.write_text(json.dumps({"machines": [entry]}), encoding="utf-8")
    return path


# ------------------------------------------------------------- the tag map


def test_a_machine_that_names_its_counters_subscribes_to_them_and_not_to_goodcount(tmp_path):
    spec = load_tag_map(a_map(tmp_path))[0]
    assert spec.counter_tags == {"Good_A": "good", "Good_B": "good",
                                 "Scrap_A": "scrap", "Scrap_B": "scrap"}
    assert {"Good_A", "Good_B", "Scrap_A", "Scrap_B"} <= set(spec.tags)
    assert "GoodCount" not in spec.tags and "ScrapCount" not in spec.tags
    assert all(spec.acts_on(t) for t in ("State", "Good_A", "Scrap_B"))
    # A total it also publishes is recorded, never booked a second time.
    assert not spec.acts_on("GoodCount")


def test_a_machine_that_names_no_counters_counts_on_goodcount_and_scrapcount_as_before(tmp_path):
    spec = load_tag_map(a_map(tmp_path, counters=None))[0]
    assert spec.counter_tags == {"GoodCount": "good", "ScrapCount": "scrap"}
    assert spec.acts_on("GoodCount") and spec.acts_on("ScrapCount")
    assert load_tag_map(a_map(tmp_path, counters=None, counts=False))[0].counter_tags == {}


def test_a_node_addressed_machine_subscribes_to_the_counters_its_nodes_list(tmp_path):
    nodes = {"State": "ns=2;s=Chan.Dev.Status", "Good_A": "ns=2;s=Chan.Dev.GoodA",
             "Good_B": "ns=2;s=Chan.Dev.GoodB"}
    spec = load_tag_map(a_map(tmp_path, object=None, nodes=nodes,
                              counters={"good": ["Good_A", "Good_B"]}))[0]
    assert spec.tags == ("State", "Good_A", "Good_B")


@pytest.mark.parametrize("counters, said", [
    ({"good": ["Good_A"], "total": ["X"]}, "which tags count good"),
    ({"good": ["Good_A", "Good_A"]}, "listed twice"),
    ({"good": ["Good_A"], "scrap": ["Good_A"]}, "listed twice"),
    ({"good": "Good_A"}, "list of tag names"),
    ({"good": [], "scrap": []}, "names no tag"),
])
def test_a_counters_block_that_could_book_production_twice_or_wrongly_is_refused(tmp_path, counters, said):
    with pytest.raises(ValueError, match=said):
        load_tag_map(a_map(tmp_path, counters=counters))


def test_counters_on_a_machine_that_counts_nothing_is_refused(tmp_path):
    with pytest.raises(ValueError, match="counts nothing"):
        load_tag_map(a_map(tmp_path, counts=False))


# ------------------------------------------------------------- the agent


class _Node:
    def __init__(self, name):
        self.name = name


@pytest.fixture()
def press(session):
    line = session.scalar(select(Equipment).where(Equipment.code == "LINE1"))
    session.add(Equipment(code="PRESS-1", name="Press 1", level=EquipmentLevel.WORK_UNIT,
                          parent=line, ideal_cycle_seconds=5.0))
    session.flush()
    return session


def _the_agent_reads(session, handler, spec, readings) -> None:
    """One batch, booked the way `_book_with_retry` books it - the counter
    values it consumed become the baseline once its transaction is in."""
    pending: dict = {}
    handler._book(session, [(spec, tag, value) for tag, value in readings], pending)
    handler.last_counts.update(pending)
    session.flush()


def _booked(session) -> tuple[float, float]:
    machine = session.scalar(select(Equipment).where(Equipment.code == "PRESS-1"))
    rows = session.scalars(select(ProductionLog).where(ProductionLog.equipment_id == machine.id)).all()
    return sum(r.good_qty for r in rows), sum(r.scrap_qty for r in rows)


def test_the_agent_books_the_sum_of_both_tools_each_from_its_own_last_value(press, tmp_path):
    spec = load_tag_map(a_map(tmp_path))[0]
    handler = agent._Handler({_Node(t): (spec, t) for t in spec.tags})
    readings = [("Good_A", 10), ("Good_B", 7), ("Scrap_A", 1), ("Scrap_B", 0)]
    # First reading of each tag is its baseline, so it books nothing.
    _the_agent_reads(press, handler, spec, readings)
    assert _booked(press) == (0, 0)

    # Tool A made 5 good and 1 scrap, tool B 3 good: one booking of 8 and 1.
    _the_agent_reads(press, handler, spec, [("Good_A", 15), ("Good_B", 10), ("Scrap_A", 2)])
    assert _booked(press) == (8, 1)

    # Tool B stopped; tool A ran on. Only A's increase is booked.
    _the_agent_reads(press, handler, spec, [("Good_A", 19), ("Good_B", 10)])
    assert _booked(press) == (12, 1)

    # The per-tool split is kept as tag history under each tag's own name.
    history = {t.tag for t in press.scalars(select(TagValue)).all()}
    assert {"PRESS-1.Good_A", "PRESS-1.Good_B", "PRESS-1.Scrap_A"} <= history


def test_a_goodcount_beside_named_counters_is_recorded_and_not_booked(press, tmp_path):
    spec = load_tag_map(a_map(tmp_path, extra_tags=["GoodCount"]))[0]
    handler = agent._Handler({_Node(t): (spec, t) for t in spec.tags})
    for readings in ([("GoodCount", 100), ("Good_A", 1)], [("GoodCount", 140), ("Good_A", 3)]):
        # Exactly the split the agent makes before it books anything.
        _the_agent_reads(press, handler, spec, [r for r in readings if spec.acts_on(r[0])])
    assert _booked(press) == (2, 0)


# ------------------------------------------------------------- the generator


def test_the_sides_of_a_machine_add_up_to_its_good_and_its_scrap(tmp_path):
    rows = play(tmp_path, a_line(FEED, PRESS))["PRESS1"]
    for row in rows[::97]:
        assert row["Good_A"] + row["Good_B"] == row["GoodCount"]
        assert row["Scrap_A"] + row["Scrap_B"] == row["ScrapCount"]
    assert rows[-1]["Good_A"] > 0 and rows[-1]["Good_B"] > 0


def test_one_side_stopped_halves_the_machine_while_the_other_runs_on(tmp_path):
    stop = {"type": "down", "station": "PRESS1", "side": "B", "start": 300, "end": 900}
    rows = play(tmp_path, a_line(FEED, PRESS, events=[stop]))["PRESS1"]
    at = {r["TSec"]: r for r in rows}
    during = [r for r in rows if 300 <= r["TSec"] < 900]
    assert {r["State_B"] for r in during} == {4}, "side B says it is stopped"
    assert {r["State_A"] for r in during} == {1} and {r["State"] for r in during} == {1}
    assert at[899]["Good_B"] == at[300]["Good_B"], "side B counted nothing while stopped"
    a_made = at[899]["Good_A"] - at[300]["Good_A"]
    both_made = at[299]["GoodCount"] - at[0]["GoodCount"]
    # Half rate for twice the seconds: about what both sides made in the first 300 s.
    assert a_made == pytest.approx(both_made, rel=0.25)


def test_both_sides_stopped_is_the_machine_down(tmp_path):
    events = [{"type": "down", "station": "PRESS1", "side": s, "start": 300, "end": 400}
              for s in ("A", "B")]
    rows = play(tmp_path, a_line(FEED, PRESS, events=events))["PRESS1"]
    assert {r["State"] for r in rows if 300 <= r["TSec"] < 400} == {4}


def test_two_presses_in_one_stage_share_the_feeder_and_both_count_for_the_line(tmp_path):
    first = {**PRESS, "stage": "PRESSES"}
    second = {"name": "PRESS2", "rate_per_min": 12, "scrap_pct": 5.0, "stage": "PRESSES"}
    tables = play(tmp_path, a_line(FEED, first, second))
    p1, p2, line = tables["PRESS1"][-1], tables["PRESS2"][-1], tables["Line"][-1]
    assert p1["GoodCount"] > 100 and p2["GoodCount"] > 100
    assert line["LineGoodCount"] == p1["GoodCount"] + p2["GoodCount"]
    # One feeder at 30 a minute feeds both 12-a-minute presses; neither is starved.
    assert p1["GoodCount"] + p1["ScrapCount"] == pytest.approx(p2["GoodCount"] + p2["ScrapCount"], rel=0.1)


def test_a_line_with_no_stages_has_one_place_per_station():
    stations = [{"name": n} for n in ("A", "B", "C")]
    assert gen.stage_of(stations) == [0, 1, 2]
    stations[1]["stage"] = stations[2]["stage"] = "S"
    assert gen.stage_of(stations) == [0, 1, 1]


def test_defect_kinds_add_up_to_the_scrap_and_a_burst_names_its_kind(tmp_path):
    check = {"name": "CHECK", "rate_per_min": 25, "scrap_pct": 4.0,
             "scrap_kinds": {"Dent": 2, "Scratch": 1}}
    burst = {"type": "scrap_burst", "station": "CHECK", "start": 600, "end": 720,
             "scrap_pct": 40.0, "scrap_kind": "Scratch"}
    rows = play(tmp_path, a_line(FEED, check, events=[burst]))["CHECK"]
    at = {r["TSec"]: r for r in rows}
    for row in rows[::89]:
        assert row["Dent"] + row["Scratch"] == row["ScrapCount"]
    assert at[719]["Dent"] == at[600]["Dent"], "the burst's scrap is all scratches"
    assert at[719]["Scratch"] - at[600]["Scratch"] >= 10


def test_an_identifier_tag_steps_and_is_written_as_text(tmp_path):
    press = {**PRESS, "texts": [{"name": "LotId", "every_s": 300, "format": "LOT-{n}", "first": 7}]}
    tables = play(tmp_path, a_line(FEED, press))
    values = [r["LotId"] for r in tables["PRESS1"]]
    assert values[0] == "LOT-7" and values[299] == "LOT-7" and values[300] == "LOT-8"
    manifest = load_manifest(tables["_out"])["tables"]["PRESS1"]["tags"]
    assert manifest["LotId"]["kind"] == "text"
    assert manifest["Good_A"]["kind"] == "counter" and manifest["State_B"]["integer"]
    schema = (tables["_out"] / "schema.ini").read_text(encoding="utf-8")
    assert "LotId Text" in schema and "Good_A Long" in schema


@pytest.mark.parametrize("line, said", [
    (a_line(FEED, {**PRESS, "sides": {"names": ["A"], "counters": {"G_{side}": "good"}}}), "two or more"),
    (a_line(FEED, {**PRESS, "sides": {"names": ["A", "B"], "counters": {"G": "good"}}}), "{side}"),
    (a_line(FEED, {**PRESS, "sides": {"names": ["A", "B"], "counters": {"G_{side}": "total"}}}), "{side}"),
    (a_line(FEED, {**PRESS, "scrap_kinds": {"Dent": 0}}), "positive number"),
    (a_line(FEED, {**PRESS, "texts": [{"name": "Lot", "every_s": 0}]}), "every_s"),
    (a_line(FEED, {**PRESS, "texts": [{"name": "Lot", "every_s": 9, "format": "a,{n}"}]}), "no comma"),
    (a_line(FEED, {**PRESS, "texts": [{"name": "GoodCount", "every_s": 9}]}), "twice"),
    (a_line({**FEED, "stage": "S"}, PRESS, {"name": "X", "stage": "S", "rate_per_min": 1}), "comes back"),
    (a_line(FEED, PRESS, events=[{"type": "down", "station": "PRESS1", "side": "C",
                                  "start": 1, "end": 5}]), "names side"),
    (a_line(FEED, PRESS, events=[{"type": "rate", "station": "PRESS1", "side": "A",
                                  "start": 1, "end": 5, "rate_per_min": 2}]), "only a"),
    (a_line(FEED, PRESS, events=[{"type": "down", "station": "PRESS1", "side": "A",
                                  "start": 1, "end": 5, "fault_code": 3}]), "no `fault_code`"),
    (a_line(FEED, PRESS, events=[{"type": "scrap_burst", "station": "PRESS1", "start": 1,
                                  "end": 5, "scrap_pct": 9, "scrap_kind": "Dent"}]), "scrap kind"),
])
def test_a_line_whose_sides_kinds_texts_or_stages_make_no_sense_is_refused(line, said):
    with pytest.raises(SystemExit) as refused:
        gen._validate_line(line, "test")
    assert said in str(refused.value)


# ------------------------------------------------------------- the replay


def test_the_replay_serves_an_identifier_as_text_and_a_side_counter_as_an_integer(tmp_path):
    press = {**PRESS, "texts": [{"name": "LotId", "every_s": 300, "format": "LOT-{n}"}]}
    out = play(tmp_path, a_line(FEED, press))["_out"]
    rows = csv_replay.load_table(out, "PRESS1")
    assert rows[0]["LotId"] == "LOT-1" and isinstance(rows[0]["Good_A"], int)
    path = a_map(tmp_path, extra_tags=["LotId", "State_A"])
    machines = load_tag_map(path, load_manifest(out))
    settings = Settings(tag_map_file=path, replay_dir=out,
                        opc_endpoint="opc.tcp://127.0.0.1:48402/freeopcua/server/")

    async def read() -> dict:
        server, replays, *_ = await csv_replay.build_server(settings, machines, out)
        await replays[0].write_row(300)
        idx = await server.get_namespace_index(settings.opc_namespace)
        obj = await server.nodes.objects.get_child(f"{idx}:PRESS1")
        seen = {}
        for tag in ("LotId", "Good_A", "State_A", "State"):
            variant = await (await obj.get_child(f"{idx}:{tag}")).read_data_value()
            seen[tag] = (variant.Value.VariantType, variant.Value.Value)
        return seen

    seen = asyncio.run(read())
    assert seen["LotId"] == (ua.VariantType.String, "LOT-2")
    assert seen["Good_A"][0] is ua.VariantType.Int64
    assert seen["State_A"][0] is ua.VariantType.Int32 and seen["State"][0] is ua.VariantType.Int32


def test_a_map_addressed_by_node_id_is_served_at_exactly_those_node_ids(tmp_path):
    """The same press, mapped the way a Kepware server names it - one flat
    string id per tag, named by whoever wrote the PLC program - runs against
    the replay unchanged: every node the agent will read is where the map says."""
    press = {**PRESS, "texts": [{"name": "LotId", "every_s": 300, "format": "LOT-{n}"}]}
    out = play(tmp_path, a_line(FEED, press))["_out"]
    nodes = {"State": "ns=2;s=Presses.Press_1.Status Word",
             "Good_A": "ns=2;s=Presses.Press_1.Tool A Good", "Good_B": "ns=2;s=Presses.Press_1.Tool B Good",
             "Scrap_A": "ns=2;s=Presses.Press_1.Tool A Scrap", "Scrap_B": "ns=2;s=Presses.Press_1.Tool B Scrap",
             "LotId": "ns=2;s=Presses.Press_1.Lot.Current Lot"}
    path = a_map(tmp_path, object="PRESS1", nodes=nodes, extra_tags=["LotId"])
    spec = load_tag_map(path, load_manifest(out))[0]
    assert set(spec.tags) == set(nodes)
    settings = Settings(tag_map_file=path, replay_dir=out,
                        opc_endpoint="opc.tcp://127.0.0.1:48403/freeopcua/server/")

    async def read() -> dict:
        server, replays, *_ = await csv_replay.build_server(settings, [spec], out)
        await replays[0].write_row(600)
        return {tag: await server.get_node(spec.node(tag)).read_value() for tag in spec.tags}

    seen = asyncio.run(read())
    rows = csv_replay.load_table(out, "PRESS1")
    assert seen["LotId"] == "LOT-3"
    assert seen["Good_A"] == rows[600]["Good_A"] and seen["Scrap_B"] == rows[600]["Scrap_B"]


# ------------------------------------- a machine whose state is its downtime code


def test_a_machine_with_only_a_downtime_code_is_down_on_a_code_and_running_on_zero(press, tmp_path):
    """Many PLCs keep no state word, only a downtime code that is zero while
    the machine may run. Read as the state, the one value stops the machine
    and names why; a code the vocabulary does not know is still a stop."""
    from fsmes.domain import EquipmentState, EquipmentStateName

    spec = load_tag_map(a_map(tmp_path, counters=None, state_tag="DowntimeCode",
                              reason_tag="DowntimeCode",
                              state_map={"0": "running", "*": "down"},
                              reason_map={"12": "die_jam"}))[0]
    assert spec.tags[0] == "DowntimeCode" and "State" not in spec.tags
    assert spec.acts_on("DowntimeCode") and not spec.acts_on("State")
    handler = agent._Handler({_Node(t): (spec, t) for t in spec.tags})

    def now() -> EquipmentState:
        machine = press.scalar(select(Equipment).where(Equipment.code == "PRESS-1"))
        return press.scalar(select(EquipmentState).where(
            EquipmentState.equipment_id == machine.id, EquipmentState.ended_at.is_(None)))

    _the_agent_reads(press, handler, spec, [("DowntimeCode", 0)])
    assert now().state is EquipmentStateName.RUNNING
    _the_agent_reads(press, handler, spec, [("DowntimeCode", 12)])
    stop = now()
    assert stop.state is EquipmentStateName.DOWN
    assert (stop.reason_code, stop.reason) == ("die_jam", "DowntimeCode 12")
    _the_agent_reads(press, handler, spec, [("DowntimeCode", 0), ("DowntimeCode", 77)])
    stop = now()
    assert stop.state is EquipmentStateName.DOWN
    assert (stop.reason_code, stop.reason) == (None, "DowntimeCode 77")


def test_a_state_map_without_a_star_still_refuses_a_value_it_does_not_list(tmp_path):
    spec = load_tag_map(a_map(tmp_path, state_map={"1": "running"}))[0]
    with pytest.raises(ValueError, match="state_map"):
        spec.to_state(7)
    star = load_tag_map(a_map(tmp_path, state_map={"1": "running", "*": "idle"}))[0]
    assert star.to_state(1).value == "running" and star.to_state(7).value == "idle"
