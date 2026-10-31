"""The line view lays a plant out where its machines stand.

The MES holds where a machine stands - x and y across the floor and z its
height, in metres, and a heading in degrees - when a source has said. The
Satisfactory reader is the first such source: every building in every poll
carries the game's `location`, in centimetres. The line view places each
station from the first source that has it: a hand-drawn `line_layout.json`
entry, then the held position, then the even run from routing order it has
always had. A plant that has never said where anything stands lays out
exactly as before.

The 2026-10-30 fixture is the evening of save "fsmes", with each building's
`location` kept (see the fixtures' README).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from fsmes.config import get_settings
from fsmes.domain.masterdata import Equipment, EquipmentLevel
from fsmes.integrations.satisfactory import feed, reader, rules
from fsmes.services import line as line_service
from fsmes.services import masterdata

FIXTURES = Path(__file__).parent / "fixtures" / "satisfactory"
EVENING = FIXTURES / "2026-10-30.jsonl.gz"
T0 = datetime(2026, 10, 31, 12, 0, 0)


def _smelter(ident: str, x: float, y: float, z: float, rotation: float) -> dict:
    return {"ID": f"Build_SmelterMk1_C_{ident}", "Name": "Smelter", "Recipe": "Iron Ingot",
            "IsConfigured": True, "IsProducing": False, "IsPaused": False,
            "PowerInfo": {"CircuitID": 1, "FuseTriggered": False},
            "location": {"x": x, "y": y, "z": z, "rotation": rotation, "pitch": 0},
            "production": [{"Name": "Iron Ingot", "ClassName": "Ingot", "Amount": 1,
                            "MaxAmount": 100, "CurrentProd": 0, "MaxProd": 30}]}


def _replay(folder: Path, scope, polls: list[list[dict]], **kwargs) -> reader.Reader:
    with (folder / "2026-10-31.jsonl").open("w", encoding="utf-8") as fh:
        for i, buildings in enumerate(polls):
            stamp = (T0 + timedelta(minutes=i)).isoformat() + "+00:00"
            fh.write(json.dumps({"ts": stamp, "endpoint": "getFactory", "data": buildings}) + "\n")
            fh.write(json.dumps({"ts": stamp, "endpoint": "getSessionInfo",
                                 "data": {"SessionName": "S", "IsPaused": False}}) + "\n")
    return reader.replay(folder, scope, now=T0 + timedelta(minutes=len(polls)), **kwargs)


def _machine(session, code: str) -> Equipment:
    session.expire_all()
    return session.scalar(select(Equipment).where(Equipment.code == code))


@pytest.fixture()
def no_drawing(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "line_layout_file", tmp_path / "absent.json")


def _drawing(monkeypatch, tmp_path, drawn: dict) -> None:
    path = tmp_path / "line_layout.json"
    path.write_text(json.dumps(drawn), encoding="utf-8")
    monkeypatch.setattr(get_settings(), "line_layout_file", path)


# ------------------------------------------------------------------ what is held


def test_a_building_at_game_centimetres_is_held_in_metres_with_its_height_and_heading(
        tmp_path, session, scope):
    _replay(tmp_path, scope, [[_smelter("1", -43794.9, -139688.7, 8236.5, 200)]])
    machine = _machine(session, "SMELTERMK1-1")
    assert round(machine.pos_x, 1) == -437.9
    assert round(machine.pos_y, 1) == -1396.9
    assert round(machine.pos_z, 1) == 82.4
    assert machine.heading == 200.0


def test_a_building_that_stays_put_is_placed_once_however_many_polls_see_it(
        tmp_path, session, scope):
    building = _smelter("1", 100.0, 200.0, 0.0, 90)
    done = _replay(tmp_path, scope, [[building]] * 4)
    assert done.report.buildings_placed == 1
    again = _replay(tmp_path, scope, [[building]] * 5)
    assert again.report.buildings_placed == 0, "a reader started again has nothing new to hold"


def test_a_building_with_no_location_holds_no_position_rather_than_the_origin(
        tmp_path, session, scope):
    building = _smelter("1", 0, 0, 0, 0)
    del building["location"]
    _replay(tmp_path, scope, [[building]])
    machine = _machine(session, "SMELTERMK1-1")
    assert masterdata.position(machine) is None
    assert rules.position({"location": {"x": "?", "y": 1}}) is None


def test_a_building_with_no_rotation_has_an_unknown_heading_not_a_heading_of_zero():
    assert rules.position({"location": {"x": 100, "y": 200}}) == (1.0, 2.0, None, None)


# ------------------------------------------------------------------ the line view


def test_a_line_whose_stations_hold_positions_is_framed_on_their_centre(
        tmp_path, session, scope, no_drawing):
    _replay(tmp_path, scope, [[_smelter("1", -43000, -139000, 8000, 0),
                               _smelter("2", -41000, -138000, 8000, 90),
                               _smelter("3", -42000, -136000, 9000, 270)]])
    scene = line_service.layout(session, "SMELTERS")
    assert scene["plan"] is True
    where = {s["code"]: (s["x"], s["z"]) for s in scene["stations"]}
    # The box spans x -430..-410 and y -1390..-1360: its middle is (-420, -1375).
    assert where == {"SMELTERMK1-1": (-10.0, -15.0), "SMELTERMK1-2": (10.0, -5.0),
                     "SMELTERMK1-3": (0.0, 15.0)}
    assert {s["placed"] for s in scene["stations"]} == {"held"}
    assert {s["code"]: s["height"] for s in scene["stations"]}["SMELTERMK1-3"] == 90.0


def test_a_held_heading_turns_the_station_the_way_the_building_faces(
        tmp_path, session, scope, no_drawing):
    """The game's rotation turns clockwise seen from above; the view's `rot`
    turns the other way, so a building at 90 is drawn at 270."""
    _replay(tmp_path, scope, [[_smelter("1", 0, 0, 0, 90), _smelter("2", 1000, 0, 0, 0)]])
    rot = {s["code"]: s["rot"] for s in line_service.layout(session, "SMELTERS")["stations"]}
    assert rot == {"SMELTERMK1-1": 270.0, "SMELTERMK1-2": 0.0}


def test_a_station_with_a_drawn_entry_keeps_the_drawn_place_over_the_held_one(
        tmp_path, session, scope, monkeypatch):
    _drawing(monkeypatch, tmp_path, {"lines": {"SMELTERS": {"stations": {
        "SMELTERMK1-1": {"x": 3.0, "z": 4.0, "rot": 45}}}}})
    _replay(tmp_path, scope, [[_smelter("1", -43000, -139000, 8000, 0),
                               _smelter("2", -41000, -138000, 8000, 90)]])
    stations = {s["code"]: s for s in line_service.layout(session, "SMELTERS")["stations"]}
    drawn = stations["SMELTERMK1-1"]
    assert (drawn["x"], drawn["z"], drawn["rot"], drawn["placed"]) == (3.0, 4.0, 45.0, "drawn")
    assert stations["SMELTERMK1-2"]["placed"] == "held"


def test_a_machine_with_no_position_on_a_floor_plan_stands_in_a_row_past_its_edge(
        tmp_path, session, scope, no_drawing):
    """Unknown is not a place on the floor: it is drawn apart, and says so."""
    lost = _smelter("3", 0, 0, 0, 0)
    del lost["location"]
    _replay(tmp_path, scope, [[_smelter("1", 0, 0, 0, 0), _smelter("2", 2000, 1000, 0, 0), lost]])
    stations = {s["code"]: s for s in line_service.layout(session, "SMELTERS")["stations"]}
    held_edge = max(stations[c]["z"] for c in ("SMELTERMK1-1", "SMELTERMK1-2"))
    assert stations["SMELTERMK1-3"]["placed"] == "routing"
    assert stations["SMELTERMK1-3"]["z"] > held_edge


def test_a_plant_whose_machines_hold_no_position_lays_out_exactly_as_before(session, no_drawing):
    scene = line_service.layout(session)
    assert scene["plan"] is False
    assert [s["x"] for s in scene["stations"]] == [i * line_service.DEFAULT_SPACING
                                                    for i in range(len(scene["stations"]))]
    assert {s["z"] for s in scene["stations"]} == {0.0}
    assert {s["placed"] for s in scene["stations"]} == {"routing"}
    assert not any(entry["area"] for entry in scene["lines"]), "no area is offered"


# ------------------------------------------------------------------ the whole factory


@pytest.fixture()
def factory(tmp_path, session, scope, no_drawing):
    masterdata.create_equipment(session, code="FACTORY", name="Factory",
                                level=EquipmentLevel.AREA, actor="test")
    session.commit()
    shutil.copy(EVENING, tmp_path / EVENING.name)
    polls = feed.read_file(EVENING)
    reader.replay(tmp_path, scope, area="FACTORY", now=polls[-1].ts + timedelta(seconds=30))
    return polls


def test_the_area_view_lists_every_building_of_the_save(factory, session):
    """Every building and generator the save's unpaused polls named, from
    every work centre at once - less the ones the reader retired as torn down,
    which are off the floor. (The evening's last poll is a paused game: what
    it names was not seen.)"""
    seen = [p for p in factory if not p.paused]
    named = {rules.code(b["ID"]) for p in seen for b in p.factory or []}
    named |= {rules.code(g["ID"]) for p in seen for g in p.generators or []}
    retired = set(session.scalars(select(Equipment.code).where(
        Equipment.code.in_(named), Equipment.retired_at.is_not(None))))
    scene = line_service.layout(session, "FACTORY")
    codes = {s["code"] for s in scene["stations"]}
    assert codes == named - retired
    assert len(codes) > len(line_service.layout(session)["stations"]), "more than one line's worth"
    assert scene["line"]["area"] is True
    assert {s["placed"] for s in scene["stations"]} == {"held"}
    offered = {entry["code"]: entry for entry in scene["lines"]}
    assert offered["FACTORY"]["area"] is True
    assert offered["FACTORY"]["stations"] == len(codes)


def test_the_area_view_is_framed_on_the_factory_and_not_on_the_games_origin(factory, session):
    scene = line_service.layout(session, "FACTORY")
    xs = [s["x"] for s in scene["stations"]]
    zs = [s["z"] for s in scene["stations"]]
    assert min(xs) == pytest.approx(-max(xs), abs=0.02)
    assert min(zs) == pytest.approx(-max(zs), abs=0.02)
    assert max(xs) < 500 and max(zs) < 500, "a building a kilometre out would be the origin's doing"


def test_the_area_has_events_like_a_line(factory, session):
    events = line_service.events(session, "FACTORY")
    assert len(events["stations"]) == len(line_service.layout(session, "FACTORY")["stations"])


def test_a_name_that_is_neither_a_line_nor_a_placed_area_is_still_not_found(factory, session):
    from fsmes.services import NotFound

    with pytest.raises(NotFound):
        line_service.layout(session, "NO-SUCH-LINE")
