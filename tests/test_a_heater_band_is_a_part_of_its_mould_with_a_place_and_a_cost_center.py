"""A heater band is a part of its mould, with a place and a cost center (2026-11-27).

The coil-out scene broke "the mould" when what broke was heater band 2 on its
barrel. Each of the cutlery plant's nine moulds now carries its four barrel
heater bands and its melt temperature sensor as parts of the mould - ISA-95
equipment modules in the one equipment table, no new table - placed where the
studio drew them (`labs/cutlery/coil_places.json`) and billing where the mould
bills until an engineer says otherwise.

What this file holds:

* the shipped pack's 45 parts, their places, and the cost center each
  inherits; none of them on the floor's machine list or a line's OEE;
* a repair order raised on band 2 is still work on the mould: the mould's
  station card and history list it, a reading's maintenance panel finds it,
  the scheduler books it before the mould's production, and the dispatcher
  rosters it on the mould's shift;
* the ERP's `part_used` message names the band and the cost center it
  resolves to - the mould's, unchanged from before but for the band's code,
  or the band's own once Setup gives it one - and ERPNext posts the issue
  there;
* Setup's equipment form changes a part's place and cost center, audited,
  for whoever may write master data.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import fsmes.domain  # noqa: F401  (register all tables)
from fsmes.db import Base, utcnow
from fsmes.domain import AuditLog, Equipment, EquipmentLevel, EquipmentStateName, ErpMessage
from fsmes.integrations.erp.contract import parse_message
from fsmes.pack import masterdata as pack
from fsmes.services import Invalid, calendar, dispatch, equipment, maintenance, masterdata, moment, trend
from fsmes.sim.breakdown import breakdowns

REPO = Path(__file__).resolve().parents[1]
CUTLERY = REPO / "labs" / "cutlery"
PLACES = json.loads((CUTLERY / "coil_places.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------ the cutlery pack

@pytest.fixture(scope="module")
def cutlery():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    pack.seed(session, CUTLERY / "masterdata")
    session.flush()
    yield session
    session.close()
    engine.dispose()


def parts(session) -> list[Equipment]:
    return list(session.scalars(select(Equipment).where(
        Equipment.level == EquipmentLevel.EQUIPMENT_MODULE).order_by(Equipment.code)))


def test_each_of_the_nine_moulds_carries_four_heater_bands_and_a_melt_temperature_sensor(cutlery):
    found = parts(cutlery)
    assert len(found) == 45, f"{len(found)} parts of 45"
    by_mould: dict[str, list[str]] = {}
    for part in found:
        by_mould.setdefault(part.parent.code, []).append(part.name)
    assert len(by_mould) == 9 == len(PLACES["moulds"])
    for mould, names in by_mould.items():
        assert mould.endswith("_Mold")
        assert sorted(names) == ["Heater band 1", "Heater band 2", "Heater band 3",
                                 "Heater band 4", "Melt temperature sensor"], mould


def test_every_part_stands_where_the_studio_drew_it_under_a_code_a_search_for_the_drawing_finds(cutlery):
    drawn = {}
    for mould in PLACES["moulds"]:
        for thing in [*mould["heater_bands"], mould["melt_temp_sensor"]]:
            drawn[thing["id"]] = (thing["x"], thing["y"], thing["z"])
    held = {p.code: (p.pos_x, p.pos_y, p.pos_z) for p in parts(cutlery)}
    assert held == {code.replace(".", "_"): place for code, place in drawn.items()}
    assert all(len(code) <= 40 for code in held)


def test_a_part_has_no_cost_center_of_its_own_and_bills_where_its_mould_bills(cutlery):
    for part in parts(cutlery):
        assert part.cost_center is None, part.code
        assert masterdata.cost_center(cutlery, part) == masterdata.cost_center(cutlery, part.parent)
    band = masterdata.get_equipment(cutlery, "FORK1_Mold_HeaterZone2")
    assert masterdata.cost_center(cutlery, band) == "CC-MOULD01"


def test_no_part_is_on_the_floor_s_machine_list_or_in_a_line_s_oee(cutlery):
    floor = set(cutlery.scalars(select(Equipment.code).where(masterdata.on_the_floor())))
    codes = {p.code for p in parts(cutlery)}
    assert len(floor) == 83 and not floor & codes
    line = masterdata.work_center_of(cutlery, masterdata.get_equipment(cutlery, "FORK1_Mold"))
    assert not {u.code for u in masterdata.work_units_under(cutlery, line)} & codes


def test_a_part_reads_as_itself_and_its_machine(cutlery):
    band = masterdata.get_equipment(cutlery, "FORK1_Mold_HeaterZone2")
    assert masterdata.machine_of(band).code == "FORK1_Mold"
    assert masterdata.what_it_is(band) == f"Heater band 2, {band.parent.name}"
    assert masterdata.what_it_is(band.parent) == band.parent.name


def test_the_coil_out_breakdown_raises_its_order_on_heater_band_2_and_stops_the_mould():
    scene = json.loads((CUTLERY / "scenes" / "coil-out.json").read_text(encoding="utf-8"))
    [rule] = breakdowns(scene)
    assert (rule.machine, rule.raised_on) == ("FORK1_Mold", "FORK1_Mold_HeaterZone2")
    del scene["breakdowns"][0]["on"]
    assert breakdowns(scene)[0].raised_on == "FORK1_Mold"


def test_the_pack_refuses_a_place_with_no_x(tmp_path):
    rows = json.loads((CUTLERY / "masterdata" / "equipment.json").read_text(encoding="utf-8"))
    for row in rows:
        if row["code"] == "FORK1_Mold_HeaterZone2":
            row["position"] = {"y": 5.6}
    folder = tmp_path / "masterdata"
    folder.mkdir()
    for f in (CUTLERY / "masterdata").glob("*.json"):
        (folder / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    (folder / "equipment.json").write_text(json.dumps(rows), encoding="utf-8")
    assert any("stands at" in p for p in pack.problems(folder))


# ------------------------------------------------ an order on the band, on the mould

@pytest.fixture()
def band(session):
    """Heater band 2 on the demo plant's mixer, billing where the mixer does."""
    mixer = masterdata.get_equipment(session, "MIX01")
    mixer.cost_center = "CC-MIX01"
    part = masterdata.create_equipment(session, code="MIX01_HeaterZone2", name="Heater band 2",
                                       level=EquipmentLevel.EQUIPMENT_MODULE, parent_code="MIX01")
    session.flush()
    return part


def _on_the_band(session, summary="Heater band 2 open circuit"):
    return maintenance.raise_corrective(session, equipment_code="MIX01_HeaterZone2", summary=summary)


def test_a_repair_order_on_heater_band_2_is_listed_on_the_mould_s_station_card(session, band, client):
    order = _on_the_band(session)
    session.flush()
    r = client.get("/maintenance/orders", params={"equipment": "MIX01", "status": ["due", "in_progress"]})
    assert r.status_code == 200, r.text
    [row] = [o for o in r.json()["items"] if o["code"] == order.code]
    assert (row["equipment"], row["machine"]) == ("MIX01_HeaterZone2", "MIX01")
    assert row["on"] == f"Heater band 2, {band.parent.name}"
    mine = client.get("/maintenance/orders", params={"equipment": "MIX01_HeaterZone2"}).json()
    assert [o["code"] for o in mine["items"]] == [order.code] and mine["total"] == 1


def test_an_order_on_the_mould_itself_reads_as_the_mould(session, band, client):
    order = maintenance.raise_corrective(session, equipment_code="MIX01", summary="Guard")
    session.flush()
    [row] = [o for o in client.get("/maintenance/orders", params={"equipment": "MIX01"}).json()["items"]
             if o["code"] == order.code]
    assert (row["equipment"], row["machine"], row["on"]) == ("MIX01", "MIX01", band.parent.name)


def test_the_band_s_repair_is_open_maintenance_on_the_mould_for_a_reading_s_panel(session, band):
    order = _on_the_band(session)
    session.flush()
    mixer = band.parent
    now = utcnow()
    seen = moment.maintenance_orders(session, mixer, now - timedelta(minutes=5), now + timedelta(minutes=5))
    assert [o["code"] for o in seen["orders"]] == [order.code]
    assert trend.open_maintenance_in(session, mixer, now - timedelta(minutes=5),
                                     now + timedelta(minutes=5)) == [order.code]


def test_the_mould_is_down_while_the_band_is_mended_and_the_band_has_no_state_of_its_own(session, band):
    equipment.set_state(session, equipment_code="MIX01", state=EquipmentStateName.DOWN, reason="heater")
    _on_the_band(session)
    session.flush()
    states = {s.equipment.code: s.state for s in equipment.current_states(session)}
    assert states["MIX01"] is EquipmentStateName.DOWN
    assert "MIX01_HeaterZone2" not in states


def test_the_dispatcher_rosters_the_band_s_repair_on_the_mould_s_shift(session, band, monkeypatch):
    order = _on_the_band(session)
    session.flush()
    asked: list[int] = []
    real = calendar.shift_for

    def watching(s, at, equipment_id=None):
        asked.append(equipment_id)
        return real(s, at, equipment_id)

    monkeypatch.setattr(calendar, "shift_for", watching)
    dispatch.explain(session, order.code)
    assert asked and set(asked) == {band.parent.id}


def test_the_scheduler_books_the_band_s_repair_before_the_mould_s_production(session, band):
    from fsmes.services import scheduling

    order = _on_the_band(session)
    session.flush()
    start = utcnow()
    after = scheduling._place_maintenance(session, band.parent.id, start)
    assert after > start, "the band's repair was not booked on the mould"
    assert order.status.value == "due"


# --------------------------------------------------------------------- the ERP

def _mended(session, order):
    maintenance.start(session, order.code, actor="MT-01")
    return maintenance.complete(session, order.code, findings="band replaced", downtime_minutes=42.0,
                                part_code="SP-HEATER-BAND", part_qty=1, actor="MT-01")


def _sent(session):
    [message] = session.scalars(select(ErpMessage).where(ErpMessage.kind == "part_used"))
    return message.payload


def test_the_part_used_message_names_the_band_and_bills_the_mould_s_cost_center(session, band):
    _mended(session, _on_the_band(session))
    payload = _sent(session)
    assert payload["equipment"] == "MIX01"
    assert payload["equipment_module"] == "MIX01_HeaterZone2"
    assert payload["cost_center"] == "CC-MIX01"
    assert payload["work_center"] == masterdata.work_center_of(session, band.parent).code


def test_unoverridden_the_message_is_what_an_order_on_the_mould_sends_but_for_the_band(session, band):
    _mended(session, maintenance.raise_corrective(session, equipment_code="MIX01", summary="Heater out"))
    on_mould = dict(_sent(session))
    session.execute(ErpMessage.__table__.delete())
    _mended(session, _on_the_band(session, summary="Heater out"))
    on_band = dict(_sent(session))
    assert on_mould["equipment_module"] is None and on_band["equipment_module"] == "MIX01_HeaterZone2"
    different = {k for k in on_mould if on_mould[k] != on_band[k]}
    assert different <= {"equipment_module", "repair_order", "message_key", "completed_at"}, different


def test_a_band_given_its_own_cost_center_bills_there_and_erpnext_posts_the_issue_there(session, band):
    from tests.test_a_repair_order_closed_with_a_part_tells_the_erp_once import FakeStores, _erpnext

    assert masterdata.set_cost_center(session, "MIX01_HeaterZone2", "CC-BAND02", actor="ENG")
    _mended(session, _on_the_band(session))
    sent = parse_message(_sent(session))
    assert (sent.cost_center, sent.equipment_module, sent.equipment) == ("CC-BAND02", "MIX01_HeaterZone2", "MIX01")
    fake = FakeStores(cost_centers=(("CC-BAND02 - CW", "Cutlery Works"),))
    _erpnext(fake).send_part_used(sent)
    [entry] = fake.entries
    assert {row["cost_center"] for row in entry["items"]} == {"CC-BAND02 - CW"}
    assert "MIX01_HeaterZone2" in entry["remarks"]


# ------------------------------------------------------------------ Setup

def test_setup_gives_a_part_its_own_cost_center_and_a_place_and_both_are_audited(session, band, admin):
    r = admin.put("/masterdata/equipment/MIX01_HeaterZone2",
                  json={"cost_center": "CC-BAND02", "x": 12.25, "y": 5.6, "z": 1.55})
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(body["changed"]) == ["cost_center", "position"]
    assert (body["cost_center"], body["bills_to"]) == ("CC-BAND02", "CC-BAND02")
    assert (body["x"], body["y"], body["z"]) == (12.25, 5.6, 1.55)
    actions = set(session.scalars(select(AuditLog.action).where(AuditLog.entity_id == "MIX01_HeaterZone2")))
    assert {"equipment.cost_center", "equipment.placed"} <= actions
    cleared = admin.put("/masterdata/equipment/MIX01_HeaterZone2", json={"cost_center": ""}).json()
    assert (cleared["cost_center"], cleared["bills_to"]) == (None, "CC-MIX01")
    assert cleared["changed"] == ["cost_center"]


def test_setup_refuses_half_a_place_and_a_change_from_someone_who_may_not_write_master_data(
        session, band, admin, client):
    half = admin.put("/masterdata/equipment/MIX01_HeaterZone2", json={"x": 1.0})
    assert half.status_code in (400, 422) and "x and y" in half.text
    assert client.put("/masterdata/equipment/MIX01_HeaterZone2", json={"cost_center": "X"}).status_code == 403
    assert masterdata.get_equipment(session, "MIX01_HeaterZone2").cost_center is None


def test_a_cost_center_longer_than_a_code_is_refused(session, band):
    with pytest.raises(Invalid, match="40"):
        masterdata.set_cost_center(session, "MIX01_HeaterZone2", "C" * 41, actor="ENG")
