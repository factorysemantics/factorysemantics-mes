"""The cutlery plant (labs/cutlery): ten million serialised pieces a day,
the shape the customer described, and the seed that lets every piece trace.
"""

import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from fsmes.domain import Equipment, LotConsumption, WorkOrder

LAB = Path("labs/cutlery")
WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build_config():
    return _load(LAB / "build_config.py", "cutlery_build_config")


@pytest.fixture(scope="module")
def plant(build_config):
    return build_config.build()


def test_the_plant_is_the_one_the_customer_described(build_config, plant):
    from fsmes.sim.generate import load_factory_config

    kinds = [ln["_meta"]["kind"] for ln in plant["lines"]]
    assert kinds.count("utensil") == 9 and kinds.count("stacker") == 32
    assert kinds.count("wrapper") == 16 and kinds.count("palletizer") == 4
    # Two stackers feed every wrapper, because a stack takes twice as long as a wrap.
    cells = {}
    for ln in plant["lines"]:
        if ln["_meta"]["kind"] in ("stacker", "wrapper"):
            cells.setdefault(ln["_meta"]["cell"], []).append(ln["_meta"]["kind"])
    assert all(sorted(v) == ["stacker", "stacker", "wrapper"] for v in cells.values()) and len(cells) == 16
    assert plant["_plant"]["stacker_cycle_s"] == pytest.approx(2 * plant["_plant"]["wrapper_cycle_s"], abs=0.005)
    # The arithmetic of sixty million ids a day.
    p = plant["_plant"]
    assert p["pieces_per_type_per_day"] == 10_000_000 and p["pieces_per_day"] == 30_000_000
    assert p["stacks_per_day"] == p["plates_per_day"] == p["wraps_per_day"] == 10_000_000
    assert 60_000_000 < p["ids_per_day"] < 60_100_000 and p["pallets_per_day"] == 41_666
    marker = next(ln for ln in plant["lines"] if ln["name"] == "FORK1")["stations"][-1]
    assert marker["rate_per_min"] == pytest.approx(10_000_000 / 3 / 1440, rel=0.001)
    assert marker["inspection"]["kind"] == "piece" and len(marker["inspection"]["attributes"]) == 4
    # Every line passes the single-line rules the generator enforces.
    tmp = LAB / "out" / "_test_line.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(plant), encoding="utf-8")
    try:
        lines = load_factory_config(tmp)
    finally:
        tmp.unlink()
    assert len(lines) == 61 and sum(len(c["stations"]) for _, c in lines) == 83


def test_the_seed_points_every_order_at_its_own_machine_with_its_lot_issued(session, plant, tmp_path):
    """Eight stackers make the same STACK-24; the routing chosen for an
    order is the first for the material, so each order is pointed at its
    own stacker afterwards - the late-binding dispatch the product lacks."""
    make_tag_map = _load(LAB / "make_tag_map.py", "cutlery_make_tag_map")
    init = _load(LAB / "init.py", "cutlery_init")
    (tmp_path / "line.json").write_text(json.dumps(plant), encoding="utf-8")
    (tmp_path / "tag_map.json").write_text(json.dumps(make_tag_map.build(tmp_path / "line.json")), encoding="utf-8")
    made = init.seed(session, tmp_path)
    init.release_orders(session, made["orders"])
    session.flush()
    orders = {wo.code: wo for wo in session.scalars(select(WorkOrder).where(WorkOrder.code.like("WO-%")))}
    for i in range(1, 33):
        wo = orders[f"WO-STACK{i:02d}"]
        assert [session.get(Equipment, op.equipment_id).code for op in wo.operations] == [f"STACK{i:02d}_Stacker"]
        assert wo.status.value == "released"
    for line in ("FORK1", "SPOON2", "KNIFE3"):
        assert session.scalar(select(LotConsumption).where(LotConsumption.work_order_id == orders[f"WO-{line}"].id))
    # The dimensional checks the certificate states a Cpk for: five per utensil.
    from fsmes.domain import Material, QualitySpec
    for material in ("UT-FORK", "UT-SPOON", "UT-KNIFE"):
        n = session.scalar(select(func.count(QualitySpec.id)).join(Material, QualitySpec.material_id == Material.id)
                           .where(Material.code == material))
        assert n == 5, material
    assert init.seed(session, tmp_path) is None, "idempotent: a restarted plant is not seeded twice"


def test_every_id_enters_as_an_observed_inspection_group(plant, tmp_path):
    """Nothing mints serials from counts any more: each marker, stacker,
    wrapper and palletizer publishes an inspection group per unit, the
    manifest describes it, and the agent maps every station that has one."""
    from fsmes.integrations.opc.agent import inspection_specs
    from fsmes.integrations.opc.tag_map import load_manifest, load_tag_map
    from fsmes.sim.generate import generate

    make_tag_map = _load(LAB / "make_tag_map.py", "cutlery_make_tag_map")
    (tmp_path / "line.json").write_text(json.dumps(plant), encoding="utf-8")
    (tmp_path / "tag_map.json").write_text(json.dumps(make_tag_map.build(tmp_path / "line.json")), encoding="utf-8")
    generate(tmp_path / "line.json", tmp_path / "out")
    manifest = load_manifest(tmp_path / "out")
    machines = load_tag_map(tmp_path / "tag_map.json", manifest)
    specs = inspection_specs(manifest, machines)
    kinds = {}
    for code, entry in specs.items():
        kinds.setdefault(entry["spec"]["kind"], []).append(code)
    assert {k: len(v) for k, v in kinds.items()} == {"piece": 9, "stack": 32, "wrap": 16, "pallet": 4}
    marker = specs["FORK1_Mark"]
    assert marker["tags"] >= {"InspSeq", "InspSerial", "InspPass", "InspMembers", "Insp_Length", "Insp_Gloss"}
    assert specs["WRAP01_Wrapper"]["spec"]["plate"]["material"] == "RAW-PLATE"
    assert specs["STACK01_Stacker"]["spec"]["takes"] == ["UT-FORK", "UT-SPOON", "UT-KNIFE"]
    # The group tags are subscribed at full rate, never sampled into history.
    fork = next(m for m in machines if m.equipment == "FORK1_Mark")
    assert "Insp_Length" in fork.tags and "InspSeq" in fork.tags


def test_the_replay_judges_exactly_what_the_counters_made_and_a_stack_takes_only_good_pieces():
    """The Inspector emits one group per counted unit - passes for good,
    a failed attribute for scrap - and the Flow hands stacks only pieces a
    marker judged good, in the order they were made."""
    import asyncio

    from fsmes.integrations.opc.csv_replay import Flow, Inspector
    from fsmes.integrations.opc.tag_map import MachineMap

    written: list[tuple[str, object, object]] = []

    class Node:
        def __init__(self, name):
            self.nodeid = name

    class Server:
        async def write_attribute_value(self, nodeid, dv):
            written.append((nodeid, dv.Value.Value, dv.SourceTimestamp))

    flow = Flow()
    attrs = [{"name": "Length", "nominal": 165.0, "min": 164.0, "max": 166.0, "sigma": 0.2},
             {"name": "Gloss", "nominal": 82.0, "min": 75.0, "max": 90.0, "sigma": 1.5}]
    tags = ["InspSeq", "InspSerial", "InspPass", "InspMembers", "Insp_Length", "Insp_Gloss"]
    mark = Inspector(MachineMap(equipment="FORK1_Mark", object="FORK1_Mark", cycle_seconds=0.03),
                     {"kind": "piece", "prefix": "F1", "material": "UT-FORK", "attributes": attrs},
                     {t: Node(f"mark.{t}") for t in tags}, flow, seed=1)
    server, stamp = Server(), __import__("datetime").datetime(2026, 9, 6, 12, 0, 0)
    asyncio.run(mark.emit(server, {"GoodCount": 100, "ScrapCount": 5}, stamp))     # baseline: nothing emitted
    n = asyncio.run(mark.emit(server, {"GoodCount": 103, "ScrapCount": 6}, stamp))
    assert n == 4 and mark.emitted == 4 and mark.failed == 1
    serials = [v for k, v, _ in written if k == "mark.InspSerial"]
    assert serials == ["F1-000000001", "F1-000000002", "F1-000000003", "F1-000000004"]
    passes = [v for k, v, _ in written if k == "mark.InspPass"]
    assert passes[:3] == [0, 0, 0] and passes[3] != 0, "the scrapped piece fails an attribute"
    stamps = {ts for k, _, ts in written if k == "mark.InspSeq"}
    assert len(stamps) == 4, "every event carries its own source time"
    assert list(flow.pieces["UT-FORK"]) == serials[:3], "only good pieces are offered to a stacker"

    stacker = Inspector(MachineMap(equipment="STACK01_Stacker", object="STACK01_Stacker", cycle_seconds=0.28),
                        {"kind": "stack", "prefix": "ST01", "material": "STACK-3", "members": 3, "cell": 1,
                         "takes": ["UT-FORK", "UT-SPOON", "UT-KNIFE"], "attributes": []},
                        {t: Node(f"stack.{t}") for t in tags[:4]}, flow, seed=2)
    asyncio.run(stacker.emit(server, {"GoodCount": 0, "ScrapCount": 0}, stamp))
    n = asyncio.run(stacker.emit(server, {"GoodCount": 1, "ScrapCount": 0}, stamp))
    assert n == 0 and flow.starved["STACK01_Stacker"] == 1, "no spoons or knives yet: starved, not invented"
    flow.pieces["UT-SPOON"] = __import__("collections").deque(["S1-000000001"])
    flow.pieces["UT-KNIFE"] = __import__("collections").deque(["K1-000000001"])
    n = asyncio.run(stacker.emit(server, {"GoodCount": 2, "ScrapCount": 0}, stamp))
    assert n == 1
    members = [v for k, v, _ in written if k == "stack.InspMembers"][-1]
    assert members == "F1-000000001,S1-000000001,K1-000000001"
    assert list(flow.stacks[1]) == ["ST01-000000001"]


def test_the_trace_screen_counts_in_full_and_draws_to_the_limit():
    js = (WEB / "trace.js").read_text(encoding="utf-8")
    assert "contains_total" in js and "units_inside" in js and "by_material" in js
    assert "and ${more.toLocaleString()} more" in js
    assert "via the order's consumption" in js
    assert "countUnits" not in js, "the screen no longer counts the tree itself"
