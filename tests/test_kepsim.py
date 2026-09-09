"""The KepSim line: the generator, the tag map that reads it, and the whole
path from replayed CSV rows to booked MES production.

The end-to-end test is the one that matters. It stands up the replay server and
the real OPC agent against a real database and asserts the MES ends up with the
facts — which is the claim the Kepware integration actually makes, minus
Kepware itself. Everything that differs between the replay and a live
KEPServerEX lives in the tag map, so what is proven here is the code path, and
what is left unproven is one config file and a certificate handshake.
"""

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import fsmes.domain  # noqa: F401  (register all tables)
from fsmes.db import Base
from fsmes.domain import (
    Equipment,
    EquipmentState,
    EquipmentStateName,
    ProductionLog,
    ProductionSource,
    TagValue,
)
from fsmes.integrations.opc.csv_replay import load_table
from fsmes.integrations.opc.tag_map import load_manifest, load_tag_map
from fsmes.seed import seed_demo_plant
from fsmes.seed_kepsim import STATIONS, seed_kepsim_line

REPO = Path(__file__).resolve().parents[1]
KEPSIM_MAP = REPO / "config" / "tag_map_kepsim.json"
KEPWARE_MAP = REPO / "config" / "tag_map_kepware.json"
LEGACY_MAP = REPO / "config" / "tag_map.json"


@pytest.fixture(scope="module")
def generated(tmp_path_factory) -> Path:
    """One generated line, shared by every test that reads it.

    The line is described by labs/kepsim/line.json and built by the product's
    own generator. It used to be hardcoded Python loaded through importlib,
    which made it unscoreable - ground truth has to be readable.
    """
    from fsmes.sim.generate import generate

    out = tmp_path_factory.mktemp("kepsim_out")
    generate(REPO / "labs" / "kepsim" / "line.json", out, write_docs=False)
    return out


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


# --------------------------------------------------------------------- tag map


def test_legacy_tag_map_still_loads_unchanged():
    """The demo plant's map has none of the new fields; it must keep working."""
    machines = {m.equipment: m for m in load_tag_map(LEGACY_MAP)}
    mixer = machines["MIX01"]
    assert mixer.object == "MIX01"
    assert mixer.cycle_seconds == 4.0
    assert mixer.analog == "Temperature"  # the twin's own simulator convention
    assert mixer.order_tag == "OrderCode"  # still commanded
    assert mixer.node_id is None  # still browsed
    assert mixer.tags == ("State", "GoodCount", "ScrapCount", "Temperature")


def test_kepsim_map_describes_a_read_only_line(generated):
    machines = load_tag_map(KEPSIM_MAP, load_manifest(generated))
    assert [m.equipment for m in machines] == ["LD01", "RD01", "WASH01", "QI01", "FILL01", "PAL01"]
    assert all(m.order_tag is None for m in machines), "a CSV replay cannot be written to"
    assert all(m.node_id is None for m in machines), "the replay server is browsable"
    rd = next(m for m in machines if m.equipment == "RD01")
    assert rd.object == "RD"  # the CSV table name
    assert rd.analog == "MotorTemp"
    # The canonical four still lead, and everything else the machine
    # publishes follows them - a real machine has a dozen signals, and an MES
    # subscribing to one of them is sampling rather than integrating.
    assert rd.tags[:4] == ("State", "GoodCount", "ScrapCount", "MotorTemp")
    assert {"AlarmWord", "TotalCount", "RunMinutes", "PressForceSP"} <= set(rd.tags)
    assert len(rd.tags) >= 10


def test_kepware_map_matches_kepsim_map_except_for_addressing():
    """The whole design claim: swapping the machine layer is config, not code."""
    replay = {m.equipment: m for m in load_tag_map(KEPSIM_MAP)}
    kepware = {m.equipment: m for m in load_tag_map(KEPWARE_MAP)}
    assert replay.keys() == kepware.keys()
    for code, a in replay.items():
        b = kepware[code]
        assert (a.analog, a.cycle_seconds, a.state_map, a.order_tag) == (
            b.analog,
            b.cycle_seconds,
            b.state_map,
            b.order_tag,
        )
        # Not the bare column name: the Advanced Simulator generates tags as
        # <table>_<column>, and the table is the file, so LD.csv + State ->
        # LD_csv_State. Verified against a live KEPServerEX 7.1 on 2026-08-18;
        # the earlier guess here was wrong and would have addressed nothing.
        assert b.node_id and b.node("State") == f"ns=2;s=SimLine.{b.object}.{b.object}_csv_State"


def test_state_mapping_is_the_oee_decision():
    """Starved and blocked machines are healthy but idle; a changeover is a
    planned stop. Calling either of those DOWN would wreck availability."""
    rd = next(m for m in load_tag_map(KEPSIM_MAP) if m.equipment == "RD01")
    assert rd.to_state(1) is EquipmentStateName.RUNNING
    assert rd.to_state(0) is EquipmentStateName.IDLE
    assert rd.to_state(2) is EquipmentStateName.IDLE  # starved
    assert rd.to_state(3) is EquipmentStateName.IDLE  # blocked
    assert rd.to_state(4) is EquipmentStateName.DOWN  # a real fault
    assert rd.to_state(5) is EquipmentStateName.SETUP  # changeover, not downtime


def test_unmapped_state_is_refused_not_guessed():
    rd = next(m for m in load_tag_map(KEPSIM_MAP) if m.equipment == "RD01")
    with pytest.raises(ValueError, match="not in the tag map's state_map"):
        rd.to_state(9)


def test_state_passes_through_when_the_tag_already_carries_the_name():
    mixer = next(m for m in load_tag_map(LEGACY_MAP) if m.equipment == "MIX01")
    assert mixer.to_state("running") is EquipmentStateName.RUNNING


# ------------------------------------------------------------------- generator


def test_generated_line_is_deterministic(tmp_path):
    from fsmes.sim.generate import generate

    line = REPO / "labs" / "kepsim" / "line.json"
    a, b = tmp_path / "a", tmp_path / "b"
    generate(line, a, write_docs=False)
    generate(line, b, write_docs=False)
    for table in ("LD", "RD", "Washer", "QI", "Refill", "Palletiser", "Line"):
        assert (a / f"{table}.csv").read_bytes() == (b / f"{table}.csv").read_bytes()


def test_every_station_table_has_the_columns_its_tag_map_entry_claims(generated):
    """The failure this catches is the migration's most likely one: a tag
    renamed on one side only."""
    for spec in load_tag_map(KEPSIM_MAP):
        columns = set(load_table(generated, spec.object)[0])
        # A setpoint is the one mapped tag with no column behind it: it is
        # commanded, not replayed, and a column would mean the file
        # overwrote the engineer once a second.
        manifest = json.loads(
            (generated / "tags.json").read_text(encoding="utf-8"))["tables"][spec.object]["tags"]
        commanded = {t for t, meta in manifest.items() if meta.get("kind") == "sp"}
        assert not (commanded & columns), (
            f"{spec.equipment}: setpoints must not be generated columns")

        replayed = set(spec.tags) - commanded
        assert replayed <= columns, (
            f"{spec.equipment} ({spec.object}.csv) is missing {replayed - columns}")


def test_states_stay_inside_the_mapped_range(generated):
    known = {int(k) for k in next(m for m in load_tag_map(KEPSIM_MAP) if m.equipment == "RD01").state_map}
    for spec in load_tag_map(KEPSIM_MAP):
        states = {row["State"] for row in load_table(generated, spec.object)}
        assert states <= known, f"{spec.equipment} emits states outside the map: {states - known}"


def test_the_scripted_events_are_actually_in_the_data(generated):
    """The scenario file promises specific lessons at specific times; if the
    data stops containing them, every runbook exercise built on it is fiction."""
    rd = load_table(generated, "RD")
    washer = load_table(generated, "Washer")

    assert all(rd[t]["State"] == 4 for t in range(1500, 1620)), "RD breakdown missing"
    assert rd[1499]["MotorTemp"] > 85, "MotorTemp should have drifted up before the failure"
    assert rd[3000]["GoodCount"] == 0, "the RD counter reset at t=3000 is gone"
    assert all(rd[t]["State"] == 5 for t in range(2400, 2640)), "changeover missing"

    burst = [washer[t] for t in range(1800, 1980)]
    before = washer[1700]["ScrapCount"] - washer[1600]["ScrapCount"]
    during = burst[-1]["ScrapCount"] - burst[0]["ScrapCount"]
    assert during > before * 3, "the washer scrap burst is not visibly worse than normal"
    assert sum(r["WashTemp"] for r in burst) / len(burst) > 73, "WashTemp should run hot during the burst"


def test_a_breakdown_starves_and_blocks_its_neighbours(generated):
    """The reason the line is modelled with finite buffers at all: a stop
    propagates. Without this, downtime analysis has nothing to teach."""
    upstream = load_table(generated, "LD")
    downstream = load_table(generated, "Washer")
    window = range(1500, 1620)
    assert any(upstream[t]["State"] == 3 for t in window), "LD never blocked while RD was down"
    assert any(downstream[t]["State"] == 2 for t in window), "Washer never starved while RD was down"


# ------------------------------------------------------------------------ seed


def test_seeding_the_line_takes_its_rated_cycle_times_from_the_tag_map(db):
    assert seed_kepsim_line(db, tag_map=KEPSIM_MAP) is True
    db.flush()
    cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(KEPSIM_MAP)}
    for code, _, _ in STATIONS:
        equipment = db.scalar(select(Equipment).where(Equipment.code == code))
        assert equipment is not None, f"{code} was not seeded"
        assert equipment.ideal_cycle_seconds == cycles[code]


def test_seeding_the_line_is_idempotent_and_coexists_with_the_demo_plant(db):
    seed_demo_plant(db)
    assert seed_kepsim_line(db, tag_map=KEPSIM_MAP) is True
    db.flush()
    assert seed_kepsim_line(db, tag_map=KEPSIM_MAP) is False
    db.flush()
    # One shared area, both lines under it, no duplicated hierarchy.
    assert db.scalar(select(func.count()).select_from(Equipment).where(Equipment.code == "PKG")) == 1
    assert db.scalar(select(Equipment).where(Equipment.code == "MIX01")) is not None
    assert db.scalar(select(Equipment).where(Equipment.code == "LD01")) is not None


# -------------------------------------------------------------------- end-to-end


@pytest.mark.slow
def test_replayed_line_becomes_mes_production(generated, tmp_path, monkeypatch):
    """Replay server -> OPC agent -> MES, with nothing stubbed in between."""
    monkeypatch.setenv("MES_DATABASE_URL", f"sqlite:///{(tmp_path / 'e2e.db').as_posix()}")
    monkeypatch.setenv("MES_TAG_MAP_FILE", str(KEPSIM_MAP))
    monkeypatch.setenv("MES_OPC_ENDPOINT", "opc.tcp://127.0.0.1:48411/mes-twin/replay-test")

    from fsmes import config
    from fsmes import db as db_module

    for cached in (config.get_settings, db_module.get_engine, db_module.get_sessionmaker):
        cached.cache_clear()
    settings = config.get_settings()

    from fsmes.db import session_scope
    from fsmes.integrations.opc import agent, csv_replay
    from fsmes.services import workorders

    Base.metadata.create_all(db_module.get_engine())
    with session_scope() as session:
        seed_kepsim_line(session, tag_map=KEPSIM_MAP)
        session.flush()
        # Big enough that no operation completes mid-test and stops counting.
        workorders.create(session, code="WO-KEPSIM-1", material_code="FG-BOTTLE", quantity=1_000_000)
        workorders.release(session, "WO-KEPSIM-1")

    async def drive() -> None:
        # 20 rows/second, so a few seconds of test covers a few minutes of line.
        server = asyncio.create_task(csv_replay.run(settings, directory=generated, speed=20.0))
        await asyncio.sleep(1.0)  # let the server bind before the agent dials
        client = asyncio.create_task(agent.run(settings))
        try:
            deadline = asyncio.get_running_loop().time() + 30
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(1.0)
                with session_scope() as session:
                    booked = session.scalar(select(func.count()).select_from(ProductionLog))
                    stations = session.scalar(
                        select(func.count(func.distinct(ProductionLog.equipment_id))).select_from(ProductionLog)
                    )
                if booked and stations >= 3:
                    return
            pytest.fail("the agent never booked production from the replayed line")
        finally:
            for task in (client, server):
                task.cancel()
            await asyncio.gather(client, server, return_exceptions=True)

    asyncio.run(drive())

    with session_scope() as session:
        # Production was booked, and attributed to the machine, not invented.
        logs = session.scalars(select(ProductionLog)).all()
        assert logs, "no production booked"
        assert all(log.source is ProductionSource.OPC for log in logs)
        assert all(log.good_qty >= 0 and log.scrap_qty >= 0 for log in logs)

        # The integer State tag arrived as a real MES state, via the map.
        states = session.scalars(select(EquipmentState)).all()
        assert states, "no equipment states recorded"
        assert all(isinstance(state.state, EquipmentStateName) for state in states)

        # Tag history kept the machine's own analog names, not a normalised one.
        tags = {tag for (tag,) in session.execute(select(TagValue.tag).distinct())}
        assert "RD01.MotorTemp" in tags
        assert "WASH01.WashTemp" in tags
        assert not any(t.endswith(".Temperature") for t in tags), "analog tag names were flattened"

    for cached in (config.get_settings, db_module.get_engine, db_module.get_sessionmaker):
        cached.cache_clear()
