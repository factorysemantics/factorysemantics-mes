"""The body shop: eight cells, twenty robots inside them, one hour of line.

`labs/multiplant/bodyshop/` is the first pack whose machines have machines
inside them. A robot publishes a state, a fault number, its weld current and
its tip wear and counts nothing; its cell counts the bodies, and the cell's
state follows its robots. These tests read the pack as shipped and the hour
its `line.json` generates, and hold the stories that hour is written to tell:
a tip dress is setup, a robot fault is its cell down with the robot's reason,
the cell after it starves rather than breaks, and the OEE an MES computes
from the replay is the replay's own arithmetic.

Nothing here listens on a port and nothing reads a wall clock against a
bound: the OEE is checked as a ratio of the replay's own counts.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from fsmes.config import get_settings
from fsmes.db import utcnow
from fsmes.domain import (
    Equipment,
    EquipmentLevel,
    EquipmentState,
    EquipmentStateName,
    ProductionLog,
    ProductionSource,
    WorkOrder,
)
from fsmes.integrations.opc import agent
from fsmes.integrations.opc.tag_map import load_manifest, load_tag_map
from fsmes.pack import check as checker
from fsmes.pack import masterdata as pack_masterdata
from fsmes.sim import generate as gen
from fsmes.sim.operations import keep_planned

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "labs" / "multiplant" / "bodyshop"
CELLS = ("UB10", "UB20", "FR10", "FR20", "RS10", "RS20", "MF10", "QI10")
ROBOTS = {"UB10": 4, "UB20": 3, "FR10": 4, "FR20": 3, "RS10": 3, "RS20": 2, "MF10": 0, "QI10": 1}

#: The tag map's codes, read once: 0 stopped, 1 running, 2 starved,
#: 3 blocked, 4 down, 5 changeover (the generator's own numbering).
RUNNING, STARVED, BLOCKED, DOWN, CHANGEOVER = 1, 2, 3, 4, 5

_KEPT = {"MES_PLANT_TIMEZONE", "MES_DATABASE_URL", "MES_TEST_DATABASE_URL"}


@pytest.fixture(autouse=True)
def _a_plant_nobody_else_configured(monkeypatch):
    """A suite that has applied a pack hands the next test a plant somebody
    else configured; these tests are about what this pack says."""
    for name in [k for k in os.environ if k.startswith("MES_") and k not in _KEPT]:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def hour(tmp_path_factory) -> dict[str, list[dict]]:
    """The hour the pack plays, generated from its `line.json` into a
    directory of this test's own, as {table: [row, ...]} with integer
    states and fault codes."""
    out = tmp_path_factory.mktemp("bodyshop-out")
    gen.generate(PACK / "line.json", out, write_docs=True)
    tables: dict[str, list[dict]] = {"_out": out}
    for path in sorted(out.glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as f:
            tables[path.stem] = [
                {k: (int(v) if k in ("TSec", "State", "FaultCode", "GoodCount", "TipWear") else v)
                 for k, v in row.items()}
                for row in csv.DictReader(f)
            ]
    return tables


@pytest.fixture()
def bodyshop(session):
    """The pack's master data, seeded the way `fsmes pack apply` seeds it:
    rated cycles read from the pack's own tag map."""
    cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(PACK / "tag_map.json")}
    pack_masterdata.seed(session, PACK / "masterdata", cycles)
    session.flush()
    return session


def spells(rows: list[dict], state: int) -> list[tuple[int, int]]:
    """(first second, seconds) of every run of `state` in a table."""
    found, start = [], None
    for row in rows:
        if row["State"] == state and start is None:
            start = row["TSec"]
        elif row["State"] != state and start is not None:
            found.append((start, row["TSec"] - start))
            start = None
    if start is not None:
        found.append((start, rows[-1]["TSec"] + 1 - start))
    return found


# ------------------------------------------------------------------ the pack


def test_the_pack_checks_clean_and_the_fleet_lists_it():
    report = checker.check(PACK)
    assert report.ok, [str(p) for p in report.problems]
    fleet = (REPO / "labs" / "multiplant" / "fleet.toml").read_text(encoding="utf-8")
    assert '"bodyshop"' in fleet


def test_the_pack_loads_and_names_eight_cells_and_twenty_robots_as_their_children(bodyshop):
    line = bodyshop.scalar(select(Equipment).where(Equipment.code == "BS1"))
    cells = bodyshop.scalars(select(Equipment).where(Equipment.parent_id == line.id)).all()
    assert sorted(c.code for c in cells) == sorted(CELLS)
    assert all(c.level is EquipmentLevel.WORK_UNIT for c in cells)

    robots = 0
    for cell in cells:
        children = bodyshop.scalars(select(Equipment).where(Equipment.parent_id == cell.id)).all()
        assert sorted(r.code for r in children) == [f"{cell.code}-R{n}" for n in range(1, ROBOTS[cell.code] + 1)]
        assert all(r.level is EquipmentLevel.EQUIPMENT_MODULE for r in children)
        robots += len(children)
    assert robots == 20


def test_a_robot_counts_nothing_and_its_cell_counts_the_bodies(hour):
    """Read as the agent reads it: the tag map completed from the manifest
    the generator writes beside the hour."""
    manifest = load_manifest(hour["_out"])
    machines = {m.equipment: m for m in load_tag_map(PACK / "tag_map.json", manifest)}
    assert len(machines) == 28
    for cell in CELLS:
        assert machines[cell].counts and "GoodCount" in machines[cell].tags
    robot = machines["FR10-R2"]
    assert not robot.counts
    assert "GoodCount" not in robot.tags and "ScrapCount" not in robot.tags
    assert {"State", "FaultCode", "WeldCurrent", "TipWear"} <= set(robot.tags)


def test_a_robots_weld_cycle_ends_inside_its_cells_cycle():
    machines = {m.equipment: m for m in load_tag_map(PACK / "tag_map.json")}
    for code, spec in machines.items():
        if "-" in code:
            cell = machines[code.split("-")[0]]
            assert spec.cycle_seconds < cell.cycle_seconds, (code, spec.cycle_seconds, cell.cycle_seconds)


def test_the_book_holds_more_than_a_day_of_the_bottleneck_in_two_body_styles():
    book = json.loads((PACK / "masterdata" / "work_orders.json").read_text(encoding="utf-8"))
    slowest = max(m.cycle_seconds for m in load_tag_map(PACK / "tag_map.json"))
    assert sum(r["quantity"] for r in book) / (3600 / slowest) >= 24.0
    assert {r["material"] for r in book} == {"BIW-S4", "BIW-E5"}
    assert all(r["code"].startswith("BLD-") for r in book)
    assert [r["code"] for r in book if r.get("release", True)] == ["BLD-110201"]
    dues = [r["due_in_hours"] for r in book]
    assert dues == sorted(set(dues))
    floor = json.loads((PACK / "floor.json").read_text(encoding="utf-8"))
    assert keep_planned(floor) >= 2


# ------------------------------------------------------- the hour it plays


def test_the_replays_first_hour_holds_tip_dresses_a_robot_fault_a_starved_spell_and_a_noisy_metal_finish(hour):
    dressed = [t for t, rows in hour.items() if "_R" in t and spells(rows, CHANGEOVER)]
    assert len(dressed) >= 2, f"only {dressed} dressed their tips"
    # The tip wear counter says why: it climbs, and drops to zero at a dress.
    wear = [r["TipWear"] for r in hour["FR10_R1"]]
    assert max(wear) >= 280 and wear.count(0) > 1

    assert spells(hour["FR10_R2"], DOWN) == [(900, 480)]
    assert {r["FaultCode"] for r in hour["FR10_R2"] if r["State"] == DOWN} == {101}
    assert {r["FaultCode"] for r in hour["RS10_R1"] if r["State"] == DOWN} == {102}

    starved = spells(hour["FR20"], STARVED)
    assert any(900 <= t <= 1380 and length >= 300 for t, length in starved), starved

    def spread(table: str) -> float:
        cycles = [int(r["CycleTimeMs"]) for r in hour[table] if r["State"] == RUNNING]
        return max(cycles) - min(cycles)
    assert spread("MF10") > 5 * max(spread(c) for c in CELLS if c != "MF10")
    mf10_rate = 60 / json.loads((PACK / "line.json").read_text(encoding="utf-8"))["stations"][6]["rate_per_min"]
    assert mf10_rate > 60.0, "metal finish is slower than a 60 s takt"


def test_a_robot_fault_makes_its_cell_down_with_that_robots_fault(hour):
    cell = {r["TSec"]: r for r in hour["FR10"]}
    for row in hour["FR10_R2"]:
        if row["State"] == DOWN:
            assert cell[row["TSec"]]["State"] == DOWN
            assert cell[row["TSec"]]["FaultCode"] == row["FaultCode"] == 101


def test_a_tip_dress_is_setup_not_down(hour):
    machines = {m.equipment: m for m in load_tag_map(PACK / "tag_map.json")}
    cell = {r["TSec"]: r for r in hour["UB10"]}
    dresses = spells(hour["UB10_R1"], CHANGEOVER)
    assert dresses, "UB10's first robot never dressed its tips"
    for start, length in dresses:
        assert length == 8
        assert cell[start]["State"] == CHANGEOVER
    assert machines["UB10-R1"].to_state(CHANGEOVER) is EquipmentStateName.SETUP
    assert machines["UB10"].to_state(CHANGEOVER) is EquipmentStateName.SETUP
    floor = json.loads((PACK / "floor.json").read_text(encoding="utf-8"))
    assert floor["stops"]["changeover"] == "tip_dress"


def test_a_starved_cell_is_starved_not_down(hour):
    machines = {m.equipment: m for m in load_tag_map(PACK / "tag_map.json")}
    during = [r for r in hour["FR20"] if 1000 <= r["TSec"] < 1300]
    assert {r["State"] for r in during} == {STARVED}
    assert {r["FaultCode"] for r in during} == {0}
    assert machines["FR20"].to_state(STARVED) is EquipmentStateName.IDLE
    assert machines["FR20"].to_state(BLOCKED) is EquipmentStateName.IDLE


# ---------------------------------------- the agent books the robot's reason


class _Node:
    def __init__(self, name):
        self.name = name


def _the_agent_reads(session, spec, readings) -> None:
    handler = agent._Handler({_Node(t): (spec, t) for t in spec.tags})
    handler._book(session, [(spec, tag, value) for tag, value in readings], {})
    session.flush()


def _open_stop(session, code: str) -> EquipmentState:
    machine = session.scalar(select(Equipment).where(Equipment.code == code))
    return session.scalar(select(EquipmentState).where(
        EquipmentState.equipment_id == machine.id, EquipmentState.ended_at.is_(None)))


@pytest.mark.parametrize("order", ["fault number first", "state first"])
def test_a_robot_fault_books_its_cell_down_with_the_robots_reason(bodyshop, order):
    """The State and the fault number are two data changes and arrive in
    either order; the stop is named the same way both times."""
    machines = {m.equipment: m for m in load_tag_map(PACK / "tag_map.json")}
    readings = [("FaultCode", 101), ("State", DOWN)]
    if order == "state first":
        readings.reverse()
    for code in ("FR10-R2", "FR10"):
        _the_agent_reads(bodyshop, machines[code], readings)
        stop = _open_stop(bodyshop, code)
        assert stop.state is EquipmentStateName.DOWN
        assert stop.reason_code == "weld_gun_fault"
        assert stop.reason == "FaultCode 101"


def test_a_fault_number_the_map_does_not_know_is_kept_as_said_with_no_code(bodyshop):
    spec = {m.equipment: m for m in load_tag_map(PACK / "tag_map.json")}["RS10"]
    _the_agent_reads(bodyshop, spec, [("FaultCode", 999), ("State", DOWN)])
    stop = _open_stop(bodyshop, "RS10")
    assert stop.reason_code is None and stop.reason == "FaultCode 999"


def test_a_tip_dress_and_a_starve_are_booked_with_no_fault_reason(bodyshop):
    machines = {m.equipment: m for m in load_tag_map(PACK / "tag_map.json")}
    _the_agent_reads(bodyshop, machines["UB10"], [("FaultCode", 0), ("State", CHANGEOVER)])
    _the_agent_reads(bodyshop, machines["FR20"], [("FaultCode", 0), ("State", STARVED)])
    dress, starve = _open_stop(bodyshop, "UB10"), _open_stop(bodyshop, "FR20")
    assert dress.state is EquipmentStateName.SETUP and dress.reason is None
    assert starve.state is EquipmentStateName.IDLE and starve.reason is None


# ---------------------------------------------------------- OEE and layout


def test_the_oee_over_the_replay_matches_the_replays_own_count_within_one_per_cent(bodyshop, hour):
    """Book the hour as an agent would - state intervals and counter deltas,
    placed an hour back - and the line's OEE per cell is the replay's own
    good x rated cycle / observed seconds. A ratio, never a clock."""
    from fsmes.services import analysis

    machines = {m.equipment: m for m in load_tag_map(PACK / "tag_map.json")}
    order = bodyshop.scalar(select(WorkOrder).where(WorkOrder.code == "BLD-110201"))
    base = utcnow().replace(microsecond=0) - timedelta(seconds=3600)
    for code in CELLS:
        spec, rows = machines[code], hour[code]
        machine = bodyshop.scalar(select(Equipment).where(Equipment.code == code))
        start, state = 0, rows[0]["State"]
        for row in [*rows[1:], None]:
            if row is None or row["State"] != state:
                end = rows[-1]["TSec"] + 1 if row is None else row["TSec"]
                bodyshop.add(EquipmentState(
                    equipment_id=machine.id, state=spec.to_state(state),
                    started_at=base + timedelta(seconds=start), ended_at=base + timedelta(seconds=end)))
                if row is not None:
                    start, state = row["TSec"], row["State"]
        last = 0
        for row in rows:
            if row["GoodCount"] > last:
                bodyshop.add(ProductionLog(
                    work_order_id=order.id, equipment_id=machine.id, good_qty=row["GoodCount"] - last,
                    scrap_qty=0, source=ProductionSource.OPC, ts=base + timedelta(seconds=row["TSec"])))
                last = row["GoodCount"]
    bodyshop.flush()

    stations = {s["code"]: s for s in analysis.oee_breakdown(bodyshop, line_code="BS1", hours=1.0)["stations"]}
    assert set(stations) == set(CELLS), "a robot is not a station on the line"
    for code in CELLS:
        rows = hour[code]
        good = rows[-1]["GoodCount"]
        replay = good * machines[code].cycle_seconds / len(rows)
        measured = stations[code]
        assert measured["good_qty"] == pytest.approx(good, rel=0.01), code
        assert measured["oee"] == pytest.approx(replay, rel=0.01), (code, measured, replay)
    # The bottleneck's breakdown is downtime; the tip dresses beside it are not.
    fr10 = stations["FR10"]
    assert fr10["downtime_seconds"] == pytest.approx(480, abs=2), fr10["seconds_by_state"]
    assert fr10["seconds_by_state"]["setup"] > 0


def test_line_layout_names_eight_stations_with_positions(bodyshop, client, monkeypatch):
    monkeypatch.setattr(get_settings(), "line_layout_file", PACK / "line_layout.json")
    answer = client.get("/line/layout", params={"line": "BS1"}).json()
    stations = answer["stations"]
    assert [s["code"] for s in stations] == list(CELLS)
    assert [s["x"] for s in stations] == [12.0 * i for i in range(8)]
    assert {s["z"] for s in stations} == {0.0}


# ------------------------------------- the general pieces, refused when wrong


def _a_line(**station) -> dict:
    base = {"name": "C1", "rate_per_min": 1.0, "units": [{"name": "R1", "ops_per_piece": 10, "cycle_s": 20}]}
    base.update(station)
    return {"seed": 1, "duration_s": 60, "buffers": {"capacity": 3}, "stations": [base], "events": []}


@pytest.mark.parametrize("line, said", [
    (_a_line(units=[{"name": "R-1"}]), "unit name"),
    (_a_line(units=[{"name": "R1"}, {"name": "R1"}]), "duplicate unit"),
    (_a_line(units=[{"name": "R1", "ops_per_piece": 10, "service": {"every": 0, "duration_s": 8}}]),
     "has a `service`"),
    (_a_line(cycle_cv=1.5), "cycle_cv"),
])
def test_a_line_whose_units_make_no_sense_is_refused_with_the_reason(line, said):
    with pytest.raises(SystemExit) as refused:
        gen._validate_line(line, "test")
    assert said in str(refused.value)


@pytest.mark.parametrize("event, said", [
    ({"type": "down", "station": "C1", "unit": "R9", "start": 1, "end": 5}, "names unit"),
    ({"type": "rate", "station": "C1", "unit": "R1", "start": 1, "end": 5, "rate_per_min": 2}, "only a"),
    ({"type": "down", "station": "C1", "start": 1, "end": 5, "fault_code": "101"}, "fault_code"),
])
def test_a_unit_event_that_names_nothing_real_is_refused(event, said):
    line = _a_line()
    line["events"] = [event]
    with pytest.raises(SystemExit) as refused:
        gen._validate_line(line, "test")
    assert said in str(refused.value)


def test_a_line_with_no_units_and_no_fault_numbers_publishes_no_fault_column(tmp_path):
    """Every line written before units existed generates what it did before:
    no FaultCode, no unit tables."""
    out = tmp_path / "out"
    gen.generate(REPO / "labs" / "multiplant" / "machining" / "line.json", out, write_docs=False)
    assert sorted(p.stem for p in out.glob("*.csv")) == ["Deburr", "Line", "Mill", "Saw"]
    header = (out / "Mill.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "FaultCode" not in header


def test_a_reason_map_with_no_reason_tag_is_refused(tmp_path):
    path = tmp_path / "tag_map.json"
    path.write_text(json.dumps({"machines": [{
        "equipment": "C1", "object": "C1", "cycle_seconds": 60, "reason_map": {"1": "x"}}]}),
        encoding="utf-8")
    with pytest.raises(ValueError, match="reason_tag"):
        load_tag_map(path)


def test_every_tag_the_map_subscribes_to_is_a_column_of_the_hour(hour):
    """The replay server refuses a tag its CSV does not have, so a cell that
    names a fault-number tag it never publishes is a plant that does not
    start. MF10 is people, not robots, and names none."""
    manifest = load_manifest(hour["_out"])
    for spec in load_tag_map(PACK / "tag_map.json", manifest):
        header = set(hour[spec.object][0])
        missing = [tag for tag in spec.tags if tag not in header]
        assert not missing, f"{spec.equipment} subscribes to {missing}, which {spec.object}.csv lacks"
