"""A Satisfactory game factory, read from its poll files into the MES.

The fixture is ten real polls of one evening, trimmed - see
`tests/fixtures/satisfactory/README.md` for exactly what was cut and the one
poll that was changed to say the game was paused. Everything here reads it the
way `fsmes run-satisfactory --once` does, into the test's database.

What is pinned is mostly what the reader refuses to say: a tripped fuse is
down, but a paused game, a gap in the polls and a feed that stopped are not
observed - never down, never idle, never zero.
"""

from __future__ import annotations

import gzip
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from fsmes.domain import EquipmentState, EquipmentStateName, ProductionLog
from fsmes.domain.equipment import ConnectionStateName, EquipmentConnection
from fsmes.domain.masterdata import Equipment
from fsmes.integrations.satisfactory import feed, pack, reader, rules

FIXTURE = Path(__file__).parent / "fixtures" / "satisfactory" / "2026-10-28.jsonl.gz"

# The fixture's moments, as the naive UTC the MES keeps (Chicago is UTC-5).
FIRST = datetime(2026, 10, 29, 2, 3, 15)
FUSE_RESET = datetime(2026, 10, 29, 2, 6, 16)
BEFORE_GAP = datetime(2026, 10, 29, 2, 7, 16)
AFTER_GAP = datetime(2026, 10, 29, 2, 11, 16)
PAUSED = datetime(2026, 10, 29, 2, 13, 17)
LAST = datetime(2026, 10, 29, 2, 15, 17)


@pytest.fixture()
def folder(tmp_path) -> Path:
    shutil.copy(FIXTURE, tmp_path / FIXTURE.name)
    return tmp_path


def _replay(folder: Path, scope, now: datetime | None = None) -> reader.Reader:
    # `now` just after the last poll: the feed is still running.
    return reader.replay(folder, scope, now=now or LAST + timedelta(seconds=30))


def _game_buildings(session) -> list[Equipment]:
    """The game's buildings, not the demo plant every test database has."""
    return [e for e in session.scalars(select(Equipment).order_by(Equipment.code))
            if e.code.startswith(("SMELTERMK1-", "CONSTRUCTORMK1-", "ASSEMBLERMK1-"))]


def _intervals(session, code: str) -> list[EquipmentState]:
    machine = session.scalar(select(Equipment).where(Equipment.code == code))
    return list(session.scalars(select(EquipmentState)
                                .where(EquipmentState.equipment_id == machine.id)
                                .order_by(EquipmentState.started_at, EquipmentState.id)))


def _connections(session, code: str) -> list[EquipmentConnection]:
    machine = session.scalar(select(Equipment).where(Equipment.code == code))
    return list(session.scalars(select(EquipmentConnection)
                                .where(EquipmentConnection.equipment_id == machine.id)
                                .order_by(EquipmentConnection.started_at, EquipmentConnection.id)))


def _fused_at_first_poll() -> list[str]:
    first = feed.read_file(FIXTURE)[0]
    return [rules.code(b["ID"]) for b in first.factory
            if (b.get("PowerInfo") or {}).get("FuseTriggered")]


# ------------------------------------------------------------------ the fixture


def test_the_fixture_is_ten_polls_with_a_gap_and_one_paused_poll():
    polls = feed.read_file(FIXTURE)
    # Twelve moments: twice the poller wrote the session answer a second
    # after the rest of its poll, and each reads as a poll with no buildings.
    assert len(polls) == 12
    assert sum(1 for p in polls if p.factory is None) == 2
    assert [p.ts for p in polls if p.paused] == [PAUSED]
    assert polls[0].ts == FIRST
    assert len(_fused_at_first_poll()) == 14


# ------------------------------------------------------------------ fuse


def test_polls_with_a_tripped_fuse_become_down_intervals_with_the_reason_fuse(folder, session, scope):
    _replay(folder, scope)
    fused = _fused_at_first_poll()
    for code in fused:
        first = _intervals(session, code)[0]
        assert first.state is EquipmentStateName.DOWN, code
        assert first.reason_code == "fuse", code
        assert first.started_at == FIRST
        # Down until the poll that saw the fuse reset - not a minute longer.
        assert first.ended_at == FUSE_RESET, code


def test_fuse_is_the_largest_cause_of_downtime_in_the_evening(folder, session, scope):
    _replay(folder, scope)
    buildings = [m.id for m in _game_buildings(session)]
    seconds = func.sum(func.julianday(EquipmentState.ended_at) - func.julianday(EquipmentState.started_at))
    if session.get_bind().dialect.name != "sqlite":
        seconds = func.sum(func.extract("epoch", EquipmentState.ended_at - EquipmentState.started_at))
    down = session.execute(
        select(EquipmentState.reason_code, seconds)
        .where(EquipmentState.state == EquipmentStateName.DOWN,
               EquipmentState.equipment_id.in_(buildings),
               EquipmentState.ended_at.is_not(None))
        .group_by(EquipmentState.reason_code)
        .order_by(seconds.desc())).all()
    assert down[0][0] == "fuse"


# ------------------------------------------------------------------ not observed


def test_a_gap_in_the_polls_is_not_observed_with_its_cause_and_not_down(folder, session, scope):
    _replay(folder, scope)
    for building in _game_buildings(session):
        lost = [c for c in _connections(session, building.code)
                if c.state is ConnectionStateName.DISCONNECTED and c.started_at < AFTER_GAP]
        assert len(lost) == 1, building.code
        # Unseen from one poll interval after the last poll before the gap,
        # to the first poll after it, with the cause written down.
        assert lost[0].started_at == BEFORE_GAP + timedelta(seconds=60)
        assert lost[0].ended_at == AFTER_GAP
        assert "no poll from the game" in lost[0].reason
        # No state interval covers the gap: the history stops and resumes.
        for interval in _intervals(session, building.code):
            assert not (interval.started_at < AFTER_GAP and
                        (interval.ended_at is None or interval.ended_at > lost[0].started_at)
                        and interval.started_at >= lost[0].started_at), building.code


def test_a_paused_game_is_not_observed_and_not_down(folder, session, scope):
    _replay(folder, scope)
    for building in _game_buildings(session):
        paused = [c for c in _connections(session, building.code)
                  if c.state is ConnectionStateName.DISCONNECTED and c.started_at == PAUSED]
        assert len(paused) == 1, building.code
        assert paused[0].reason == "the game was paused"
        assert not [i for i in _intervals(session, building.code) if i.started_at == PAUSED]


def test_when_the_feed_stops_every_building_reads_not_observed_rather_than_down(folder, session, scope):
    # The plant is looked at an hour after the last poll: the game was
    # closed, or the poller is off, and the file cannot say which.
    _replay(folder, scope, now=LAST + timedelta(hours=1))
    for building in _game_buildings(session):
        last = _connections(session, building.code)[-1]
        assert last.state is ConnectionStateName.DISCONNECTED
        assert last.ended_at is None
        assert last.started_at == LAST + timedelta(seconds=60)
        assert "the game is closed or the poller is off" in last.reason
        assert all(i.ended_at is not None for i in _intervals(session, building.code)), \
            "a building nobody can see has no open state"


def test_a_folder_with_no_poll_file_writes_nothing_at_all(tmp_path, session, scope):
    before = session.scalar(select(func.count()).select_from(EquipmentState))
    done = reader.replay(tmp_path, scope, now=LAST)
    assert done.report.polls == 0
    assert session.scalar(select(func.count()).select_from(EquipmentState)) == before


# ------------------------------------------------------------------ production


def _poll(ts: datetime, rate: float, *, paused: bool = False) -> dict:
    building = {"ID": "Build_SmelterMk1_C_1", "Name": "Smelter", "Recipe": "Iron Ingot",
                "IsConfigured": True, "IsProducing": rate > 0, "IsPaused": False,
                "PowerInfo": {"CircuitID": 1, "FuseTriggered": False},
                "production": [{"Name": "Iron Ingot", "ClassName": "Desc_IronIngot_C",
                                "Amount": 1, "MaxAmount": 100, "CurrentProd": rate,
                                "MaxProd": 30}],
                "ingredients": [], "InputInventory": [], "OutputInventory": []}
    stamp = ts.isoformat() + "+00:00"
    return [{"ts": stamp, "endpoint": "getFactory", "data": [building]},
            {"ts": stamp, "endpoint": "getSessionInfo", "data": {"IsPaused": paused}}]


def _write(folder: Path, polls: list[list[dict]]) -> None:
    with (folder / "2026-10-30.jsonl").open("w", encoding="utf-8") as fh:
        for poll in polls:
            for line in poll:
                fh.write(json.dumps(line) + "\n")


def _booked(session) -> float:
    machine = session.scalar(select(Equipment).where(Equipment.code == "SMELTERMK1-1"))
    return session.scalar(select(func.coalesce(func.sum(ProductionLog.good_qty), 0))
                          .where(ProductionLog.equipment_id == machine.id))


def test_bookings_are_the_games_rate_times_the_minutes_between_polls(tmp_path, session, scope):
    t0 = datetime(2026, 10, 30, 12, 0, 0)
    _write(tmp_path, [_poll(t0 + timedelta(minutes=i), 30) for i in range(4)])
    reader.replay(tmp_path, scope, now=t0 + timedelta(minutes=4))
    # Three minutes between the first poll and the last, at 30 a minute.
    assert _booked(session) == 90


def test_nothing_is_booked_across_a_gap_or_a_pause(tmp_path, session, scope):
    t0 = datetime(2026, 10, 30, 12, 0, 0)
    _write(tmp_path, [
        _poll(t0, 30), _poll(t0 + timedelta(minutes=1), 30),          # 30
        _poll(t0 + timedelta(minutes=10), 30),                        # after a gap: 0
        _poll(t0 + timedelta(minutes=11), 30, paused=True),           # paused: 0
        _poll(t0 + timedelta(minutes=12), 30),                        # after the pause: 0
        _poll(t0 + timedelta(minutes=13), 30),                        # 30
    ])
    reader.replay(tmp_path, scope, now=t0 + timedelta(minutes=13))
    assert _booked(session) == 60


def test_a_reader_that_starts_again_books_nothing_twice(folder, session, scope):
    _replay(folder, scope)
    once = session.scalar(select(func.coalesce(func.sum(ProductionLog.good_qty), 0)))
    states = session.scalar(select(func.count()).select_from(EquipmentState))
    again = _replay(folder, scope)
    assert again.report.polls == 0
    assert session.scalar(select(func.coalesce(func.sum(ProductionLog.good_qty), 0))) == once
    assert session.scalar(select(func.count()).select_from(EquipmentState)) == states
    assert once > 0


# ------------------------------------------------------------------ rules


def _building(**over) -> dict:
    base = {"ID": "Build_ConstructorMk1_C_7", "Name": "Constructor", "Recipe": "Iron Rod",
            "IsConfigured": True, "IsProducing": False, "IsPaused": False,
            "PowerInfo": {"CircuitID": 1, "FuseTriggered": False},
            "production": [{"ClassName": "Rod", "Amount": 1, "MaxAmount": 200,
                            "CurrentProd": 0, "MaxProd": 15}],
            "ingredients": [{"ClassName": "Ingot", "Amount": 1}],
            "InputInventory": [{"ClassName": "Ingot", "Amount": 50, "MaxAmount": 100}],
            "OutputInventory": [{"ClassName": "Rod", "Amount": 3, "MaxAmount": 200}]}
    base.update(over)
    return base


def test_an_empty_input_is_starved_and_a_full_output_is_blocked_both_idle():
    starved = rules.read(_building(InputInventory=[{"ClassName": "Ingot", "Amount": 0}]))
    blocked = rules.read(_building(OutputInventory=[{"ClassName": "Rod", "Amount": 200,
                                                     "MaxAmount": 200}]))
    assert (starved.state, starved.reason_code) == (EquipmentStateName.IDLE, "starved")
    assert (blocked.state, blocked.reason_code) == (EquipmentStateName.IDLE, "blocked")


def test_every_building_on_a_tripped_circuit_is_down_for_the_fuse_even_unflagged():
    power = [{"FuseTriggered": True, "AssociatedCircuits": [1]}]
    reading = rules.read(_building(IsProducing=True), rules.tripped_circuits(power))
    assert (reading.state, reading.reason_code) == (EquipmentStateName.DOWN, "fuse")


def test_a_building_switched_off_or_with_no_recipe_or_no_power_says_which():
    assert rules.read(_building(IsPaused=True)).reason_code == "paused"
    assert rules.read(_building(Recipe="Unassigned", IsConfigured=False)).reason_code == "no_recipe"
    unpowered = rules.read(_building(PowerInfo={"CircuitID": -1, "FuseTriggered": False}))
    assert (unpowered.state, unpowered.reason_code) == (EquipmentStateName.DOWN, "no_power")
    assert rules.read(_building(IsProducing=True)).state is EquipmentStateName.RUNNING


def test_a_building_is_coded_by_its_game_id_and_named_by_what_it_makes():
    assert rules.code("Build_ConstructorMk1_C_2146677430") == "CONSTRUCTORMK1-2146677430"
    assert rules.display_name(_building()) == "Constructor · Iron Rod"
    assert rules.display_name(_building(Recipe="Unassigned", IsConfigured=False)) == "Constructor"


# ------------------------------------------------------------------ pack and plant


TREE = (("ENT", "Enterprise"), ("ST", "Site"), ("AR", "Area"))


def test_the_pack_master_data_from_the_fixture_has_every_building_and_the_fuse_reason():
    data = pack.build(feed.read_file(FIXTURE), TREE)
    units = [e for e in data["equipment"] if e["level"] == "work_unit"]
    assert len(units) == 18
    assert {"code": "fuse"} .items() <= next(r for r in data["downtime_reasons"]
                                              if r["code"] == "fuse").items()
    assert all(u["parent"] in {e["code"] for e in data["equipment"]} for u in units)
    centres = [e for e in data["equipment"] if e["level"] == "work_center"]
    assert centres and all(c["parent"] == "AR" for c in centres), \
        "the work centres hang under the area the pack's author named"


def test_a_building_the_pack_never_saw_is_filed_under_the_plants_own_area(folder, session, scope):
    from fsmes.domain.masterdata import EquipmentLevel
    from fsmes.services import masterdata

    masterdata.create_equipment(session, code="GAME-AREA", name="The game's area",
                                level=EquipmentLevel.AREA, actor="test")
    session.commit()
    reader.replay(folder, scope, area="GAME-AREA", now=LAST + timedelta(seconds=30))
    session.expire_all()
    smelters = session.scalar(select(Equipment).where(Equipment.code == "SMELTERS"))
    assert smelters.parent is not None and smelters.parent.code == "GAME-AREA"


def test_without_an_area_a_new_work_centre_stands_on_its_own(folder, session, scope):
    reader.replay(folder, scope, area="NO-SUCH-AREA", now=LAST + timedelta(seconds=30))
    session.expire_all()
    smelters = session.scalar(select(Equipment).where(Equipment.code == "SMELTERS"))
    assert smelters.parent is None, "the reader names no level of the plant's tree itself"


def test_the_satisfactory_pack_checks_and_runs_its_reader_beside_the_api():
    from fsmes import plant
    from fsmes.pack import fleet
    from fsmes.pack import format as fmt

    root = Path(__file__).resolve().parents[1] / "labs" / "satisfactory"
    cfg = fleet.compile_pack(fmt.read(root))
    assert cfg["simulate"] is False
    assert [p.args for p in plant.processes(cfg)] == [("run-api",), ("run-satisfactory",)]
    note = cfg["env"]["MES_SOURCE_NOTE"]
    assert "simulated by the game, not by us" in note
    assert "the game has no scrap" in note


def test_health_carries_the_plants_sentence_about_where_its_data_comes_from(anon, monkeypatch):
    from fsmes import config

    monkeypatch.setenv("MES_SOURCE_NOTE", "a game factory")
    config.get_settings.cache_clear()
    try:
        assert anon.get("/health").json()["source"] == "a game factory"
    finally:
        monkeypatch.delenv("MES_SOURCE_NOTE")
        config.get_settings.cache_clear()
    assert anon.get("/health").json()["source"] is None


def test_the_fixture_is_gzipped_utf8_json_lines():
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as fh:
        assert all(json.loads(line)["endpoint"] in {"getFactory", "getPower", "getSessionInfo"}
                   for line in fh)
