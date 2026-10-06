"""Behind every point on the fill-weight chart there is a story to find.

Scott, 2026-10-05: *"What source did it come from? What's that source's
calibration? How does it compare to other measurements? How does it compare to
telemetry?"* Measured on the bottling lab plant the day before: **0 of the
last 500 quality checks named a gauge**, seven days of downtime were **100 %
unlabelled**, and **887 non-conformances** had been raised and never reviewed.
A panel that opened on a point would have had the operator, the order and the
neighbouring points to show, and nothing about the instrument or the machine.

So this file is about the data half. Five causes are planted - four in the
line's own script (`labs/kepsim/line.json`) and two in the plant pack's floor
script (`labs/multiplant/bottling/floor.json`, which also plants the night
shift) - and every test here asserts **the trace a cause leaves in the
records**, keyed on the pack's own declaration of it rather than on a count
inside a loop. A story nobody can find in the records is not planted, it is
just written down.

**Nothing here listens on a port.** The floor talks to its plant over HTTP, so
the plant is the real application answering over an in-process ASGI transport:
the same routers, the same database, the same sign-in, and nothing for the
operating system to leave running afterwards. The same arrangement as
`test_lab_order_book.py`, and for the same reason.
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import fsmes.domain  # noqa: F401  (register all tables)
from fsmes.api.app import create_app
from fsmes.config import get_settings
from fsmes.db import Base
from fsmes.domain import (
    EquipmentStateName,
    Gauge,
    NcStatus,
    NonConformance,
    QualityCheck,
    TagValue,
)
from fsmes.integrations.opc.tag_map import load_tag_map
from fsmes.pack import masterdata
from fsmes.services import auth
from fsmes.services import equipment as equipment_service
from fsmes.sim import generate, measurement
from fsmes.sim.operations import Floor

ROOT = Path(__file__).resolve().parents[1]
BOTTLING = ROOT / "labs" / "multiplant" / "bottling"
LINE = ROOT / "labs" / "kepsim" / "line.json"
SCRIPT = BOTTLING / "floor.json"

SUPERVISOR = "FLOOR-SUP"
SUPERVISOR_PASSWORD = "supervisor"
FILLER = "FILL01"
#: What the pack says the filler's scales are called. Read from the pack, not
#: repeated here: a test that hard-coded the codes would pass on a pack that
#: had lost them.
STEADY, DRIFTING = "SCALE-FILL-01", "SCALE-FILL-02"


def script() -> dict:
    return measurement.load(SCRIPT)


# ----------------------------------------------------------------- the plant


@pytest.fixture()
def plant(tmp_path, monkeypatch):
    """One bottling plant, seeded from the shipped pack, in a file of its own.

    A file and this plant's own `MES_DATABASE_URL`, because the floor is an
    HTTP client whose requests are solved in a worker thread - a test that
    overrode only the dependencies would be half on this plant and half on
    whatever database the process last had.
    """
    from fsmes.config import get_settings as settings_cache
    from fsmes.db import get_engine, get_sessionmaker

    url = f"sqlite:///{(tmp_path / 'plant.db').as_posix()}"
    monkeypatch.setenv("MES_DATABASE_URL", url)
    monkeypatch.setenv("MES_PLANT_TIMEZONE", "UTC")
    monkeypatch.setenv("MES_TAG_RETENTION_DAYS", "0")
    for leaked in ("MES_MODULES", "MES_WORDS", "MES_PLANT_NAME", "MES_PLANT_LABEL",
                   "MES_REPLAY_DIR", "MES_SIM_SPEED", "MES_TAG_MAP_FILE"):
        monkeypatch.delenv(leaked, raising=False)
    for cache in (settings_cache, get_engine, get_sessionmaker):
        cache.cache_clear()

    engine = get_engine()
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(BOTTLING / "tag_map.json")}
    masterdata.seed(session, BOTTLING / "masterdata", cycles)
    auth.ensure_builtin_roles(session)
    auth.create_user(session, code=SUPERVISOR, name="Simulated shift supervisor",
                     password=SUPERVISOR_PASSWORD, role="supervisor")
    session.commit()

    yield session

    session.close()
    engine.dispose()
    for cache in (settings_cache, get_engine, get_sessionmaker):
        cache.cache_clear()


def on_the_floor(session: Session, work, *, seed: int = 7) -> None:
    """Run one coroutine of the floor's against this plant, as the supervisor.

    The supervisor, because everything here that writes - a check, a stop's
    label, a disposition - is gated, and the simulated floor runs as two
    identities for exactly that reason. One account in a test keeps the file
    about what is recorded rather than about who may record it;
    `tests/test_auth.py` is where the gates themselves are pinned.
    """
    session.commit()
    app = create_app()

    async def _run():
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                   base_url="http://plant")
        async with client:
            floor = Floor(get_settings(), client, random.Random(seed), script())
            await floor.sign_in(SUPERVISOR, SUPERVISOR_PASSWORD)
            await floor.read_the_register()
            await work(floor)

    asyncio.run(_run())
    session.expire_all()


def the_filler_reports(session: Session, weight: float, *, seconds_ago: float = 0.0) -> None:
    """A fill-weight sample arriving from the machine, as the OPC agent books it."""
    machine = equipment_service.masterdata.get_equipment(session, FILLER)
    row = TagValue(equipment_id=machine.id, tag=f"{FILLER}.FillWeight", value_num=weight)
    row.ts = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=seconds_ago)
    session.add(row)
    session.commit()


async def the_specs(floor: Floor) -> list[dict]:
    return await floor.every("/quality/specs")


async def the_machines(floor: Floor) -> list[dict]:
    return (await floor.get("/dashboard/summary")).get("machines", [])


def the_filler_showing(weight: float, *, seconds_ago: float = 0.0) -> list[dict]:
    """The machine rows as `/dashboard/summary` reports them, built here.

    `inspect` takes the machines as an argument, and handing them over
    directly is what keeps a test about *one* reading: the real summary is
    cached for a second against a stamp that a tag value does not change, so
    six readings taken inside one second through the endpoint would all be
    the first one. That the endpoint reports the moment a reading was taken
    at all is pinned on its own, below.
    """
    at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=seconds_ago)
    return [{"code": FILLER, "state": "running",
             "analog": {"name": "FillWeight", "value": weight, "at": at.isoformat()}}]


async def the_orders(floor: Floor) -> list[dict]:
    return (await floor.get("/workorders", status=["released", "running"], limit=500))["items"]


def the_checks(session: Session) -> list[QualityCheck]:
    return list(session.scalars(select(QualityCheck).order_by(QualityCheck.id)))


def gauge_of(session: Session, check: QualityCheck) -> str | None:
    if check.gauge_id is None:
        return None
    return session.get(Gauge, check.gauge_id).code


# ------------------------------------------- the gauges, without a plant at all


def test_the_drifting_scale_reads_high_by_what_the_script_says_and_no_more():
    """The cause as data: grams per day since its last calibration, to a
    ceiling. A gauge that drifted without end would be a different story -
    a gauge nobody ever calibrates - and this one is about a calibration
    finding it."""
    entry = script()["measurement"]["characteristics"]["fill_weight"]["gauges"][1]
    per_day, ceiling = entry["drift"]["per_day"], entry["drift"]["max_bias"]
    calibrated = date(2026, 1, 1)
    gauge = measurement.Instrument(code=DRIFTING, last_calibrated=calibrated,
                                   drift_per_day=per_day, max_bias=ceiling)
    assert gauge.bias(calibrated) == 0.0, "a gauge is not drifting the day it is calibrated"
    assert gauge.bias(calibrated + timedelta(days=10)) == pytest.approx(per_day * 10)
    far = gauge.bias(calibrated + timedelta(days=4000))
    assert far == pytest.approx(ceiling), "the drift has no ceiling, so it grows for ever"


def test_the_other_scale_does_not_drift_at_all():
    """Two gauges and one story. If both drifted there would be nothing to
    compare the drifting one against, which is the whole method."""
    entry = script()["measurement"]["characteristics"]["fill_weight"]["gauges"][0]
    assert "drift" not in entry
    steady = measurement.Instrument(code=STEADY, last_calibrated=date(2026, 1, 1))
    assert steady.bias(date(2026, 6, 1)) == 0.0


def test_a_reading_is_written_to_the_resolution_the_gauge_can_actually_read():
    """A scale reading to a tenth of a gram does not report thousandths. A
    quality history full of them is a history of arithmetic."""
    gauge = measurement.Instrument(code=STEADY, resolution=0.1)
    reading = gauge.read(499.9427, rng=random.Random(1), today=date(2026, 10, 5))
    assert reading == pytest.approx(499.9)


def test_one_scale_takes_most_of_the_readings_and_the_other_takes_some():
    """Weighted, because a plant with two scales does not use them in turn:
    one lives at the station and one comes out when the first is busy."""
    register = [{"code": STEADY, "status": "in_service", "resolution": 0.1,
                 "last_calibrated": "2026-10-01"},
                {"code": DRIFTING, "status": "in_service", "resolution": 0.1,
                 "last_calibrated": "2026-07-01"}]
    bench = measurement.Bench(script(), register)
    rng = random.Random(4)
    picked = [bench.pick("fill_weight", rng).code for _ in range(400)]
    busy = picked.count(STEADY) / len(picked)
    assert 0.6 < busy < 0.9, f"the busy scale took {busy:.0%} of the readings"
    assert picked.count(DRIFTING) > 20, "the second scale never comes out at all"


def test_a_scale_taken_out_of_service_stops_being_used():
    """A decision somebody made on the screen means nothing if the floor goes
    on measuring with it."""
    register = [{"code": STEADY, "status": "out_of_service", "resolution": 0.1},
                {"code": DRIFTING, "status": "in_service", "resolution": 0.1}]
    bench = measurement.Bench(script(), register)
    assert [i.code for i in bench.instruments("fill_weight")] == [DRIFTING]


def test_the_night_shift_reads_the_same_process_with_a_wider_spread():
    """Not a worse plant - a thinner one. Fewer people, less light, nobody to
    ask, and the spread of its measurements says so."""
    bench = measurement.Bench(script())
    day = bench.spread("fill_weight", "DAY")
    night = bench.spread("fill_weight", "NIGHT")
    assert night > day > 0, f"night {night}, day {day}"


def test_a_characteristic_with_no_gauge_in_the_script_is_passed_through_untouched():
    """An unmeasured number that has been given a reading error is a fiction
    twice over."""
    bench = measurement.Bench(script(), [])
    gauge, value = bench.measure("viscosity", 12.5, rng=random.Random(1),
                                 today=date(2026, 10, 5))
    assert (gauge, value) == (None, 12.5)


def test_a_plant_with_no_floor_script_measures_exactly_as_it_did_before():
    """Every real plant. It records what the tag said, names no gauge and
    labels nothing, because it has people instead."""
    bench = measurement.Bench(measurement.load(None))
    assert bench.stale_after_s == 0.0
    assert bench.gauge_codes == []
    assert bench.measure("fill_weight", 500.0, rng=random.Random(1),
                         today=date(2026, 10, 5)) == (None, 500.0)


# ------------------------------------------------- the gauge on the records


def test_every_fill_weight_check_the_floor_takes_off_the_machine_names_a_gauge(plant):
    """The headline. 0 of the last 500 checks named one on 2026-10-04."""
    async def work(floor: Floor):
        specs, orders = await the_specs(floor), await the_orders(floor)
        for weight in (499.1, 500.4, 501.2, 498.7, 502.0, 499.8):
            await floor.inspect(specs, the_filler_showing(weight), orders)

    on_the_floor(plant, work)
    checks = the_checks(plant)
    assert len(checks) >= 5, f"the floor recorded {len(checks)} checks"
    named = [c for c in checks if c.gauge_id is not None]
    assert len(named) == len(checks), (
        f"{len(checks) - len(named)} of {len(checks)} checks name no gauge")
    assert {gauge_of(plant, c) for c in checks} <= {STEADY, DRIFTING}
    assert all(c.equipment_id is not None for c in checks), (
        "a reading off the machine does not say which machine")


def the_week_passes(session: Session, gauge: str = DRIFTING, days: int = 10) -> None:
    """Age one gauge past its calibration date, as a week of the plant does.

    The pack ships this scale six days from due, which is the state Scott
    sees on the screen today; the supervisor's visit is what a plant left up
    for a week gets. Moving the date is how a test gets there in a second.
    """
    row = session.scalar(select(Gauge).where(Gauge.code == gauge))
    row.last_calibrated = row.last_calibrated - timedelta(days=days)
    session.commit()


def test_a_scale_that_is_only_warned_about_is_not_calibrated_yet(plant):
    """The floor calibrates when a gauge falls due, not when it is warned
    about. Calibrating on the warning would end the drifting-gauge story in
    the first twenty seconds of every plant built from this pack."""
    async def work(floor: Floor):
        assert await floor.calibrate_what_is_due(by=SUPERVISOR) == []

    on_the_floor(plant, work)
    gauge = plant.scalar(select(Gauge).where(Gauge.code == DRIFTING))
    assert gauge.calibrations == []


def test_the_drifting_scales_readings_run_high_until_it_is_calibrated(plant):
    """The cause and its trace, in one test. The same bottle weight, measured
    on both scales, and the drifting one reads about 0.6 g heavier - which is
    what a panel on an out-of-control point has to be able to say. After the
    supervisor calibrates it, it does not.
    """
    entry = script()["measurement"]["characteristics"]["fill_weight"]["gauges"][1]
    expected = entry["drift"]["max_bias"]
    truth = 500.0
    readings: dict[str, list[float]] = {STEADY: [], DRIFTING: []}
    after: dict[str, list[float]] = {STEADY: [], DRIFTING: []}

    async def work(floor: Floor):
        # Both scales, on a bottle that weighs exactly 500 g, before anything
        # is calibrated. Measured directly rather than through `inspect`, so
        # the comparison is of the two instruments and not of two samples.
        for instrument in floor.bench.instruments("fill_weight"):
            readings[instrument.code] = [
                instrument.read(truth, rng=random.Random(3), today=date.today())
                for _ in range(40)]
        the_week_passes(plant)
        await floor.read_the_register()
        calibrated = await floor.calibrate_what_is_due(by=SUPERVISOR)
        assert calibrated == [DRIFTING], (
            f"the supervisor calibrated {calibrated}; one scale has fallen due")
        await floor.read_the_register()
        for instrument in floor.bench.instruments("fill_weight"):
            after[instrument.code] = [
                instrument.read(truth, rng=random.Random(3), today=date.today())
                for _ in range(40)]

    on_the_floor(plant, work)

    def mean(values):
        return sum(values) / len(values)

    assert mean(readings[STEADY]) == pytest.approx(truth, abs=0.2)
    assert mean(readings[DRIFTING]) - mean(readings[STEADY]) == pytest.approx(expected, abs=0.15), (
        f"steady {mean(readings[STEADY]):.2f} g, drifting {mean(readings[DRIFTING]):.2f} g")
    assert mean(after[DRIFTING]) == pytest.approx(truth, abs=0.2), (
        "the scale was calibrated and is still reading high")


def test_the_calibration_the_supervisor_records_is_on_the_gauges_own_register(plant):
    """It is a plant record, not a simulation detail: the register says who
    did it and when, and the gauge is not overdue afterwards."""
    async def work(floor: Floor):
        the_week_passes(plant)
        await floor.read_the_register()
        await floor.calibrate_what_is_due(by=SUPERVISOR)

    on_the_floor(plant, work)
    gauge = plant.scalar(select(Gauge).where(Gauge.code == DRIFTING))
    assert gauge.last_calibrated == date.today()
    assert [c.performed_by for c in gauge.calibrations] == [SUPERVISOR]
    assert gauge.calibrations[0].notes, "a calibration with no note says nothing"


def test_a_tag_that_has_gone_quiet_is_recorded_with_no_gauge_and_no_station(plant):
    """The coverage story. A frozen display is not a bottle somebody weighed,
    and writing the stale number down once a minute would fill the chart with
    readings nobody took. The honest record is a plausible value that names no
    instrument and no machine - which is exactly what `null` means in those
    two columns.
    """
    stale_after = script()["measurement"]["stale_after_s"]

    async def work(floor: Floor):
        specs, orders = await the_specs(floor), await the_orders(floor)
        for weight in (500.2, 499.4, 501.1, 498.9):
            await floor.inspect(specs,
                                the_filler_showing(weight, seconds_ago=stale_after * 3),
                                orders)

    on_the_floor(plant, work)
    checks = the_checks(plant)
    assert checks, "nothing was recorded at all"
    assert all(c.gauge_id is None for c in checks), (
        "a reading nobody took has a gauge's name on it")
    assert all(c.equipment_id is None for c in checks), (
        "a reading nobody took says which machine it came from")


def test_a_fresh_reading_is_not_treated_as_stale(plant):
    """The other half of the same judgment, so the window cannot quietly
    swallow every reading."""
    async def work(floor: Floor):
        specs, orders = await the_specs(floor), await the_orders(floor)
        await floor.inspect(specs, the_filler_showing(500.2, seconds_ago=1.0), orders)

    on_the_floor(plant, work)
    checks = the_checks(plant)
    assert len(checks) == 1 and checks[0].gauge_id is not None


def test_the_plant_reports_when_a_process_value_was_read_and_not_only_what_it_was(plant):
    """A process value with no time on it cannot be judged stale, and a tag
    that has stopped arriving looks exactly like one holding steady. Whether a
    reading is too old to use is the reader's judgment; the plant's job is to
    say when it was taken."""
    the_filler_reports(plant, 500.5, seconds_ago=42.0)

    async def work(floor: Floor):
        machines = await the_machines(floor)
        filler = next(m for m in machines if m["code"] == FILLER)
        assert filler["analog"]["name"] == "FillWeight"
        assert filler["analog"]["value"] == pytest.approx(500.5)
        taken = floor._too_old(filler["analog"]["at"])
        assert taken is None, "42 seconds old is not stale; the window is 150"

    on_the_floor(plant, work)


# ----------------------------------------------------------- the stops it names


def the_machine_goes(session: Session, state: str, *, code: str = FILLER,
                     ago: float = 0.0) -> None:
    """Put a machine into a state, as the OPC agent's state tag would."""
    interval = equipment_service.set_state(
        session, equipment_code=code, state=EquipmentStateName(state), actor="opc-agent")
    if ago:
        interval.started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=ago)
    session.commit()


def stops(session: Session, code: str = FILLER) -> list:
    machine = equipment_service.masterdata.get_equipment(session, code)
    return list(session.scalars(
        select(equipment_service.EquipmentState)
        .where(equipment_service.EquipmentState.equipment_id == machine.id)
        .order_by(equipment_service.EquipmentState.started_at)))


def test_the_floor_names_a_breakdown_it_watched_once_the_machine_is_back(plant):
    """A stop is named afterwards, because the moment a machine goes down is
    the moment nobody knows why yet - and on a plant whose states arrive from
    an OPC agent, nobody is ever asked. That was 100 % of seven days of
    downtime on this plant, measured 2026-10-04.
    """
    async def work(floor: Floor):
        the_machine_goes(plant, "down", ago=600)
        assert await floor.watch_the_stops() == [], "it named a stop that had not ended"
        the_machine_goes(plant, "running")
        assert await floor.watch_the_stops() == [FILLER]

    on_the_floor(plant, work)
    named = [s for s in stops(plant) if s.state is EquipmentStateName.DOWN]
    assert len(named) == 1
    assert named[0].reason_code == script()["stops"]["longer_than_a_micro_stop"]
    assert named[0].reason, "a code with no sentence beside it"
    assert named[0].reason_source is None, (
        "the plant's own label is recorded as somebody else's claim")


def test_a_stop_cleared_where_somebody_stood_is_a_micro_stop_and_not_a_breakdown(plant):
    """Which word each is, is the script's. The floor never chooses one."""
    async def work(floor: Floor):
        the_machine_goes(plant, "down", ago=2)
        await floor.watch_the_stops()
        the_machine_goes(plant, "running")
        await floor.watch_the_stops()

    on_the_floor(plant, work)
    down = [s for s in stops(plant) if s.state is EquipmentStateName.DOWN]
    assert down[0].reason_code == script()["stops"]["micro_stop"]


def test_a_breakdown_on_a_sped_up_replay_is_not_filed_as_a_micro_stop(plant):
    """A scored run plays an hour of line in a minute. A two-minute
    breakdown is over in four seconds of the floor's own time there, and a
    floor that judged it by those four seconds would file every breakdown on
    every scored run as a stop somebody cleared where they stood.

    How long a machine was stopped for is a fact about the line, so the
    judgment is made in line seconds.
    """
    async def work(floor: Floor):
        floor.speed = 60.0
        the_machine_goes(plant, "down", ago=4)       # four seconds watched
        await floor.watch_the_stops()
        the_machine_goes(plant, "running")
        await floor.watch_the_stops()

    on_the_floor(plant, work)
    down = next(s for s in stops(plant) if s.state is EquipmentStateName.DOWN)
    assert down.reason_code == script()["stops"]["longer_than_a_micro_stop"], (
        "four minutes of line time was filed as a micro stop")


def test_a_changeover_is_named_so_that_a_point_after_it_can_be_explained(plant):
    """The planted cause that is only findable if the stop before it has a
    name: the first fills after a restart run heavy, and an unnamed setup
    interval explains nothing."""
    async def work(floor: Floor):
        the_machine_goes(plant, "setup", ago=240)
        await floor.watch_the_stops()
        the_machine_goes(plant, "running")
        await floor.watch_the_stops()

    on_the_floor(plant, work)
    setup = [s for s in stops(plant) if s.state is EquipmentStateName.SETUP]
    assert setup[0].reason_code == script()["stops"]["changeover"]


def test_naming_one_stop_leaves_the_stretch_after_it_alone(plant):
    """The window is the instant the stop began and one second of it. A window
    reaching up to now would have swept in whatever the machine did next and
    called that a breakdown too."""
    async def work(floor: Floor):
        the_machine_goes(plant, "down", ago=300)
        await floor.watch_the_stops()
        the_machine_goes(plant, "idle")
        await floor.watch_the_stops()

    on_the_floor(plant, work)
    idle = [s for s in stops(plant) if s.state is EquipmentStateName.IDLE]
    assert idle and all(s.reason_code is None for s in idle), (
        "the idle stretch after the stop was labelled as part of it")


def test_an_idle_machine_is_left_unlabelled_because_nothing_recorded_says_why(plant):
    """This line's tag map maps starved and blocked both onto `idle`, so by
    the time the MES holds the interval the difference is gone. The vocabulary
    has a word for each and nothing observed says which, so the floor says
    neither. Guessing would put a cause in the pareto that nothing watched.
    """
    async def work(floor: Floor):
        the_machine_goes(plant, "idle", ago=400)
        await floor.watch_the_stops()
        the_machine_goes(plant, "running")
        assert await floor.watch_the_stops() == []

    on_the_floor(plant, work)
    idle = [s for s in stops(plant) if s.state is EquipmentStateName.IDLE]
    assert idle and all(s.reason_code is None and s.reason is None for s in idle)


def test_the_pareto_says_the_label_came_from_the_plants_own_list(plant):
    """What the whole exercise is for. The downtime pareto was one bar called
    *unlabelled* for every window anybody asked about."""
    from fsmes.services import analysis

    async def work(floor: Floor):
        the_machine_goes(plant, "down", ago=900)
        await floor.watch_the_stops()
        the_machine_goes(plant, "running")
        await floor.watch_the_stops()

    on_the_floor(plant, work)
    pareto = analysis.downtime_pareto(plant, hours=8)
    assert pareto["from_the_list_seconds"] > 0, pareto
    assert pareto["unlabelled_share"] == 0, pareto
    assert pareto["from_the_list_share"] == pytest.approx(1.0), pareto
    named = [b for b in pareto["reasons"] if b["from_the_list"]]
    assert named and named[0]["code"] == script()["stops"]["longer_than_a_micro_stop"]
    assert named[0]["labelled_by"] == {"here": pytest.approx(named[0]["seconds"], abs=1.0)}


def test_a_label_already_on_a_stop_is_never_overwritten(plant):
    """Somebody who was there has said what it was. A simulated floor that
    rewrote that would be the reason nobody trusts the pareto."""
    async def work(floor: Floor):
        the_machine_goes(plant, "down", ago=600)
        await floor.watch_the_stops()
        down = next(s for s in stops(plant) if s.state is EquipmentStateName.DOWN)
        down.reason_code, down.reason = "changeover", "Changeover"
        plant.commit()
        the_machine_goes(plant, "running")
        await floor.watch_the_stops()

    on_the_floor(plant, work)
    down = next(s for s in stops(plant) if s.state is EquipmentStateName.DOWN)
    assert down.reason_code == "changeover"


def test_naming_a_stop_in_a_window_nothing_was_observed_in_creates_nothing(plant):
    """An interval is this MES's own observation. Manufacturing one from a
    claim about one would put seconds into availability nobody watched."""
    async def work(floor: Floor):
        began = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=4)
        response = await floor.client.post(
            f"/equipment/{FILLER}/stops/label",
            json={"reason_code": "breakdown", "start": began.isoformat(),
                  "end": (began + timedelta(minutes=5)).isoformat()})
        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["labelled"] == 0
        assert "no interval is ever created here" in answer["note"]

    on_the_floor(plant, work)
    assert stops(plant) == []


def test_a_word_that_is_not_in_the_plants_vocabulary_is_refused(plant):
    """Four spellings of one reason is the failure a vocabulary exists to end."""
    async def work(floor: Floor):
        response = await floor.client.post(
            f"/equipment/{FILLER}/stops/label",
            json={"reason_code": "jammed_again",
                  "start": datetime.now(UTC).replace(tzinfo=None).isoformat()})
        assert response.status_code == 400, response.text
        assert "approved downtime reasons" in response.text

    on_the_floor(plant, work)


# ------------------------------------------------- the paperwork a shift does


def raise_a_finding(session: Session, code: str, severity: str = "minor") -> NonConformance:
    nc = NonConformance(code=code, description="Fill weight below specification",
                        severity=severity, status=NcStatus.OPEN, raised_by="opc-agent")
    session.add(nc)
    session.commit()
    return nc


def test_the_supervisor_takes_a_finding_all_the_way_to_closed(plant):
    """Three steps, each recorded against the person who took it. This loop
    used to post `/close` on an undispositioned record, the product refused
    (decision 0024), the refusal was logged, and nothing else happened - which
    is how this plant came to hold **887 open non-conformances, none ever
    reviewed**, measured 2026-10-04.
    """
    rules = script()["nonconformances"]
    for i in range(rules["leave_open"] + 2):
        raise_a_finding(plant, f"NC-{i:05d}")

    async def work(floor: Floor):
        assert await floor.review_nonconformances() >= 1

    on_the_floor(plant, work)
    worked = list(plant.scalars(select(NonConformance).order_by(NonConformance.id)))
    closed = [n for n in worked if n.status is NcStatus.CLOSED]
    assert closed, "every finding is still open"
    for nc in closed:
        assert nc.disposition is not None, "closed with the material question unanswered"
        assert nc.disposition_reason, "a concession nobody wrote a reason for"
        assert nc.reviewed_by == SUPERVISOR and nc.closed_by == SUPERVISOR


def test_what_happens_to_the_material_is_the_scripts_decision_and_not_the_codes(plant):
    """`use_as_is` on a batch that failed its specification is a concession
    somebody has to defend a year later. A simulated plant that invented the
    sentence would be writing the one field nobody can check."""
    rules = script()["nonconformances"]
    raise_a_finding(plant, "NC-10001", severity="major")
    for i in range(rules["leave_open"]):
        raise_a_finding(plant, f"NC-2000{i}")

    async def work(floor: Floor):
        await floor.review_nonconformances()

    on_the_floor(plant, work)
    major = plant.scalar(select(NonConformance).where(NonConformance.code == "NC-10001"))
    expected = rules["by_severity"]["major"]
    assert major.disposition.value == expected["disposition"]
    assert major.disposition_reason == expected["reason"]


def test_a_few_of_the_newest_findings_are_left_open_on_purpose(plant):
    """A plant where every finding is closed by the end of the shift is as
    unrealistic as one where nothing ever is."""
    rules = script()["nonconformances"]
    for i in range(rules["leave_open"] + 3):
        raise_a_finding(plant, f"NC-{i:05d}")

    async def work(floor: Floor):
        for _ in range(4):
            await floor.review_nonconformances()

    on_the_floor(plant, work)
    still_open = plant.scalars(select(NonConformance).where(
        NonConformance.status.in_([NcStatus.OPEN, NcStatus.UNDER_REVIEW]))).all()
    assert len(still_open) == rules["leave_open"], (
        f"{len(still_open)} left open; the script says {rules['leave_open']}")


def test_a_plant_whose_pack_has_no_floor_script_closes_nothing(plant):
    """Which is what this did before, and what a real plant wants: it has
    people, and their decisions are theirs."""
    raise_a_finding(plant, "NC-30001")
    session = plant
    session.commit()
    app = create_app()

    async def _run():
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                   base_url="http://plant")
        async with client:
            floor = Floor(get_settings(), client, random.Random(1))  # no script
            await floor.sign_in(SUPERVISOR, SUPERVISOR_PASSWORD)
            assert await floor.review_nonconformances() == 0
            assert await floor.watch_the_stops() == []

    asyncio.run(_run())
    session.expire_all()
    nc = session.scalar(select(NonConformance).where(NonConformance.code == "NC-30001"))
    assert nc.status is NcStatus.OPEN


# ------------------------------------------- the line's own planted causes


def line_config() -> dict:
    return generate.load_config(LINE)


def events_of(kind: str, station: str | None = None) -> list[dict]:
    return [e for e in line_config()["events"]
            if e["type"] == kind and (station is None or e.get("station") == station)]


def column(rows: list[list], header: list[str], name: str) -> list:
    return [row[header.index(name)] for row in rows]


def refill_table() -> tuple[list[str], list[list]]:
    config = line_config()
    rows = generate.simulate(config)
    header = ["TSec", "State", "GoodCount", "ScrapCount", "TotalCount", "AlarmWord",
              "CycleTimeMs", "RunMinutes", "ReadyBit",
              *[a["name"] for a in generate.station_analogs(
                  next(s for s in config["stations"] if s["name"] == "Refill"))]]
    return header, rows["Refill"]


def test_the_nozzle_pressure_dip_leaves_the_fill_weights_low_in_its_own_window():
    """The cause and the trace, both out of the line's script: pressure held
    10 % low for six minutes, and every bottle filled in that window four
    grams light, with no alarm raised on the filler."""
    dip = next(e for e in events_of("offset", "Refill") if e["analog"] == "NozzlePressure")
    effect = dip["effects"][0]
    header, rows = refill_table()
    inside = [r for r in rows if dip["start"] <= r[0] < dip["end"]]
    # The line at rest: after the dip and before the washer story, which
    # takes nine grams off the same tag for its own five minutes. Comparing
    # against a window that contained another cause would be measuring the
    # two against each other.
    outside = [r for r in rows if dip["end"] <= r[0] < 1800]

    def mean(rows_, name):
        values = [v for v in column(rows_, header, name) if v is not None]
        return sum(values) / len(values)

    assert mean(inside, "NozzlePressure") - mean(outside, "NozzlePressure") == pytest.approx(
        dip["offset"], abs=0.03)
    assert mean(inside, "FillWeight") - mean(outside, "FillWeight") == pytest.approx(
        effect["offset"], abs=0.5)
    # Bit 0 is the machine's own fault bit. Nothing alarms: an engineer who
    # only reads alarms never finds this, which is the point of planting it.
    assert all(word == 0 for word in column(inside, header, "AlarmWord"))


def test_the_first_fills_after_the_changeover_run_heavy():
    """A point above the control limit whose cause is a *planned* stop that
    ended moments earlier - the one an availability figure must not count as
    downtime and a control chart must still be able to explain."""
    changeover = events_of("changeover")[0]
    heavy = next(e for e in events_of("offset", "Refill") if e["analog"] == "FillWeight")
    assert heavy["start"] == changeover["end"], (
        "the heavy fills no longer begin where the changeover ends")
    header, rows = refill_table()
    inside = [r for r in rows if heavy["start"] <= r[0] < heavy["end"]]
    settled = [r for r in rows if heavy["end"] <= r[0] < 2700]

    def mean(rows_, name):
        values = [v for v in column(rows_, header, name) if v is not None]
        return sum(values) / len(values)

    assert mean(inside, "FillWeight") - mean(settled, "FillWeight") == pytest.approx(
        heavy["offset"], abs=1.0)


def test_a_quiet_tag_publishes_nothing_while_every_other_signal_on_the_machine_does():
    """An empty cell, not a zero and not the last value repeated. Zero is a
    reading of zero; a repeat is a reading somebody could believe; and the
    whole question downstream is whether a value is old."""
    quiet = events_of("quiet", "Refill")[0]
    header, rows = refill_table()
    inside = [r for r in rows if quiet["start"] <= r[0] < quiet["end"]]
    assert inside, "the quiet window is empty of rows"
    assert all(v is None for v in column(inside, header, quiet["analog"]))
    for other in ("NozzlePressure", "ProductTemp", "State", "GoodCount"):
        assert all(v is not None for v in column(inside, header, other)), (
            f"{other} went quiet too; the scenario is one tag, not the machine")
    before = [r for r in rows if r[0] < quiet["start"]]
    assert all(v is not None for v in column(before, header, quiet["analog"]))


def test_a_quiet_tag_is_read_back_as_absent_and_not_as_a_number(tmp_path):
    """Through the file, because that is how the replay server gets it: an
    empty cell has to survive being written and read."""
    from fsmes.integrations.opc.csv_replay import load_table

    config = line_config()
    rows = generate.simulate(config)
    generate.write_output(config, rows, tmp_path)
    quiet = events_of("quiet", "Refill")[0]
    table = load_table(tmp_path, "Refill")
    assert table[quiet["start"]][quiet["analog"]] is None
    assert table[quiet["start"] - 1][quiet["analog"]] is not None


def test_a_scenario_that_silences_a_signal_the_station_does_not_have_is_refused():
    """A typo used to simulate as nothing at all: the run still produced a
    report, and the report said the plant behaved."""
    config = json.loads(LINE.read_text(encoding="utf-8"))
    config["events"] = [{"type": "quiet", "station": "Refill", "analog": "FillWieght",
                         "start": 10, "end": 20}]
    with pytest.raises(SystemExit) as refused:
        generate._validate_line(config, "a test")
    assert "FillWieght" in str(refused.value)


def test_an_offset_event_that_does_not_say_how_far_is_refused():
    config = json.loads(LINE.read_text(encoding="utf-8"))
    config["events"] = [{"type": "offset", "station": "Refill", "analog": "FillWeight",
                         "start": 10, "end": 20}]
    with pytest.raises(SystemExit) as refused:
        generate._validate_line(config, "a test")
    assert "offset" in str(refused.value)


def test_every_cause_the_pack_declares_is_actually_in_one_of_the_two_scripts():
    """The list Scott reads is the list the plant plants. A story in a README
    that nothing scripts is worse than no story at all."""
    stories = script()["_the_stories_planted_here"]
    assert len(stories) >= 4, stories
    # The line's four, named in its own file so a reader of either script can
    # find the other half.
    kinds = {e["type"] for e in line_config()["events"]}
    assert {"offset", "quiet", "changeover", "scrap_burst"} <= kinds
    assert len(events_of("offset", "Refill")) == 2, (
        "the nozzle dip and the heavy first fills are the two offset events")


# --------------------------------------- and one behind a point on the
#                                         fill-height chart
#
# The sixth cause, and the first that lives on a chart of means rather than of
# readings. Fill height is not published by anything on the line - the bottles
# are carried to a bench and measured by hand five at a time - so the floor
# takes the last five stored fill weights off the filler, turns each into a
# height by the one straight line written into `labs/kepsim/scenario.md`, adds
# the glass's own piece-to-piece variation, and measures each through the bench
# gauge. The planted cause is the nozzle the changeover left behind: for a
# window after the line comes back the heights sit about one within-sample
# sigma high, which shows on the means and not on the ranges.


def the_register() -> dict:
    """The bench height gauge's row in the pack's own gauge register."""
    rows = json.loads((BOTTLING / "masterdata" / "gauges.json").read_text())
    rows = rows["items"] if isinstance(rows, dict) else rows
    return next(g for g in rows if g["code"] == "HEIGHT-FILL-01")


def the_plan():
    """The pack's own sampling plan for fill height, read from the pack."""
    return measurement.plans(script())["fill_height"]


async def sample_once(floor: Floor) -> bool:
    specs = await the_specs(floor)
    return await floor.inspect_a_sample(the_plan(), specs, await the_orders(floor))


def the_samples(session: Session) -> list:
    from fsmes.domain import QualitySample

    return list(session.scalars(select(QualitySample).order_by(QualitySample.id)))


def the_heights(session: Session) -> list[float]:
    from fsmes.domain import QualitySpec

    spec = session.scalar(select(QualitySpec).where(QualitySpec.characteristic == "fill_height"))
    return [c.value for c in the_checks(session) if c.spec_id == spec.id]


def the_filler_has_weighed(session: Session, weights, *, from_seconds_ago: float = 60.0) -> None:
    """A run of fill weights on the filler's history, oldest first.

    Five seconds apart, which is what the generated line publishes, and each
    one its own stored row - because the five pieces of a sample are five
    bottles weighed at five instants and not one bottle weighed five times.
    """
    for i, weight in enumerate(weights):
        the_filler_reports(session, weight, seconds_ago=from_seconds_ago - i * 5.0)


# ------------------------------------------------ the plan, without a plant

def test_the_sampling_plan_is_the_packs_and_not_this_codes():
    """Every number of it: how often, which tag the pieces come off, the
    straight line that turns one into the other, the glass's own spread and
    the planted cause's window. A floor that held any of these in Python
    would be a floor that knew what shape one plant's bottle is."""
    plan = the_plan()
    assert (plan.equipment, plan.tag) == (FILLER, "FillWeight")
    assert plan.every_line_s == 900.0, "fifteen line minutes: a tray at a time"
    assert (plan.offset, plan.per_unit) == (12.0, 0.26)
    assert plan.piece_to_piece == 0.35
    assert (plan.after_changeover_minutes, plan.after_changeover_offset) == (25.0, 0.5)


def test_a_weight_becomes_a_height_by_the_one_formula_the_scenario_states():
    """`height_mm = 12.0 + 0.26 * fill_weight_g`, and nothing else. A bottle
    filled to the middle of its weight specification stands in the middle of
    its height specification, which is the only reason the two can be read
    beside each other."""
    plan = the_plan()
    assert plan.convert(500.0) == 142.0
    assert plan.convert(494.0) == pytest.approx(140.44)
    assert plan.convert(506.0) == pytest.approx(143.56)


def test_a_plant_whose_script_has_no_sampling_section_has_no_plans():
    """Which is every pack written before this existed. A floor with no plan
    runs its inspection loop exactly as it did and posts no samples."""
    assert measurement.plans({}) == {}
    assert measurement.plans({"measurement": {"characteristics": {}}}) == {}


def test_a_sampling_entry_that_does_not_say_where_the_pieces_come_from_is_skipped():
    """Rather than guessed at. A plan with no tag behind it could only be a
    plan this code invented pieces for."""
    assert measurement.plans({"sampling": {"x": {"every_line_s": 60}}}) == {}
    assert measurement.plans({"sampling": {"_why": "a comment"}}) == {}


# ---------------------------------------------------- the floor, on a plant

def test_the_pack_inspects_fill_height_five_bottles_at_a_time(plant):
    """The plan is master data, on the specification, and the floor reads it
    from the plant rather than from its own script - so a plant that changed
    the plan on the screen has changed what the floor records."""
    from fsmes.domain import QualitySpec

    spec = plant.scalar(select(QualitySpec).where(QualitySpec.characteristic == "fill_height"))
    assert spec is not None, "the pack's quality_specs.json carries it"
    assert spec.sample_size == 5
    assert (spec.unit, spec.min_value, spec.max_value) == ("mm", 139.0, 145.0)


def test_the_five_pieces_are_five_different_readings_of_the_filler(plant):
    """Five bottles weighed at five instants, not one bottle weighed five
    times. Pinned on the readings the floor took off the tag, before the
    glass and the gauge touch them: had it taken one reading five times the
    sample's range would be zero and the mean range the limits are built from
    would collapse."""
    weights = [496.0, 498.0, 500.0, 502.0, 504.0]
    the_filler_has_weighed(plant, weights)
    taken = []

    async def take(floor: Floor) -> None:
        taken.extend(await floor._pieces(the_plan(), 5) or [])

    on_the_floor(plant, take)
    assert sorted(taken) == weights, taken


def test_a_sample_of_five_is_one_record_over_five_rows(plant):
    """One `quality_samples` row, five `quality_checks` rows under it, and a
    range that is not zero. The heights are not all different to the tenth of
    a millimetre - the bench gauge reads to 0.1 mm and two bottles can land
    on the same tenth, which is a fact about the gauge and not a sample this
    floor flattened."""
    the_filler_has_weighed(plant, [496.0, 498.0, 500.0, 502.0, 504.0])
    on_the_floor(plant, sample_once)

    assert len(the_samples(plant)) == 1
    heights = the_heights(plant)
    assert len(heights) == 5
    assert max(heights) - min(heights) > 0, heights
    # Five weights two grams apart are five heights about half a millimetre
    # apart, plus the glass and the gauge. Still inside the specification.
    assert min(heights) > 139.0 and max(heights) < 145.0, heights


def test_the_sample_names_the_bench_gauge_and_the_station_it_was_measured_at(plant):
    """The pieces came off the filler and were carried to the height gauge's
    own station. The record says where the measuring happened, because that
    is the place a reader can go and look at."""
    from fsmes.domain import Equipment, QualitySample

    the_filler_has_weighed(plant, [498.0, 499.0, 500.0, 501.0, 502.0])
    on_the_floor(plant, sample_once)

    sample = the_samples(plant)[0]
    assert plant.get(Gauge, sample.gauge_id).code == "HEIGHT-FILL-01"
    # The station the gauge's own register says it lives at, read from the
    # pack rather than repeated here: a gauge moved on the screen has moved
    # for this floor too, and a sample that named the station the script was
    # written against would say a measurement happened somewhere it did not.
    assert plant.get(Equipment, sample.equipment_id).code == the_register()["location"]
    tied = [c for c in the_checks(plant) if c.sample_id == sample.id]
    assert len(tied) == 5
    assert {gauge_of(plant, c) for c in tied} == {"HEIGHT-FILL-01"}, \
        "one gauge for the sample: a range measured half on each of two " \
        "instruments is partly the two instruments disagreeing"
    assert plant.scalar(select(QualitySample.id).where(
        QualitySample.id == sample.id)) is not None


def test_a_filler_with_no_history_yet_is_not_sampled_at_all(plant):
    """And the floor says so, once. Four readings is not five bottles, and
    taking four - or taking one of them twice - would be a sample this floor
    invented. The honest answer to *what is the fill height* on a plant that
    has just started is nothing."""
    the_filler_has_weighed(plant, [499.0, 500.0, 501.0, 502.0])
    took = []

    async def twice(floor: Floor) -> None:
        took.append(await sample_once(floor))
        took.append(await sample_once(floor))
        took.append(floor._said_no_pieces)

    on_the_floor(plant, twice)
    assert took[:2] == [False, False]
    assert took[2] == {"fill_height"}, "said once, not every fifteen minutes"
    assert the_samples(plant) == []
    assert the_heights(plant) == []


def test_the_next_sample_takes_five_bottles_the_last_one_did_not(plant):
    """Ten readings on the history and two attempts inside one run gets two
    samples of five different bottles - the first five, then the next five.
    A floor that re-measured the same five would be writing one sample down
    twice."""
    first = [494.0, 495.0, 496.0, 497.0, 498.0]
    next_five = [502.0, 503.0, 504.0, 505.0, 506.0]
    the_filler_has_weighed(plant, first, from_seconds_ago=120.0)
    took = []

    async def three_goes(floor: Floor) -> None:
        # The newest five the floor has not used. A tray is taken off the
        # line now, not an hour ago.
        took.append(await floor._pieces(the_plan(), 5))
        # Nothing new has been bottled, so there is nothing to sample.
        took.append(await floor._pieces(the_plan(), 5))
        the_filler_has_weighed(plant, next_five, from_seconds_ago=40.0)
        took.append(await floor._pieces(the_plan(), 5))

    on_the_floor(plant, three_goes)
    assert sorted(took[0]) == first, took
    assert took[1] is None, "no five bottles the first sample did not already take"
    assert sorted(took[2]) == next_five, took
    assert not set(took[0]) & set(took[2]), "no bottle in both samples"


def test_within_one_run_a_sample_never_takes_a_bottle_the_last_one_took(plant):
    """The memory that makes the samples different: the stamp of the newest
    reading already used. Five readings on the history and two attempts gets
    one sample, not two."""
    the_filler_has_weighed(plant, [496.0, 498.0, 500.0, 502.0, 504.0])
    took = []

    async def twice(floor: Floor) -> None:
        took.append(await sample_once(floor))
        took.append(await sample_once(floor))

    on_the_floor(plant, twice)
    assert took == [True, False]
    assert len(the_samples(plant)) == 1


def test_the_heights_after_a_changeover_sit_half_a_millimetre_high(plant):
    """The planted cause. Same five weights, sampled twice: once with no
    changeover behind it and once inside the window after one. The offset
    goes on the whole sample, because what the nozzle setting moved is the
    process and not one bottle - so it shows on the means and not on the
    ranges, which is the finding an engineer is meant to be able to make."""
    plan = the_plan()
    weights = [499.0, 500.0, 501.0, 500.0, 499.0]
    settled, after = [], []

    async def both(floor: Floor) -> None:
        the_filler_has_weighed(plant, weights, from_seconds_ago=200.0)
        assert await sample_once(floor)
        settled.extend(the_heights(plant))

        # The line has just changed over and come back. This is the floor's
        # own record of the instant, set by `watch_the_stops` when it names a
        # setup stop; set here directly so the test is about the cause and
        # not about the stop watcher, which is pinned above.
        floor._changeover_ended = datetime.now(UTC).replace(tzinfo=None)
        the_filler_has_weighed(plant, weights, from_seconds_ago=20.0)
        assert await sample_once(floor)
        after.extend(the_heights(plant)[5:])

    on_the_floor(plant, both)

    assert len(settled) == 5 and len(after) == 5
    lift = sum(after) / 5 - sum(settled) / 5
    assert lift == pytest.approx(plan.after_changeover_offset, abs=0.35), lift
    # Half a millimetre is what the pack plants, and that is all this test
    # claims. How big it is *relative to the spread* is not a thing one
    # sample of five can show: here the five weights are held near enough
    # identical, so the within-sample spread is only the glass. On the line
    # the five bottles are weighed five seconds apart, their weights differ
    # by more than their moulding does, and the same half-millimetre is about
    # four tenths of the spread the range chart sees - which is why this is a
    # shift found by splitting the means on the changeover rather than one
    # that trips a control rule.
    # On the ranges it does not show: one offset on five bottles leaves the
    # spread of the five where it was.
    assert abs((max(after) - min(after)) - (max(settled) - min(settled))) < 1.0


def test_a_sampled_characteristic_is_not_also_inspected_one_piece_at_a_time(plant):
    """The inspection loop leaves it alone. One reading of a subgroup of five
    is not a point on its chart, the plant refuses it, and a floor that kept
    offering it would fill the log with refusals."""
    the_filler_reports(plant, 500.0)

    async def inspect_everything(floor: Floor) -> None:
        specs = await the_specs(floor)
        assert any(s["characteristic"] == "fill_height" for s in specs), \
            "the plant offers it; the floor is what declines to inspect it this way"
        await floor.inspect(specs, the_filler_showing(500.0), await the_orders(floor),
                            every_spec=True)

    on_the_floor(plant, inspect_everything)
    assert the_heights(plant) == [], "no single reading of a sampled characteristic"
    assert the_checks(plant), "and fill weight was still inspected"
