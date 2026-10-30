"""The two warts of the first Satisfactory slice, pinned.

**Booked production implies run time.** The game's `CurrentProd` is a rolling
figure: a constructor that has run out of ingots goes on reporting a rate for
minutes after the game's own `IsProducing` says it stopped. The first slice
booked that rate whatever state it had just written, so the OEE page flagged
most constructors with "counted work outruns the run time". A poll now books
only for an interval the MES holds as *running*.

**One save on the floor.** 2026-10-28's evening was the save "Hello World",
2026-10-29's the save "fsmes". The first slice kept the old save's buildings
on the floor as disconnected. A building list under a new save name now
retires every building not in it, history kept, and the bar names the save.

The two committed fixtures are those two evenings, trimmed (see the
fixtures' README).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from fsmes.domain import EquipmentState, ProductionLog
from fsmes.domain.equipment import ConnectionStateName, EquipmentConnection
from fsmes.domain.masterdata import Equipment
from fsmes.integrations.satisfactory import feed, reader, rules
from fsmes.services import analysis, connection

FIXTURES = Path(__file__).parent / "fixtures" / "satisfactory"
HELLO_WORLD = FIXTURES / "2026-10-28.jsonl.gz"
FSMES = FIXTURES / "2026-10-29.jsonl.gz"


def _codes(path: Path) -> set[str]:
    return {rules.code(b["ID"]) for p in feed.read_file(path) for b in p.factory or []}


def _last(path: Path) -> datetime:
    return feed.read_file(path)[-1].ts


@pytest.fixture()
def both(tmp_path) -> Path:
    for path in (HELLO_WORLD, FSMES):
        shutil.copy(path, tmp_path / path.name)
    return tmp_path


# ------------------------------------------------------------------ hand-made polls


def _building(ident: str, *, producing: bool, rate: float, starved: bool = False) -> dict:
    return {"ID": f"Build_ConstructorMk1_C_{ident}", "Name": "Constructor",
            "Recipe": "Iron Rod", "IsConfigured": True, "IsProducing": producing,
            "IsPaused": False, "PowerInfo": {"CircuitID": 1, "FuseTriggered": False},
            "production": [{"Name": "Iron Rod", "ClassName": "Rod", "Amount": 1,
                            "MaxAmount": 200, "CurrentProd": rate, "MaxProd": 15}],
            "ingredients": [{"ClassName": "Ingot", "Amount": 1}],
            "InputInventory": [{"ClassName": "Ingot", "Amount": 0 if starved else 50,
                                "MaxAmount": 100}],
            "OutputInventory": [{"ClassName": "Rod", "Amount": 3, "MaxAmount": 200}]}


def _poll(ts: datetime, buildings: list[dict], save: str | None = "S") -> list[dict]:
    stamp = ts.isoformat() + "+00:00"
    lines = [{"ts": stamp, "endpoint": "getFactory", "data": buildings}]
    if save is not None:
        lines.append({"ts": stamp, "endpoint": "getSessionInfo",
                      "data": {"SessionName": save, "IsPaused": False}})
    return lines


def _write(folder: Path, polls: list[list[dict]]) -> None:
    with (folder / "2026-10-30.jsonl").open("w", encoding="utf-8") as fh:
        for poll in polls:
            for line in poll:
                fh.write(json.dumps(line) + "\n")


def _booked(session, code: str) -> int:
    machine = session.scalar(select(Equipment).where(Equipment.code == code))
    return session.scalar(select(func.coalesce(func.sum(ProductionLog.good_qty), 0))
                          .where(ProductionLog.equipment_id == machine.id))


T0 = datetime(2026, 10, 30, 12, 0, 0)


def test_a_building_starved_for_three_polls_while_the_game_still_reads_4_a_minute_books_nothing_for_them(
        tmp_path, session, scope):
    running = _building("1", producing=True, rate=4)
    starved = _building("1", producing=False, rate=4, starved=True)
    _write(tmp_path, [_poll(T0, [running]),
                      _poll(T0 + timedelta(minutes=1), [running]),     # 4: ran 12:00-12:01
                      _poll(T0 + timedelta(minutes=2), [starved]),     # 4: ran 12:01-12:02
                      _poll(T0 + timedelta(minutes=3), [starved]),     # starved 12:02-12:03: 0
                      _poll(T0 + timedelta(minutes=4), [starved]),     # starved 12:03-12:04: 0
                      _poll(T0 + timedelta(minutes=5), [running])])    # starved 12:04-12:05: 0
    reader.replay(tmp_path, scope, now=T0 + timedelta(minutes=5))
    assert _booked(session, "CONSTRUCTORMK1-1") == 8


def test_a_running_building_books_its_rate_times_the_minutes_as_before(tmp_path, session, scope):
    running = _building("1", producing=True, rate=4)
    _write(tmp_path, [_poll(T0 + timedelta(minutes=i), [running]) for i in range(6)])
    reader.replay(tmp_path, scope, now=T0 + timedelta(minutes=6))
    assert _booked(session, "CONSTRUCTORMK1-1") == 20


def test_a_reader_started_again_over_both_evenings_books_and_retires_nothing_twice(both, session, scope):
    reader.replay(both, scope, now=_last(FSMES) + timedelta(seconds=30))
    made = session.scalar(select(func.coalesce(func.sum(ProductionLog.good_qty), 0)))
    states = session.scalar(select(func.count()).select_from(EquipmentState))
    again = reader.replay(both, scope, now=_last(FSMES) + timedelta(seconds=30))
    assert again.report.polls == 0
    assert again.report.retired == 0 and again.report.returned == 0
    assert session.scalar(select(func.coalesce(func.sum(ProductionLog.good_qty), 0))) == made
    assert session.scalar(select(func.count()).select_from(EquipmentState)) == states
    assert made > 0


# ------------------------------------------------------------------ one save


def test_a_poll_under_a_new_save_retires_the_old_saves_buildings_and_the_floor_counts_only_the_new(
        both, session, scope):
    before = connection.watching(session)["machines"]
    done = reader.replay(both, scope, now=_last(FSMES) + timedelta(seconds=30))
    old, new = _codes(HELLO_WORLD), _codes(FSMES)
    assert old and new and not old & new
    session.expire_all()
    for code in old:
        machine = session.scalar(select(Equipment).where(Equipment.code == code))
        assert machine.retired_at is not None, code
        assert machine.retired_reason == "not in save fsmes", code
        # Off the floor, not out of the record: its states are still there.
        assert session.scalar(select(func.count()).select_from(EquipmentState)
                              .where(EquipmentState.equipment_id == machine.id)) > 0
    # The new save's own buildings that the player tore down during the day
    # leave as well, with their own reason (five lists in a row without them).
    torn = {code for code in new if session.scalar(
        select(Equipment.retired_reason).where(Equipment.code == code)) == "torn down"}
    assert done.report.retired == len(old) + len(torn)
    assert connection.watching(session)["machines"] == before + len(new) - len(torn)
    assert not old & {e.code for e in analysis.masterdata.work_units_under(
        session, session.scalar(select(Equipment).where(Equipment.code == "CONSTRUCTORS")))}


def test_the_old_saves_buildings_leave_with_the_cause_not_as_gone_from_the_list(both, session, scope):
    reader.replay(both, scope, now=_last(FSMES) + timedelta(seconds=30))
    for code in _codes(HELLO_WORLD):
        machine = session.scalar(select(Equipment).where(Equipment.code == code))
        last = session.scalar(select(EquipmentConnection)
                              .where(EquipmentConnection.equipment_id == machine.id)
                              .order_by(EquipmentConnection.started_at.desc(),
                                        EquipmentConnection.id.desc()).limit(1))
        assert last.state is ConnectionStateName.DISCONNECTED
        # The feed stopped at the end of the evening before; the save change is
        # the next thing said about them only if they were still seen. Either
        # way nothing calls them torn down.
        assert last.reason != "no longer in the game's building list", code


def test_the_same_save_loaded_again_brings_its_buildings_back(tmp_path, session, scope):
    a, b = _building("1", producing=True, rate=4), _building("2", producing=True, rate=4)
    _write(tmp_path, [_poll(T0, [a], save="Hello World"),
                      _poll(T0 + timedelta(minutes=1), [b], save="fsmes"),
                      _poll(T0 + timedelta(minutes=2), [a], save="Hello World")])
    done = reader.replay(tmp_path, scope, now=T0 + timedelta(minutes=2))
    session.expire_all()
    first = session.scalar(select(Equipment).where(Equipment.code == "CONSTRUCTORMK1-1"))
    second = session.scalar(select(Equipment).where(Equipment.code == "CONSTRUCTORMK1-2"))
    assert first.retired_at is None and first.retired_reason is None
    assert second.retired_reason == "not in save Hello World"
    assert (done.report.retired, done.report.returned) == (2, 1)


def test_a_poll_without_a_session_answer_changes_nothing(tmp_path, session, scope):
    a, b = _building("1", producing=True, rate=4), _building("2", producing=True, rate=4)
    _write(tmp_path, [_poll(T0, [a, b], save="fsmes"),
                      # Building 2 torn down, and this poll's session answer
                      # missing: it is gone from the list, not from the save.
                      _poll(T0 + timedelta(minutes=1), [a], save=None),
                      _poll(T0 + timedelta(minutes=2), [a], save="fsmes")])
    done = reader.replay(tmp_path, scope, now=T0 + timedelta(minutes=2))
    session.expire_all()
    torn = session.scalar(select(Equipment).where(Equipment.code == "CONSTRUCTORMK1-2"))
    assert torn.retired_at is None
    assert done.report.retired == 0
    assert reader.source_note(session, "x") == "save fsmes · x"


def test_health_names_the_save_on_the_floor_in_front_of_the_plants_sentence(both, scope, anon, monkeypatch):
    from fsmes import config

    reader.replay(both, scope, now=_last(FSMES) + timedelta(seconds=30))
    monkeypatch.setenv("MES_SOURCE_NOTE", "simulated by the game, not by us")
    monkeypatch.setenv("MES_SATISFACTORY_FEED_DIR", str(both))
    config.get_settings.cache_clear()
    try:
        assert anon.get("/health").json()["source"] == "save fsmes · simulated by the game, not by us"
    finally:
        monkeypatch.delenv("MES_SOURCE_NOTE")
        monkeypatch.delenv("MES_SATISFACTORY_FEED_DIR")
        config.get_settings.cache_clear()


# ------------------------------------------------------------------ the OEE page


def _recipe_changed(path: Path) -> set[str]:
    """Buildings whose recipe changed during the evening. The MES holds one
    rated cycle per machine - the latest recipe's - so the units made under
    an earlier, faster recipe are counted against the slower rating, and the
    OEE code rightly refuses the figure. That is the parts mix, the next
    slice's work, and not a booking fault."""
    recipes: dict[str, set[str]] = {}
    for poll in feed.read_file(path):
        for b in poll.factory or []:
            if rules.configured(b):
                recipes.setdefault(rules.code(b["ID"]), set()).add(b["Recipe"])
    return {code for code, seen in recipes.items() if len(seen) > 1}


@pytest.mark.parametrize("fixture", [HELLO_WORLD, FSMES], ids=["hello-world", "fsmes"])
def test_a_replayed_evening_has_no_building_whose_counted_work_outruns_its_run_time_unless_its_recipe_changed(
        fixture, tmp_path, session, scope, monkeypatch):
    shutil.copy(fixture, tmp_path / fixture.name)
    end = _last(fixture) + timedelta(seconds=30)
    reader.replay(tmp_path, scope, now=end)
    monkeypatch.setattr(analysis, "utcnow", lambda: end)
    flagged, figures = set(), 0
    for line in ("CONSTRUCTORS", "SMELTERS"):
        if session.scalar(select(Equipment.id).where(Equipment.code == line)) is None:
            continue
        stations = analysis.oee_breakdown(session, line_code=line, hours=8)["stations"]
        flagged |= {s["code"] for s in stations if s["counts_outrun_run_time"]}
        figures += sum(1 for s in stations if s["performance"] is not None)
    assert flagged <= _recipe_changed(fixture)
    assert figures > 0
