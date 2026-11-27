"""A heater band that burns out runs a fork mould cold, and its repair order says how long.

The scene (`labs/cutlery/scenes/coil-out.json`, the design in the crew's
`reports/sim-designs/2026-11-27-coil-out.md`): zone 2 of FORK1's barrel
heater fails at line minute 30, the melt sags from 214 C to about 199 C while
its setpoint holds, the forks come out short and light and the scrap climbs,
the length chart fires, the operator stops the mould at minute 44 and a
corrective maintenance order is raised, the band is replaced, and the mould
restarts at minute 86 with good forks: 42 minutes down.

Three layers, each where it lives:

- the cold half of a piece rule (`measurement.PieceRule`), with no plant;
- the scene's generated hour and the checks a floor would take off it, before
  and after the fault, with no plant;
- FORK1_Mold's hour played into a real (in-process) plant - its states and
  its counts, minute by minute - while the floor watches it: exactly one
  corrective order, raised, started and closed with the findings and the
  minutes down, and the answer key's *what happened* read back out of the
  database. The key's other half, *what the ERP should show*, is fixed
  numbers checked against nothing: no price or cost goes into the MES.

And the everyday shift is pinned: the pack on disk has no breakdowns, a floor
without them makes no request for one, and the builder leaves every line the
scene does not name byte-for-byte as it was.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import importlib.util
import json
import random
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import fsmes.domain  # noqa: F401  (register all tables)
from fsmes.api.app import create_app
from fsmes.config import get_settings
from fsmes.db import Base
from fsmes.domain import (
    Equipment,
    EquipmentState,
    EquipmentStateName,
    MaintenanceOrder,
    TagValue,
)
from fsmes.integrations.opc.tag_map import load_tag_map
from fsmes.pack import masterdata as pack_masterdata
from fsmes.services import auth, equipment, execution, masterdata, spc
from fsmes.sim import breakdown, generate, measurement
from fsmes.sim.operations import Floor

ROOT = Path(__file__).resolve().parents[1]
CUTLERY = ROOT / "labs" / "cutlery"
SCENE = CUTLERY / "scenes" / "coil-out.json"
SUPERVISOR, SUPERVISOR_PASSWORD = "FLOOR-SUP", "supervisor"
MOULD = "FORK1_Mold"


def the_scene() -> dict:
    return json.loads(SCENE.read_text(encoding="utf-8"))


def builder():
    """`labs/cutlery/scene.py`, imported by path: a lab tool, not a package."""
    spec = importlib.util.spec_from_file_location("cutlery_scene", CUTLERY / "scene.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fork_length(scene: dict) -> measurement.PieceRule:
    return next(r for r in measurement.piece_rules(scene)
                if (r.material, r.characteristic) == ("UT-FORK", "length_mm"))


def the_spec(characteristic: str) -> dict:
    specs = json.loads((CUTLERY / "masterdata" / "quality_specs.json").read_text(encoding="utf-8"))
    return next(s for s in specs if (s["material"], s["characteristic"])
                == ("UT-FORK", characteristic))


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """The scene built into a copy of the pack, its hour generated, its key written."""
    return builder().build(SCENE, tmp_path_factory.mktemp("coil") / "coil-out")


@pytest.fixture(scope="module")
def key(built) -> dict:
    return json.loads((built / "answer-key.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def hour(built) -> list[dict]:
    with (built / "out" / f"{MOULD}.csv").open(encoding="ascii", newline="") as f:
        return list(csv.DictReader(f))


# ------------------------------------------------ the cold half, no plant


def test_a_piece_that_follows_a_tag_below_its_threshold_comes_out_short_by_the_rule():
    rule = fork_length(the_scene())
    rng = random.Random(1)
    warm = [rule.draw(rng, 214.0) for _ in range(4000)]
    cold = [rule.draw(rng, 199.0) for _ in range(4000)]

    assert statistics.fmean(warm) == pytest.approx(rule.centre, abs=0.01)
    shift = (rule.follows.below - 199.0) * rule.follows.centre_per_unit
    assert shift < 0
    assert statistics.fmean(cold) == pytest.approx(rule.centre + shift, abs=0.01)
    # The spread does not move: a cold barrel short-fills every fork alike.
    assert statistics.stdev(cold) == pytest.approx(statistics.stdev(warm), rel=0.06)


def test_above_the_threshold_the_centre_is_the_healthy_one():
    rule = fork_length(the_scene())
    assert rule.centre_at(214.0) == rule.centre_at(rule.follows.below) == rule.centre


def test_a_centre_that_follows_a_tag_will_not_be_drawn_without_a_reading_of_it():
    """Unknown is not zero: no reading is no measurement, not the healthy centre."""
    with pytest.raises(ValueError):
        fork_length(the_scene()).centre_at(None)


def test_a_rule_that_says_below_without_how_far_the_centre_moves_is_refused():
    with pytest.raises(ValueError):
        measurement.piece_rules({"pieces": {"every_line_s": 90, "rules": [{
            "material": "UT-FORK", "characteristic": "length_mm", "stations": [MOULD],
            "centre": 165.0, "sigma": 0.15, "follows": {"tag": "MeltTemp", "below": 206}}]}})


# --------------------------------------------- the scene's hour, no plant


def _mean_melt(hour: list[dict], start: int, end: int) -> float:
    return statistics.fmean(float(r["MeltTemp"]) for r in hour[start:end])


def _scrap_pct(hour: list[dict], start: int, end: int) -> float:
    made = int(hour[end]["TotalCount"]) - int(hour[start]["TotalCount"])
    return 100.0 * (int(hour[end]["ScrapCount"]) - int(hour[start]["ScrapCount"])) / made


def _lengths(built: Path, scene: dict, start: int, end: int) -> list[float]:
    stream = breakdown.check_stream(scene, built / "out", "UT-FORK length_mm",
                                    scene["answer"]["chart_seed"])
    return [v for t, mould, v in stream if start <= t < end and mould == MOULD]


def test_before_minute_30_the_mould_runs_warm_its_forks_are_capable_and_its_scrap_is_at_base(
        built, hour):
    scene, spec = the_scene(), the_spec("length_mm")
    assert _mean_melt(hour, 0, 1800) == pytest.approx(214.0, abs=1.0)
    lengths = _lengths(built, scene, 0, 1800)
    assert len(lengths) >= spc.MIN_POINTS
    assert spc.capability(lengths, spec["min"], spec["max"], fewest=spc.MIN_POINTS)["cpk"] >= 1.33
    assert _scrap_pct(hour, 700, 1800) < 1.0


def test_after_minute_30_the_melt_is_12_degrees_under_its_setpoint_and_the_forks_are_short(
        built, hour, key):
    scene = the_scene()
    setpoint = 214.0
    # The last two minutes before the operator stops it, and the stop itself.
    assert _mean_melt(hour, 2520, 2640) <= setpoint - 12.0
    assert statistics.fmean(_lengths(built, scene, 2160, 2640)) \
        < statistics.fmean(_lengths(built, scene, 0, 1800)) - 0.5
    assert _scrap_pct(hour, 2400, 2640) > 8.0
    fired = key["what_happened"]["minute_chart_fired"]
    assert fired is not None and 30.0 < fired < 44.0


def test_after_the_restart_the_melt_and_the_forks_are_back(built, hour):
    scene = the_scene()
    after = 5160
    assert int(hour[after + 60]["State"]) == 1
    assert _mean_melt(hour, after + 30, len(hour)) == pytest.approx(214.0, abs=2.5)
    assert statistics.fmean(_lengths(built, scene, after + 60, len(hour))) \
        == pytest.approx(165.0, abs=0.15)
    assert _scrap_pct(hour, after + 60, len(hour) - 1) < 1.5


def test_the_answer_key_is_two_halves_and_the_erp_half_is_the_designs_numbers(key):
    erp = key["what_the_erp_should_show"]
    assert erp["part"]["price"] == 140.0 and erp["part"]["quantity"] == 1
    assert erp["labour"] == {"hours": 0.7, "rate_per_hour": 60.0, "cost": 42.0}
    assert erp["total"] == pytest.approx(140.0 + 42.0 + erp["scrap"]["cost"])
    assert erp["output_lost"]["cost"] is None  # counted, never priced by invention
    what = key["what_happened"]
    assert (what["minute_failed"], what["minute_stopped"], what["minute_restarted"],
            what["minutes_down"]) == (30.0, 44.0, 86.0, 42.0)
    assert 30.0 < what["minute_left_band"] < what["minute_stopped"]
    # No cost of any kind in the half the MES is checked against.
    assert not any("cost" in k or "price" in k for k in what if k != "cost_center")


# ------------------------------------------- the hour, played into a plant


@pytest.fixture()
def plant(tmp_path, monkeypatch, built):
    """One cutlery plant, seeded from the scene's copy of the pack."""
    from fsmes.config import get_settings as settings_cache
    from fsmes.db import get_engine, get_sessionmaker

    url = f"sqlite:///{(tmp_path / 'plant.db').as_posix()}"
    monkeypatch.setenv("MES_DATABASE_URL", url)
    monkeypatch.setenv("MES_PLANT_TIMEZONE", "UTC")
    monkeypatch.setenv("MES_TAG_RETENTION_DAYS", "0")
    monkeypatch.setenv("MES_TAG_MAP_FILE", str(built / "tag_map.json"))
    for leaked in ("MES_MODULES", "MES_WORDS", "MES_PLANT_NAME", "MES_PLANT_LABEL",
                   "MES_REPLAY_DIR", "MES_SIM_SPEED"):
        monkeypatch.delenv(leaked, raising=False)
    for cache in (settings_cache, get_engine, get_sessionmaker):
        cache.cache_clear()

    engine = get_engine()
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(built / "tag_map.json")}
    pack_masterdata.seed(session, built / "masterdata", cycles)
    auth.ensure_builtin_roles(session)
    auth.create_user(session, code=SUPERVISOR, name="Simulated shift supervisor",
                     password=SUPERVISOR_PASSWORD, role="supervisor")
    session.commit()

    yield session

    session.close()
    engine.dispose()
    for cache in (settings_cache, get_engine, get_sessionmaker):
        cache.cache_clear()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Replay:
    """FORK1_Mold's generated hour, played into the plant as its agent would:
    each change of state at its own line second, the counts minute by minute,
    and the newest melt reading as the tag's present value. One line second
    is one plant second, so the floor watching it runs at speed 1."""

    def __init__(self, session: Session, rows: list[dict]):
        self.session, self.rows = session, rows
        self.machine = session.scalar(select(Equipment).where(Equipment.code == MOULD))
        self.states = next(m for m in load_tag_map(CUTLERY / "tag_map.json")
                           if m.equipment == MOULD).state_map
        self.start = _now() - timedelta(seconds=len(rows))
        self.played, self.state = 0, None

    def at(self, t: int) -> datetime:
        return self.start + timedelta(seconds=t)

    def to(self, t: int) -> None:
        for i in range(self.played, t + 1):
            row = self.rows[i]
            state = self.states[str(row["State"])]
            if state != self.state:
                equipment.set_state(self.session, equipment_code=MOULD, state=state,
                                    at=self.at(i))
                self.state = state
            if i % 60 == 59:
                back = self.rows[i - 59 - 1] if i >= 60 else {"GoodCount": 0, "ScrapCount": 0}
                good = int(row["GoodCount"]) - int(back["GoodCount"])
                scrap = int(row["ScrapCount"]) - int(back["ScrapCount"])
                if good or scrap:
                    execution.record_unassigned(self.session, equipment=self.machine,
                                                good=good, scrap=scrap, ts=self.at(i))
        self.played = t + 1
        self.session.execute(delete(TagValue).where(TagValue.equipment_id == self.machine.id))
        reading = TagValue(equipment_id=self.machine.id, tag=f"{MOULD}.MeltTemp",
                           value_num=float(self.rows[t]["MeltTemp"]))
        reading.ts = _now()
        self.session.add(reading)
        self.session.commit()


def the_floor_watches(session: Session, script: dict, rows: list[dict]) -> Replay:
    replay = Replay(session, rows)
    app = create_app()

    async def _run():
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://plant")
        async with client:
            floor = Floor(get_settings(), client, random.Random(7), script, speed=1.0)
            await floor.sign_in(SUPERVISOR, SUPERVISOR_PASSWORD)
            for t in range(0, len(rows), 60):
                replay.to(t)
                await floor.answer_the_breakdowns()

    asyncio.run(_run())
    session.expire_all()
    return replay


def scene_floor() -> dict:
    floor = json.loads((CUTLERY / "floor.json").read_text(encoding="utf-8"))
    return builder().scened_floor(floor, the_scene())


def test_the_floor_raises_one_corrective_order_for_the_cold_stop_and_closes_it_with_the_minutes_down(
        plant, hour, key):
    the_floor_watches(plant, scene_floor(), hour)
    what = key["what_happened"]

    machine = plant.scalar(select(Equipment).where(Equipment.code == MOULD))
    orders = list(plant.scalars(select(MaintenanceOrder)))
    assert len(orders) == 1, [(o.code, o.summary) for o in orders]  # not the warm stop at 10
    order = orders[0]
    assert order.equipment_id == machine.id
    assert order.kind.value == "corrective"
    assert order.status.value == "done"
    assert order.findings == "Heater zone 2 open circuit, band replaced"
    assert order.performed_by == "MT-01"
    assert order.skill_code == "ELEC"
    assert order.downtime_minutes == what["minutes_down"] == 42.0
    assert order.downtime_minutes / 60.0 == pytest.approx(what["technician_hours"])


def test_what_the_answer_key_says_happened_is_what_the_database_holds(plant, built, hour, key):
    replay = the_floor_watches(plant, scene_floor(), hour)
    what = key["what_happened"]
    machine = plant.scalar(select(Equipment).where(Equipment.code == MOULD))

    assert masterdata.work_center_of(plant, machine).code == what["work_center"]
    assert masterdata.cost_center(plant, machine) == what["cost_center"]

    # The stop the order is for, as the MES's state history holds it.
    downs = [s for s in plant.scalars(select(EquipmentState)
                                      .where(EquipmentState.equipment_id == machine.id))
             if s.state == EquipmentStateName.DOWN]
    long_one = max(downs, key=lambda s: s.ended_at - s.started_at)
    assert (long_one.started_at - replay.start).total_seconds() / 60 == what["minute_stopped"]
    assert (long_one.ended_at - replay.start).total_seconds() / 60 == what["minute_restarted"]

    # The forks, as the MES counted them.
    def counted(first_s: int, last_s: int) -> tuple[float, float]:
        sums = equipment.production_sums(plant, [machine.id], replay.at(first_s),
                                         replay.at(last_s))
        good, scrap, _ = sums.get(machine.id, (0.0, 0.0, 0.0))
        return good, scrap

    failed_s, stop_s = int(what["minute_failed"] * 60), int(what["minute_stopped"] * 60)
    good, scrap = counted(failed_s, stop_s - 1)
    line = json.loads((built / "line.json").read_text(encoding="utf-8"))
    mould = next(st for entry in line["lines"] if entry["name"] == "FORK1"
                 for st in entry["stations"] if st["name"] == "Mold")
    everyday = float(mould["scrap_pct"])
    assert round(scrap - (good + scrap) * everyday / 100.0) == what["forks_scrapped"]

    healthy_good, _ = counted(690, failed_s - 1)
    per_minute = healthy_good / ((failed_s - 690) / 60.0)
    assert round(per_minute * what["minutes_down"]) == pytest.approx(what["output_lost"], abs=3)


# --------------------------------------------- the everyday shift, unchanged


def test_the_cutlery_pack_on_disk_has_no_breakdowns_and_no_scene_people():
    everyday = json.loads((CUTLERY / "floor.json").read_text(encoding="utf-8"))
    assert breakdown.breakdowns(everyday) == []
    assert not (CUTLERY / "masterdata" / "personnel.json").exists()


def test_a_floor_with_no_breakdowns_makes_no_request_for_one():
    calls: list[str] = []

    async def _run():
        def answer(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(200, json=[])
        client = httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="http://plant")
        async with client:
            floor = Floor(get_settings(), client, random.Random(7),
                          json.loads((CUTLERY / "floor.json").read_text(encoding="utf-8")))
            assert await floor.answer_the_breakdowns() == []

    asyncio.run(_run())
    assert calls == []


def test_no_fork_is_measured_off_a_mould_that_is_down(plant):
    """It makes nothing while it is down, so nothing comes off it to measure."""
    machine = plant.scalar(select(Equipment).where(Equipment.code == MOULD))
    reading = TagValue(equipment_id=machine.id, tag=f"{MOULD}.MeltTemp", value_num=199.0)
    reading.ts = _now()
    plant.add(reading)
    equipment.set_state(plant, equipment_code=MOULD, state=EquipmentStateName.DOWN)
    plant.commit()
    script = scene_floor()
    script["pieces"]["rules"] = [dict(r, stations=[MOULD]) for r in script["pieces"]["rules"]
                                 if r["characteristic"] == "length_mm"]
    app = create_app()

    async def _run():
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://plant")
        async with client:
            floor = Floor(get_settings(), client, random.Random(7), script)
            await floor.sign_in(SUPERVISOR, SUPERVISOR_PASSWORD)
            specs = await floor.every("/quality/specs")
            orders = (await floor.get("/workorders", status=["released", "running"],
                                      limit=500))["items"]
            assert not await floor.inspect_a_piece(floor.pieces[0], specs, orders)
            equipment.set_state(plant, equipment_code=MOULD, state=EquipmentStateName.RUNNING)
            plant.commit()
            assert await floor.inspect_a_piece(floor.pieces[0], specs, orders)

    asyncio.run(_run())


def test_a_down_mould_with_no_melt_reading_raises_nothing():
    """Unknown is not cold: a stop nobody can read the melt of is nobody's breakdown."""
    rule = breakdown.breakdowns(the_scene())[0]
    assert breakdown.decide(rule, "down", 100.0, None, None, 10.0).act is None
    assert breakdown.decide(rule, "down", 100.0, 214.0, None, 10.0).act is None
    assert breakdown.decide(rule, "down", 100.0, 199.0, None, 10.0).act == "raise"
    closed = breakdown.decide(rule, "running", 352.0, None, 100.0, 10.0)
    assert (closed.act, closed.downtime_minutes) == ("close", 42.0)


def _digest(folder: Path, names: list[str]) -> str:
    h = hashlib.sha256()
    for name in sorted(names):
        h.update(name.encode())
        h.update((folder / name).read_bytes())
    return h.hexdigest()


def test_building_the_scene_leaves_the_pack_and_every_line_it_does_not_name_byte_for_byte(
        tmp_path):
    watched = [CUTLERY / "line.json", CUTLERY / "floor.json",
               *sorted((CUTLERY / "masterdata").glob("*.json"))]
    before = {p: p.read_bytes() for p in watched}
    built = builder().build(SCENE, tmp_path / "coil-out")
    for path, data in before.items():
        assert path.read_bytes() == data
    assert not (CUTLERY / "masterdata" / "personnel.json").exists()

    everyday = tmp_path / "everyday"
    generate.generate(CUTLERY / "line.json", everyday, write_docs=False)
    tables = sorted(p.name for p in everyday.glob("*.csv"))
    untouched = [n for n in tables if not n.startswith("FORK1_")]
    assert len(untouched) > 100
    assert _digest(everyday, untouched) == _digest(built / "out", untouched)


def test_the_scenes_places_are_the_nine_moulds_drawn_not_measured():
    places = json.loads((CUTLERY / "coil_places.json").read_text(encoding="utf-8"))
    assert "drawn, not measured" in places["_what"]
    moulds = places["moulds"]
    assert len(moulds) == 9, [m["equipment"] for m in moulds]
    assert len({m["equipment"] for m in moulds}) == 9
    for mould in moulds:
        assert mould["melt_temp_sensor"]["id"] == f"{mould['equipment']}.MeltTemp"
        assert len(mould["heater_bands"]) == 4
        assert {b["zone"] for b in mould["heater_bands"]} == {1, 2, 3, 4}
