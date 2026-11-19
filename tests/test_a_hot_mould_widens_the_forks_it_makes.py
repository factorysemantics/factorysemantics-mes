"""A fork mould that runs hot makes forks whose width wanders, and the certificate says so.

The film's story (`labs/cutlery/scenes/hot-mould.json`): FORK1's mould stops
on a cooling fault, comes back with its melt climbing from 214 C to 238 C,
and from then on its forks' width spreads around the same 25 mm centre. A
pallet built before the fault carries a width Cpk of at least 1.33; one built
after carries less than 1.0, its mean still well inside the limits, and the
width chart's moving-range rule fires on the way.

Three layers, each tested where it lives:

- the spread rule itself (`fsmes.sim.measurement.PieceRule`), with no plant;
- the scene's own data - the generated MeltTemp of the three fork moulds and
  the checks the rule would draw off them - through `spc.capability` and the
  moving-range rule, with no plant;
- the floor taking a width check off a real (in-process) plant and naming
  the mould that made the piece.

And the everyday shift is pinned: the pack on disk carries no piece rules, a
floor without them takes none, and the scene builder leaves the pack's
generated hour byte-for-byte as it was on every line it does not name.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import importlib.util
import json
import random
import statistics
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import fsmes.domain  # noqa: F401  (register all tables)
from fsmes.api.app import create_app
from fsmes.config import get_settings
from fsmes.db import Base
from fsmes.domain import Equipment, QualityCheck, TagValue
from fsmes.integrations.opc.tag_map import load_tag_map
from fsmes.pack import masterdata
from fsmes.services import auth, spc
from fsmes.sim import generate, measurement
from fsmes.sim.operations import Floor

ROOT = Path(__file__).resolve().parents[1]
CUTLERY = ROOT / "labs" / "cutlery"
SCENE = CUTLERY / "scenes" / "hot-mould.json"
SUPERVISOR, SUPERVISOR_PASSWORD = "FLOOR-SUP", "supervisor"


def the_scene() -> dict:
    return json.loads(SCENE.read_text(encoding="utf-8"))


def builder():
    """`labs/cutlery/scene.py`, imported by path: a lab tool, not a package."""
    spec = importlib.util.spec_from_file_location("cutlery_scene", CUTLERY / "scene.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def the_story(scene: dict) -> dict[str, tuple[int, int]]:
    """The scene's own windows, in FORK1's line seconds: the stop, and the hot hold."""
    events = {e["type"]: (e["start"], e["end"]) for e in scene["events"]["FORK1"]}
    return {"stop": events["down"], "climb": events["drift"], "hot": events["offset"]}


def fork_width(scene: dict) -> measurement.PieceRule:
    return next(r for r in measurement.piece_rules(scene)
                if (r.material, r.characteristic) == ("UT-FORK", "width_mm"))


def width_spec() -> dict:
    specs = json.loads((CUTLERY / "masterdata" / "quality_specs.json").read_text(encoding="utf-8"))
    return next(s for s in specs if (s["material"], s["characteristic"]) == ("UT-FORK", "width_mm"))


# ------------------------------------------------- the spread, with no plant


def test_a_spread_that_follows_a_tag_grows_with_the_tag_and_leaves_the_mean_where_it_was():
    rule = fork_width(the_scene())
    rng = random.Random(1)
    cool = [rule.draw(rng, 214.0) for _ in range(4000)]
    hot = [rule.draw(rng, 238.0) for _ in range(4000)]

    assert statistics.stdev(cool) == pytest.approx(rule.sigma, rel=0.05)
    grown = rule.sigma + (238.0 - rule.follows.above) * rule.follows.sigma_per_unit
    assert statistics.stdev(hot) == pytest.approx(grown, rel=0.05)
    assert statistics.stdev(hot) > 4 * statistics.stdev(cool)
    # The mean does not move: the hot mould's forks are as wide on average.
    assert statistics.fmean(cool) == pytest.approx(rule.centre, abs=0.01)
    assert statistics.fmean(hot) == pytest.approx(rule.centre, abs=0.05)


def test_below_the_tag_threshold_the_spread_is_the_healthy_one():
    rule = fork_width(the_scene())
    assert rule.spread(205.0) == rule.spread(rule.follows.above) == rule.sigma


def test_the_same_seed_draws_the_same_pieces():
    rule = fork_width(the_scene())
    first, second = random.Random(42), random.Random(42)
    assert [rule.draw(first, 230.0) for _ in range(50)] == [rule.draw(second, 230.0) for _ in range(50)]


def test_a_spread_that_follows_a_tag_will_not_be_drawn_without_a_reading_of_it():
    """Unknown is not zero: no reading is no measurement, not the healthy spread."""
    with pytest.raises(ValueError):
        fork_width(the_scene()).spread(None)


def test_a_floor_script_with_no_pieces_section_has_no_piece_rules():
    assert measurement.piece_rules({}) == []
    assert measurement.piece_rules({"gauges": {}}) == []


# ------------------------------------------- the scene's own data, no plant


@pytest.fixture(scope="module")
def scene_hour(tmp_path_factory) -> dict[str, dict[int, float]]:
    """The scene's replayed hour for the three fork moulds: MeltTemp by line second."""
    module = builder()
    line = json.loads((CUTLERY / "line.json").read_text(encoding="utf-8"))
    path = tmp_path_factory.mktemp("scene") / "line.json"
    path.write_text(json.dumps(module.scened_line(line, the_scene())), encoding="utf-8")
    out = path.parent / "out"
    generate.generate(path, out, write_docs=False)
    temps: dict[str, dict[int, float]] = {}
    for mould in ("FORK1_Mold", "FORK2_Mold", "FORK3_Mold"):
        with (out / f"{mould}.csv").open(encoding="ascii", newline="") as f:
            temps[mould] = {int(row["TSec"]): float(row["MeltTemp"]) for row in csv.DictReader(f)}
    return temps


def the_checks_the_floor_would_take(rule, temps, seed: int = 7) -> list[tuple[int, str, float]]:
    """One fork-width check every `every_line_s` of the replayed hour, off a fork
    one of the three moulds made, with the spread that mould's melt reads then."""
    rng = random.Random(seed)
    checks = []
    for t in range(0, len(temps["FORK1_Mold"]), int(rule.every_line_s)):
        mould = rng.choice(sorted(rule.stations))
        hour = temps[mould]
        checks.append((t, mould, rule.draw(rng, hour[t % len(hour)])))
    return checks


def test_only_the_named_mould_runs_hot_and_only_after_its_stop(scene_hour):
    fork1, story = scene_hour["FORK1_Mold"], the_story(the_scene())
    above = fork_width(the_scene()).follows.above
    hot = range(*story["hot"])
    assert max(fork1[t] for t in range(0, story["stop"][0])) < above
    assert statistics.fmean(fork1[t] for t in hot) > 236
    assert min(fork1[t] for t in hot) > above
    for other in ("FORK2_Mold", "FORK3_Mold"):
        assert max(scene_hour[other].values()) < above


@pytest.mark.parametrize("seed", [7, 11, 2026])
def test_the_scene_gives_a_pallet_cpk_above_1_33_before_the_fault_and_under_1_after_it(scene_hour, seed):
    rule, spec = fork_width(the_scene()), width_spec()
    checks = the_checks_the_floor_would_take(rule, scene_hour, seed)
    story = the_story(the_scene())
    # A certificate reads the checks from its pallet's first piece to its
    # close (`coa.gather_pallet`) - about 1,600 line seconds on this plant at
    # its teaching pace, so a pallet wholly before the stop and one wholly in
    # the hot hold, measured as the floor measures them.
    pallet = 1600
    before = [v for t, _, v in checks if story["stop"][0] - pallet <= t < story["stop"][0]]
    after = [v for t, _, v in checks if story["hot"][1] - pallet <= t < story["hot"][1]]
    fewest = spc.MIN_POINTS
    assert len(before) >= fewest and len(after) >= fewest

    healthy = spc.capability(before, spec["min"], spec["max"], fewest=fewest)
    hot = spc.capability(after, spec["min"], spec["max"], fewest=fewest)

    assert healthy["cpk"] >= 1.33, healthy
    assert hot["cpk"] < 1.0, hot
    # The mean stays inside the limits - this is spread, not a shift.
    assert spec["min"] < hot["centre"] < spec["max"]
    assert abs(hot["centre"] - 25.0) < 0.3


def test_the_width_charts_moving_range_rule_fires_once_the_mould_runs_hot(scene_hour):
    rule = fork_width(the_scene())
    checks = the_checks_the_floor_would_take(rule, scene_hour)
    story = the_story(the_scene())
    taken = [(t, v) for t, _, v in checks if t < story["hot"][1]][-spc.HISTORY:]
    stamps, history = [t for t, _ in taken], [v for _, v in taken]
    ranges = spc._moving_ranges(history)
    upper = spc.D4_N2 * statistics.fmean(ranges)

    fired = spc._moving_range_rule(ranges, upper)

    assert fired, "no moving range beyond its upper limit"
    # Every one of them is after the fault, none in the healthy stretch.
    assert all(stamps[f["index"] + 1] > story["stop"][1] for f in fired)


# ------------------------------------------------- the floor, on a real plant


@pytest.fixture()
def plant(tmp_path, monkeypatch):
    """One cutlery plant, seeded from the shipped pack, in a file of its own."""
    from fsmes.config import get_settings as settings_cache
    from fsmes.db import get_engine, get_sessionmaker

    url = f"sqlite:///{(tmp_path / 'plant.db').as_posix()}"
    monkeypatch.setenv("MES_DATABASE_URL", url)
    monkeypatch.setenv("MES_PLANT_TIMEZONE", "UTC")
    monkeypatch.setenv("MES_TAG_RETENTION_DAYS", "0")
    monkeypatch.setenv("MES_TAG_MAP_FILE", str(CUTLERY / "tag_map.json"))
    for leaked in ("MES_MODULES", "MES_WORDS", "MES_PLANT_NAME", "MES_PLANT_LABEL",
                   "MES_REPLAY_DIR", "MES_SIM_SPEED"):
        monkeypatch.delenv(leaked, raising=False)
    for cache in (settings_cache, get_engine, get_sessionmaker):
        cache.cache_clear()

    engine = get_engine()
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(CUTLERY / "tag_map.json")}
    masterdata.seed(session, CUTLERY / "masterdata", cycles)
    auth.ensure_builtin_roles(session)
    auth.create_user(session, code=SUPERVISOR, name="Simulated shift supervisor",
                     password=SUPERVISOR_PASSWORD, role="supervisor")
    session.commit()

    yield session

    session.close()
    engine.dispose()
    for cache in (settings_cache, get_engine, get_sessionmaker):
        cache.cache_clear()


def the_mould_reads(session: Session, mould: str, melt: float) -> None:
    machine = session.scalar(select(Equipment).where(Equipment.code == mould))
    row = TagValue(equipment_id=machine.id, tag=f"{mould}.MeltTemp", value_num=melt)
    row.ts = datetime.now(UTC).replace(tzinfo=None)
    session.add(row)
    session.commit()


def on_the_floor(session: Session, script: dict, work, *, seed: int = 7) -> None:
    session.commit()
    app = create_app()

    async def _run():
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://plant")
        async with client:
            floor = Floor(get_settings(), client, random.Random(seed), script)
            await floor.sign_in(SUPERVISOR, SUPERVISOR_PASSWORD)
            await work(floor)

    asyncio.run(_run())
    session.expire_all()


def scene_floor() -> dict:
    floor = json.loads((CUTLERY / "floor.json").read_text(encoding="utf-8"))
    return builder().scened_floor(floor, the_scene())


async def specs_and_orders(floor: Floor):
    specs = await floor.every("/quality/specs")
    orders = (await floor.get("/workorders", status=["released", "running"], limit=500))["items"]
    return specs, orders


def test_a_cutlery_width_check_names_the_mould_that_made_the_fork(plant):
    for mould in ("FORK1_Mold", "FORK2_Mold", "FORK3_Mold"):
        the_mould_reads(plant, mould, 214.0)

    async def work(floor: Floor):
        specs, orders = await specs_and_orders(floor)
        rule = next(r for r in floor.pieces if r.characteristic == "width_mm"
                    and r.material == "UT-FORK")
        for _ in range(12):
            assert await floor.inspect_a_piece(rule, specs, orders)

    on_the_floor(plant, scene_floor(), work)

    checks = list(plant.scalars(select(QualityCheck)))
    assert len(checks) == 12
    named = {plant.get(Equipment, c.equipment_id).code for c in checks if c.equipment_id}
    assert all(c.equipment_id is not None for c in checks)  # every check names a station...
    assert named <= {"FORK1_Mold", "FORK2_Mold", "FORK3_Mold"}  # ...and it is a fork mould
    assert len(named) > 1  # ...and not always the same one
    assert all(c.work_order_id is not None for c in checks)


def test_a_mould_with_no_melt_reading_has_no_fork_measured_off_it(plant):
    """Unknown is not zero: with no MeltTemp the spread cannot be said, so no check."""

    async def work(floor: Floor):
        specs, orders = await specs_and_orders(floor)
        rule = next(r for r in floor.pieces if r.characteristic == "width_mm"
                    and r.material == "UT-FORK")
        assert not await floor.inspect_a_piece(rule, specs, orders)

    on_the_floor(plant, scene_floor(), work)
    assert list(plant.scalars(select(QualityCheck))) == []


def test_the_floor_leaves_the_characteristics_its_piece_rules_measure_to_them(plant):
    """With piece rules, the timed inspection takes no stand-in of those characteristics."""
    for mould in ("FORK1_Mold", "FORK2_Mold", "FORK3_Mold"):
        the_mould_reads(plant, mould, 214.0)

    async def work(floor: Floor):
        specs, orders = await specs_and_orders(floor)
        machines = (await floor.get("/dashboard/summary")).get("machines", [])
        await floor.inspect(specs, orders, machines)

    on_the_floor(plant, scene_floor(), work)
    assert list(plant.scalars(select(QualityCheck))) == []


# --------------------------------------------- the everyday shift, unchanged


def test_the_cutlery_pack_on_disk_carries_no_piece_rules():
    everyday = json.loads((CUTLERY / "floor.json").read_text(encoding="utf-8"))
    assert "pieces" not in everyday
    assert measurement.piece_rules(everyday) == []


def test_a_floor_with_no_piece_rules_takes_its_inspections_exactly_as_before(plant):
    """Same seed, same plant: the timed inspection draws what it always drew.

    The piece filter is skipped outright when there are no rules, so it takes
    nothing from the floor's random stream; what the everyday floor records
    is the plausible stand-in, with no station, as it was.
    """
    everyday = json.loads((CUTLERY / "floor.json").read_text(encoding="utf-8"))

    async def work(floor: Floor):
        assert floor.pieces == []
        specs, orders = await specs_and_orders(floor)
        machines = (await floor.get("/dashboard/summary")).get("machines", [])
        await floor.inspect(specs, orders, machines)

    on_the_floor(plant, everyday, work)
    checks = list(plant.scalars(select(QualityCheck)))
    assert checks, "the everyday floor still inspects"
    assert all(c.equipment_id is None for c in checks)


def _digest(folder: Path, names: list[str]) -> str:
    h = hashlib.sha256()
    for name in sorted(names):
        h.update(name.encode())
        h.update((folder / name).read_bytes())
    return h.hexdigest()


def test_building_the_scene_leaves_the_pack_and_every_line_it_does_not_name_byte_for_byte(tmp_path):
    before = {p: p.read_bytes() for p in (CUTLERY / "line.json", CUTLERY / "floor.json")}
    built = builder().build(SCENE, tmp_path / "hot-mould")
    for path, data in before.items():
        assert path.read_bytes() == data

    everyday = tmp_path / "everyday"
    generate.generate(CUTLERY / "line.json", everyday, write_docs=False)
    tables = sorted(p.name for p in everyday.glob("*.csv"))
    untouched = [n for n in tables if not n.startswith("FORK1_")]
    assert len(untouched) > 100
    assert _digest(everyday, untouched) == _digest(built / "out", untouched)
    assert (everyday / "schema.ini").read_bytes() == (built / "out" / "schema.ini").read_bytes()
