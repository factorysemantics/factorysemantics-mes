"""The 3D line view points at a heater band, and an engineer moves it (2026-11-28).

Each of the cutlery plant's moulds carries four heater bands and a melt
temperature sensor as parts of the mould (#335). The line view's data now
carries them too, so the view can draw a marker on each: where the part is
held to sit, which cost center pays for it and from where, and the repair
order open on it - the one that turns its marker red.

A move is a change of the part's place through Setup's own route
(`PUT /masterdata/equipment/{code}`, `masterdata.change_equipment`), so it is
audited and only someone who may write master data can make it. This file
holds the data and that gate; the clicking is in
`test_the_3d_view_opens_a_part_and_saves_where_it_was_put.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import fsmes.domain  # noqa: F401  (register all tables)
from fsmes.config import get_settings
from fsmes.db import Base
from fsmes.domain import AuditLog, EquipmentLevel
from fsmes.pack import masterdata as pack
from fsmes.services import line, maintenance, masterdata

CUTLERY = Path(__file__).resolve().parents[1] / "labs" / "cutlery"
FIVE = ["Heater band 1", "Heater band 2", "Heater band 3", "Heater band 4", "Melt temperature sensor"]


@pytest.fixture()
def cutlery(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    pack.seed(session, CUTLERY / "masterdata")
    session.flush()
    monkeypatch.setattr(get_settings(), "line_layout_file", CUTLERY / "line_layout.json")
    yield session
    session.close()
    engine.dispose()


def _mould(view: dict, code: str = "FORK1_Mold") -> dict:
    [station] = [s for s in view["stations"] if s["code"] == code]
    return station


def test_the_line_view_carries_each_mould_s_five_parts_with_place_cost_center_and_open_repair(cutlery):
    maintenance.raise_corrective(cutlery, equipment_code="FORK1_Mold_HeaterZone2",
                                 summary="Heater band 2 open circuit")
    view = line.layout(cutlery, "FORK1LINE")
    mould = _mould(view)
    assert mould["parts_total"] == 5 == len(mould["parts"])
    assert sorted(p["name"] for p in mould["parts"]) == FIVE
    by_code = {p["code"]: p for p in mould["parts"]}

    band2 = by_code["FORK1_Mold_HeaterZone2"]
    assert band2["place"] == {"x": 12.25, "y": 5.6, "z": 1.55}
    assert band2["what"] == "Heater band 2, FORK1 Mold"
    # It bills where its line does until an engineer says otherwise, and says so.
    assert (band2["cost_center"], band2["own_cost_center"], band2["cost_center_from"]) == (
        "CC-MOULD01", None, "Moulding line FORK1")
    assert band2["repairs_total"] == 1
    assert band2["repairs"][0]["summary"] == "Heater band 2 open circuit"
    assert all(p["repairs_total"] == 0 for c, p in by_code.items() if c != "FORK1_Mold_HeaterZone2")
    # The order on the band is not on the mould itself: the mould's marker is the band's.
    assert mould["repairs_total"] == 0


def test_a_part_sits_on_its_mould_where_the_drawing_puts_it(cutlery):
    mould = _mould(line.layout(cutlery, "FORK1LINE"))
    band2 = next(p for p in mould["parts"] if p["code"] == "FORK1_Mold_HeaterZone2")
    # The mould stands at plant (13.8, 5.6); the drawn layout's z is minus plant y.
    assert (mould["x"], mould["z"]) == (13.8, -5.6)
    assert band2["placed"] == "held"
    assert band2["at"] == {"x": -1.55, "y": 1.55, "z": 0.0}


def test_a_layout_that_does_not_say_its_frame_puts_parts_on_a_spot_of_their_own(cutlery, tmp_path, monkeypatch):
    layout = tmp_path / "line_layout.json"
    text = (CUTLERY / "line_layout.json").read_text(encoding="utf-8")
    layout.write_text(text.replace('"plant_frame"', '"not_a_frame"'), encoding="utf-8")
    monkeypatch.setattr(get_settings(), "line_layout_file", layout)
    mould = _mould(line.layout(cutlery, "FORK1LINE"))
    assert mould["frame"] is None
    assert {p["placed"] for p in mould["parts"]} == {"default"}
    assert all(p["at"] is None and p["place"] is not None for p in mould["parts"])


def test_the_line_s_feed_carries_the_repairs_open_on_its_parts_and_says_how_many(cutlery):
    assert line.events(cutlery, "FORK1LINE")["repairs_total"] == 0
    order = maintenance.raise_corrective(cutlery, equipment_code="FORK1_Mold_HeaterZone2",
                                         summary="Heater band 2 open circuit")
    maintenance.raise_corrective(cutlery, equipment_code="FORK2_Mold_HeaterZone1",
                                 summary="Not on this line")
    feed = line.events(cutlery, "FORK1LINE")
    assert feed["repairs_total"] == 1
    assert feed["repairs"] == [{"equipment": "FORK1_Mold_HeaterZone2", "order": order.code,
                                "summary": "Heater band 2 open circuit", "status": "due",
                                "kind": "corrective"}]


# ------------------------------------------------- a move, through Setup's route

@pytest.fixture()
def band(session):
    """Heater band 2 on the demo plant's mixer, with a place of its own."""
    part = masterdata.create_equipment(session, code="MIX01_HeaterZone2", name="Heater band 2",
                                       level=EquipmentLevel.EQUIPMENT_MODULE, parent_code="MIX01")
    part.pos_x, part.pos_y, part.pos_z = 1.0, 2.0, 1.5
    session.flush()
    return part


def test_an_engineer_s_move_saves_and_is_in_the_record_of_who_changed_what(session, band, admin):
    r = admin.put("/masterdata/equipment/MIX01_HeaterZone2", json={"x": 1.4, "y": 2.0, "z": 1.2})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == ["position"]
    [entry] = session.scalars(select(AuditLog).where(
        AuditLog.entity_id == "MIX01_HeaterZone2", AuditLog.action == "equipment.placed"))
    assert entry.before["x"] == 1.0 and entry.after["x"] == 1.4
    assert entry.actor


def test_a_supervisor_s_move_is_refused_and_the_band_stays_where_it_was(session, band, supervisor):
    r = supervisor.put("/masterdata/equipment/MIX01_HeaterZone2", json={"x": 9.0, "y": 9.0})
    assert r.status_code == 403
    assert masterdata.position(masterdata.get_equipment(session, "MIX01_HeaterZone2"))[:2] == (1.0, 2.0)
