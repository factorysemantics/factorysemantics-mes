"""Two shape files, two plants, and neither one flattened into the other.

`skills/plant-from-your-mes/example/` ships two reference shapes, both profiled
out of `tests/unknown_mes.py` by the skill's own three commands:

- `plant-shape.toml` — the bottling line as a straight line: one area, a tag
  node per machine, and an MES with no routing table at all.
- `plant-shape-grouped.toml` — the same six machines in a brownfield plant: two
  areas with four cells under them, tags under channel/device folders where two
  machines share one PLC, and two routings that split and meet again.

The second one is the test. A converter that only ever saw a straight line
would quietly turn it into one, and nobody would notice until a customer said
"that is not my plant". So every structural thing the second shape says and the
first does not is asserted here: the areas in `equipment.json`, the routings in
`routings.json`, and the grouped browse path in `tag_map.json`.

Speed is at the bottom, twice — the conversion, and the shift the plant plays —
each as a ratio of a forty-station plant to this six-station one measured in the
same run on the same machine, never a clock.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from sqlalchemy import select

from fsmes.domain import Equipment, EquipmentLevel
from fsmes.integrations.opc.tag_map import load_tag_map
from fsmes.pack import check as pack_check
from fsmes.pack import masterdata
from fsmes.shape import from_shape, read_shape, to_pack

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "skills" / "plant-from-your-mes" / "example"
LINEAR = EXAMPLES / "plant-shape.toml"
GROUPED = EXAMPLES / "plant-shape-grouped.toml"

#: The buffer the converter asks for. Twenty is its default and the number the
#: CI job passes with `--yes`; nothing here is sensitive to the figure.
BUFFER = 20


def _pack(shape_file: Path, name: str, out: Path) -> Path:
    plant = from_shape(read_shape(shape_file), name=name, buffer=BUFFER)
    return to_pack(plant, out, echo=lambda _line: None)


@pytest.fixture(scope="module")
def packs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Both reference shapes, each carried to a pack folder once."""
    work = tmp_path_factory.mktemp("from-shape")
    return {
        "linear": _pack(LINEAR, "linear", work / "linear"),
        "grouped": _pack(GROUPED, "grouped", work / "grouped"),
    }


def _read(pack: Path, *parts: str) -> object:
    return json.loads(pack.joinpath(*parts).read_text(encoding="utf-8"))


def _by_code(equipment: list[dict]) -> dict[str, dict]:
    return {row["code"]: row for row in equipment}


# ------------------------------------------------------------ both of them

@pytest.mark.parametrize("which", ["linear", "grouped"])
def test_a_pack_built_from_a_shape_file_is_a_pack_this_release_will_run(
        packs: dict[str, Path], which: str) -> None:
    """`fsmes pack check`, on the pack the converter just wrote.

    The pack format is the contract between the converter and everything else
    in the product; a shape-built pack that the product's own checker refuses
    is a plant that will not start, found here instead of on the customer's
    laptop.
    """
    report = pack_check.check(packs[which])
    assert report.ok, "\n".join(report.render())
    assert report.checked >= 12, f"the check only read {report.checked} file(s)"


@pytest.mark.parametrize("which", ["linear", "grouped"])
def test_every_station_in_the_shape_is_a_work_unit_under_its_own_cell_or_area(
        packs: dict[str, Path], which: str) -> None:
    equipment = _read(packs[which], "masterdata", "equipment.json")
    rows = _by_code(equipment)
    units = [row for row in equipment if row["level"] == "work_unit"]
    assert len(units) == 6, f"{len(units)} work units, not the shape's six stations"
    for unit in units:
        parent = rows.get(unit["parent"])
        assert parent is not None, f"{unit['code']} hangs under {unit['parent']!r}, which is not declared"
        assert parent["level"] in ("work_center", "area"), (
            f"{unit['code']} hangs under a {parent['level']}")


@pytest.mark.parametrize("which", ["linear", "grouped"])
def test_the_pack_says_in_writing_what_the_shape_carried_and_the_plant_does_not_use(
        packs: dict[str, Path], which: str) -> None:
    """Definition of done 4: `[from_shape].dropped` is not empty and says why.

    The shape measures more than a simulated plant can honestly use. The rule
    is that nothing is dropped in silence, so the list is in the pack a person
    can read, with one line of reason each.
    """
    pack = tomllib.loads(packs[which].joinpath("plant.toml").read_text(encoding="utf-8"))
    said = pack["from_shape"]
    assert said["dropped"], "the pack does not say what it dropped"
    for entry in said["dropped"]:
        assert entry["field"], f"a dropped entry with no field name: {entry}"
        assert len(entry["why"].split()) >= 8, (
            f"{entry['field']} is dropped with no reason beside it: {entry['why']!r}")
    # Everything FORMAT.md lists as "no field yet" is either used or named here.
    named = {entry["field"] for entry in said["dropped"]}
    for field in ("stations.analogs[kind=counter]", "stations.analogs[kind=discrete]",
                  "stations.analogs.state_correlation"):
        assert field in named, f"{field} is neither used nor named as dropped"
    assert said["asked"] == [{"what": "buffer between each pair of machines", "pieces": BUFFER}]


# -------------------------------------------------------- the straight line

def test_the_straight_line_shape_gives_one_area_one_routing_and_a_node_a_machine(
        packs: dict[str, Path]) -> None:
    pack = packs["linear"]
    equipment = _read(pack, "masterdata", "equipment.json")
    areas = [row for row in equipment if row["level"] == "area"]
    assert len(areas) == 1, f"{len(areas)} areas from a shape that measured one line"

    routings = _read(pack, "masterdata", "routings.json")
    assert len(routings) == 1
    steps = [op["equipment"] for op in routings[0]["operations"]]
    assert steps == ["ST_01", "ST_02", "ST_03", "ST_04", "ST_05", "ST_06"]

    # The bottling MES behind this shape keeps its tags one machine to a folder
    # (`<plant>.<line>.<machine>.<tag>`), so the pack groups them the same way:
    # three folders deep, and no folder holding two machines.
    machines = load_tag_map(pack / "tag_map.json")
    assert len(machines) == 6
    folders = [machine.group for machine in machines]
    assert len(set(folders)) == 6, "two machines share a node in a plant that shares none"
    for machine in machines:
        assert machine.browse_path == (*machine.group, machine.object)


def test_the_straight_line_pack_states_that_nobody_measured_a_routing(
        packs: dict[str, Path]) -> None:
    """Unknown is not zero. The MES behind this shape has no routing table, so
    the one routing in the pack is the station order, and the pack says so."""
    text = packs["linear"].joinpath("plant.toml").read_text(encoding="utf-8")
    assert "no routing table" in text
    assert "never measured" in text or "not asked" in text or "was not asked" in text


def test_a_format_one_shape_still_means_one_area_one_line_and_a_node_per_machine(
        tmp_path: Path) -> None:
    """The promise in FORMAT.md, held as a test rather than as a sentence.

    A shape file written by the first skill has no `shape_format`, no
    `[hierarchy]`, no `[routings]` and no `[tag_nodes]`. It has to keep
    building the plant it always built: one area, the stations in sequence
    order, and every machine's tags at the top of the OPC tree.
    """
    raw = _synthetic(4, 1, 1)
    raw["meta"].pop("shape_format")
    for section in ("hierarchy", "routings", "tag_nodes"):
        raw.pop(section)
    for station in raw["stations"]:
        station.pop("area")
        station.pop("cell")

    from fsmes.shape import read as shape_read

    shape = shape_read.of(raw)
    assert shape.format == 1
    plant = from_shape(shape, name="old", buffer=BUFFER)
    pack = to_pack(plant, tmp_path / "old", echo=lambda _line: None)

    equipment = json.loads((pack / "masterdata" / "equipment.json").read_text(encoding="utf-8"))
    assert [row["code"] for row in equipment if row["level"] == "area"] == ["AR_01"]
    assert [row["code"] for row in equipment if row["level"] == "work_center"] == []
    routings = json.loads((pack / "masterdata" / "routings.json").read_text(encoding="utf-8"))
    assert len(routings) == 1
    assert [op["equipment"] for op in routings[0]["operations"]] == [
        "ST_01", "ST_02", "ST_03", "ST_04"]
    for machine in load_tag_map(pack / "tag_map.json"):
        assert machine.group == (), f"{machine.equipment} was put in a folder it never asked for"
        assert machine.browse_path == (machine.object,)


# ------------------------------------------------------------ the grouped one

def test_the_grouped_shape_gives_two_areas_with_their_cells_under_them(
        packs: dict[str, Path]) -> None:
    equipment = _read(packs["grouped"], "masterdata", "equipment.json")
    rows = _by_code(equipment)
    areas = [row for row in equipment if row["level"] == "area"]
    cells = [row for row in equipment if row["level"] == "work_center"]
    assert [row["code"] for row in areas] == ["AR_01", "AR_02"]
    assert [row["code"] for row in cells] == ["CL_01", "CL_02", "CL_03", "CL_04"]
    for area in areas:
        assert rows[area["parent"]]["level"] == "site"
    assert {row["parent"] for row in cells} == {"AR_01", "AR_02"}


def test_the_grouped_shape_keeps_a_routing_that_is_not_one_straight_line(
        packs: dict[str, Path]) -> None:
    """Two routings, two different first machines, one machine they share and
    one end they share. That is the shape format 1 could not describe."""
    routings = _read(packs["grouped"], "masterdata", "routings.json")
    assert len(routings) == 2
    walks = {r["code"]: [op["equipment"] for op in r["operations"]] for r in routings}
    assert sorted(walks.values()) == [
        ["ST_01", "ST_03", "ST_05", "ST_06"],
        ["ST_02", "ST_03", "ST_04", "ST_06"],
    ]
    first = {walk[0] for walk in walks.values()}
    assert len(first) == 2, "both routings start on the same machine"
    shared = set(walks["RT_01"]) & set(walks["RT_02"])
    assert shared == {"ST_03", "ST_06"}, f"the routings share {sorted(shared)}"
    for routing in routings:
        seqs = [op["seq"] for op in routing["operations"]]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_the_grouped_shape_puts_two_machines_under_one_opc_node(
        packs: dict[str, Path]) -> None:
    """A Kepware device is a PLC, not a machine. Two machines on one PLC browse
    through one node, and `tag_map.json` has to be able to say that."""
    machines = load_tag_map(packs["grouped"] / "tag_map.json")
    assert len(machines) == 6
    folders: dict[tuple[str, ...], list[str]] = {}
    for machine in machines:
        assert machine.group, f"{machine.equipment} is at the top level, not in a folder"
        assert len(machine.browse_path) == len(machine.group) + 1
        folders.setdefault(machine.group, []).append(machine.equipment)
    shared = {path: names for path, names in folders.items() if len(names) > 1}
    assert len(shared) == 2, f"expected two shared nodes, found {sorted(folders)}"
    assert sorted(sorted(names) for names in shared.values()) == [
        ["ST_01", "ST_02"], ["ST_04", "ST_05"],
    ]
    depths = {len(path) for path in folders}
    assert depths == {3}, f"the shape says the paths are three deep; the map says {depths}"


def test_the_grouped_plant_seeds_into_a_database_with_its_hierarchy_intact(
        packs: dict[str, Path], session) -> None:
    """The pack is only half the claim: the plant has to come up with two areas
    on it, which is what the Engineering pages read."""
    pack = packs["grouped"]
    cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(pack / "tag_map.json")}
    receipt = masterdata.seed(session, pack / "masterdata", cycles)
    session.flush()
    assert receipt["equipment"]["made"] == 14, receipt["equipment"]

    levels: dict[EquipmentLevel, set[str]] = {}
    for row in session.scalars(select(Equipment)):
        levels.setdefault(row.level, set()).add(row.code)
    # A superset, not an equality: seeding a pack also makes the rows the
    # maintenance and dispatch master data refer to, and this test is about
    # the shape's own hierarchy rather than about everything in the pack.
    assert {"AR_01", "AR_02"} <= levels[EquipmentLevel.AREA]
    assert {"CL_01", "CL_02", "CL_03", "CL_04"} <= levels[EquipmentLevel.WORK_CENTER]
    assert {f"ST_{n:02d}" for n in range(1, 7)} <= levels[EquipmentLevel.WORK_UNIT]
    for unit in session.scalars(
            select(Equipment).where(Equipment.level == EquipmentLevel.WORK_UNIT)):
        if not unit.code.startswith("ST_"):
            continue
        assert unit.parent is not None and unit.parent.parent is not None
        assert unit.parent.parent.level is EquipmentLevel.AREA


# ------------------------------------------------------------------- scale

def _synthetic(stations: int, areas: int, routings: int) -> dict:
    """A shape file with as many stations as asked for, built here.

    Nothing of anybody's: the generated names are the same `ST-nn` / `AR-nn` /
    `CL-nn` / `RT-nn` the skill writes, and the numbers are made up. It exists
    so scale can be measured against the six-station case without inventing a
    fortieth machine for the fixture database to replay.
    """
    per_area = max(stations // max(areas, 1), 1)
    station_rows = []
    area_rows: list[dict] = []
    for index in range(1, stations + 1):
        area = min((index - 1) // per_area + 1, areas)
        cell = (index - 1) // max(per_area // 2, 1) + 1
        station_rows.append({
            "name": f"ST-{index:02d}", "sequence": index,
            "area": f"AR-{area:02d}", "cell": f"CL-{cell:02d}",
            "rate_per_min": 40.0 + index % 7, "scrap_pct": 0.5,
            "good_total": 2000, "scrap_total": 10, "running_seconds": 3000,
            "analogs": [
                {"name": f"AN-{index:02d}", "kind": "analog", "unit": "degC",
                 "base": 60.0 + index, "noise": 1.5, "decimals": 1,
                 "running_only": True, "sample_interval_s": 10.0, "samples": 300},
                {"name": f"AN-{index:02d}b", "kind": "analog", "unit": "bar",
                 "base": 2.5, "noise": 0.1, "decimals": 2,
                 "running_only": True, "sample_interval_s": 10.0, "samples": 300},
            ],
        })
    for area in range(1, areas + 1):
        cells = sorted({row["cell"] for row in station_rows if row["area"] == f"AR-{area:02d}"})
        area_rows.append({"name": f"AR-{area:02d}", "parent": "", "cells": cells,
                          "stations": sum(1 for r in station_rows if r["area"] == f"AR-{area:02d}")})
    walks = []
    for number in range(1, routings + 1):
        steps = [row["name"] for i, row in enumerate(station_rows) if i % routings == number - 1]
        walks.append({"name": f"RT-{number:02d}", "steps": steps})
    nodes = []
    for index in range(0, stations, 2):
        pair = [row["name"] for row in station_rows[index:index + 2]]
        number = index // 2 + 1
        nodes.append({"name": f"ND-{number:02d}", "path": ["ND-00", f"ND-{number:02d}"],
                      "stations": pair, "tags": 2 * len(pair)})
    return {
        "meta": {"shape_format": 2, "written_by": "a test", "source_dialect": "sqlite",
                 "read_only": True, "rows_read": 0, "tables_total": 1,
                 "tables_mapped": 1, "tables_not_understood": 0,
                 "leak_check": "not run: this shape was written by a test"},
        "plant": {"stations_total": stations},
        "stations": station_rows,
        "hierarchy": {"found": True, "basis": "written by a test", "areas_seen": areas,
                      "cells_seen": len({r["cell"] for r in station_rows}),
                      "stations_placed": stations, "stations_total": stations,
                      "areas": area_rows},
        "tag_nodes": {"found": True, "basis": "written by a test", "nodes_seen": len(nodes),
                      "nodes_with_more_than_one_machine": len(nodes),
                      "depth_min": 2, "depth_max": 2, "nodes": nodes},
        "routings": {"found": True, "basis": "written by a test", "routings_seen": routings,
                     "steps_total": sum(len(w["steps"]) for w in walks),
                     "stations_on_a_routing": stations, "stations_total": stations,
                     "routings": walks},
        "stops": {"found": True, "intervals_total": 400, "seconds_total": 28800,
                  "stops_total": 120, "unlabelled_stops": 24, "unlabelled_share": 0.2,
                  "states": [{"state": "running", "share": 0.8, "intervals": 200,
                              "p50_seconds": 100, "max_seconds": 400},
                             {"state": "stopped", "share": 0.2, "intervals": 120,
                              "p50_seconds": 60, "max_seconds": 900}],
                  "reasons": [{"category": "mechanical", "share_of_stops": 0.6,
                               "share_of_stop_seconds": 0.6,
                               "generic_wording": "Mechanical fault"},
                              {"category": "material", "share_of_stops": 0.4,
                               "share_of_stop_seconds": 0.4,
                               "generic_wording": "Waiting for material"}]},
        "quality": {"found": True, "checks_total": 500, "fail_share": 0.05,
                    "dispositions": [{"disposition": "rework", "share": 0.6},
                                     {"disposition": "scrap", "share": 0.4}]},
        "orders": {"found": True, "orders_seen": 10, "qty_p50": 4800, "qty_p90": 5200},
        "shifts": {"found": True, "distinct_codes": 3,
                   "pattern": [{"starts_at_utc": "06:00", "hours": 8},
                               {"starts_at_utc": "14:00", "hours": 8},
                               {"starts_at_utc": "22:00", "hours": 8}]},
    }


@pytest.mark.slow
def test_forty_stations_cost_about_what_forty_stations_should(tmp_path: Path) -> None:
    """Scale as a ratio measured here, in this run, never as a number of seconds.

    A shared runner makes any absolute bound meaningless, so the six-station
    case and the forty-station case are both built on whatever machine is
    running the test and the ratio between them is what is held. Forty stations
    is 6.7 times six, and the honest expectation is linear-ish work: the gate
    is twice that, which still catches a converter that has gone quadratic
    (6.7 squared is 44) while saying nothing about how fast the box is.

    Two things are timed, because a plant is two pieces of work: turning the
    shape into a pack, which is this converter, and generating the shift the
    pack plays, which is the simulator underneath it. Forty stations is where
    the second one would hurt first.
    """
    import time

    from fsmes.shape import read as shape_read
    from fsmes.sim import generate

    def build(raw: dict, name: str) -> float:
        shape = shape_read.of(raw)
        started = time.perf_counter()
        plant = from_shape(shape, name=name, buffer=BUFFER)
        to_pack(plant, tmp_path / name, echo=lambda _line: None)
        return time.perf_counter() - started

    small = build(_synthetic(6, 1, 1), "small")
    big = build(_synthetic(40, 5, 3), "big")
    ratio = big / max(small, 1e-9)
    print(f"6 stations: {small:.3f} s; 40 stations: {big:.3f} s; ratio {ratio:.1f}x "
          f"for 6.7x the stations")
    assert ratio <= 13.4, (
        f"forty stations cost {ratio:.1f} times six stations ({big:.3f} s against "
        f"{small:.3f} s); 6.7 times the machines should not cost more than twice "
        f"that in work")
    big_pack = tmp_path / "big"

    # The conversion above is this branch's own code; the shift underneath a
    # plant is the simulator's, and forty stations is where that one would
    # hurt. So it is held the same way, in the same run, on the same machine:
    # half an hour of line time rather than the pack's eight, which is enough
    # to see the per-station cost and leaves nothing on disk (`simulate` is a
    # pure function of the config and returns its rows).
    def shift(pack: Path) -> float:
        config = generate.load_config(pack / "line.json")
        config["duration_s"] = 1800
        started = time.perf_counter()
        rows = generate.simulate(config)
        spent = time.perf_counter() - started
        assert len(rows) == len(config["stations"]) + 1   # a table each, and the line
        return spent

    small_shift = shift(tmp_path / "small")
    big_shift = shift(big_pack)
    shift_ratio = big_shift / max(small_shift, 1e-9)
    print(f"half an hour of line time - 6 stations: {small_shift:.3f} s; "
          f"40 stations: {big_shift:.3f} s; ratio {shift_ratio:.1f}x for 6.7x "
          f"the stations")
    assert shift_ratio <= 13.4, (
        f"a shift for forty stations cost {shift_ratio:.1f} times one for six "
        f"({big_shift:.3f} s against {small_shift:.3f} s); 6.7 times the "
        f"machines should not cost more than twice that in work")

    equipment = json.loads((big_pack / "masterdata" / "equipment.json").read_text(encoding="utf-8"))
    assert sum(1 for row in equipment if row["level"] == "work_unit") == 40
    assert sum(1 for row in equipment if row["level"] == "area") == 5
    routings = json.loads((big_pack / "masterdata" / "routings.json").read_text(encoding="utf-8"))
    assert len(routings) == 3
    assert pack_check.check(big_pack).ok
