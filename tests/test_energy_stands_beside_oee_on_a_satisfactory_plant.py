"""Energy beside OEE on a Satisfactory plant.

In a job shop the constraint is volume; in Satisfactory it is energy. Each
poll's power figures are written as readings - a building's draw, a
circuit's capacity and peak demand, a generator's output and fuel - and the
energy one part cost is set against what its recipe says it should cost.

OEE does not change: energy is its own figure beside it and never enters it.
"""

from __future__ import annotations

import gzip
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from fsmes.domain.masterdata import Equipment
from fsmes.domain.timeseries import TagValue
from fsmes.integrations.satisfactory import energy, reader
from fsmes.services import analysis
from fsmes.services import energy as energy_service

FIXTURES = Path(__file__).parent / "fixtures" / "satisfactory"
TONIGHT = FIXTURES / "2026-10-30.jsonl.gz"
RECIPES = FIXTURES / "ref" / "getRecipes.json.gz"

T0 = datetime(2026, 10, 30, 12, 0, 0)
IRON_ROD = {"ID": "Recipe_IronRod_C", "Name": "Iron Rod", "ClassName": "Recipe_IronRod_C",
            "Products": [{"Name": "Iron Rod", "ClassName": "Desc_IronRod_C", "Amount": 1,
                          "FactoryRate": 15}], "FactoryDuration": 4}


def _constructor(ident: str = "1", *, mw: float = 4.0, rate: float = 15.0,
                 recipe: str = "Iron Rod") -> dict:
    return {"ID": f"Build_ConstructorMk1_C_{ident}", "Name": "Constructor",
            "ClassName": "Build_ConstructorMk1_C", "Recipe": recipe, "IsConfigured": True,
            "IsProducing": True, "IsPaused": False, "ManuSpeed": 100,
            "PowerInfo": {"CircuitID": 1, "FuseTriggered": False,
                          "PowerConsumed": mw, "MaxPowerConsumed": 4.0},
            "production": [{"Name": recipe, "ClassName": "Desc_IronRod_C", "Amount": 1,
                            "MaxAmount": 200, "CurrentProd": rate, "MaxProd": 15}],
            "ingredients": [{"ClassName": "Desc_IronIngot_C", "Amount": 1}],
            "InputInventory": [{"ClassName": "Desc_IronIngot_C", "Amount": 50, "MaxAmount": 100}],
            "OutputInventory": [{"ClassName": "Desc_IronRod_C", "Amount": 3, "MaxAmount": 200}]}


def _circuit(*, capacity: float = 100.0, peak: float = 56.0, consumed: float = 48.5,
             tripped: bool = False) -> dict:
    return {"CircuitGroupID": 0, "PowerProduction": 0, "PowerConsumed": consumed,
            "PowerCapacity": capacity, "PowerMaxConsumed": peak, "BatteryPercent": 0,
            "BatteryCapacity": 0, "AssociatedCircuits": [1], "FuseTriggered": tripped}


def _poll(ts: datetime, buildings: list[dict], *, power: list[dict] | None = None,
          generators: list[dict] | None = None, paused: bool = False) -> list[dict]:
    stamp = ts.isoformat() + "+00:00"
    lines = [{"ts": stamp, "endpoint": "getFactory", "data": buildings},
             {"ts": stamp, "endpoint": "getPower", "data": power or [_circuit()]},
             {"ts": stamp, "endpoint": "getSessionInfo",
              "data": {"SessionName": "S", "IsPaused": paused}}]
    if generators is not None:
        lines.append({"ts": stamp, "endpoint": "getGenerators", "data": generators})
    return lines


def _write(folder: Path, polls: list[list[dict]], recipes: list[dict] | None = None) -> None:
    with (folder / "2026-10-30.jsonl").open("w", encoding="utf-8") as fh:
        for poll in polls:
            for line in poll:
                fh.write(json.dumps(line) + "\n")
    if recipes is not None:
        (folder / "ref").mkdir(exist_ok=True)
        (folder / "ref" / "getRecipes.json").write_text(json.dumps(recipes), encoding="utf-8")


def _minutes(n: int) -> datetime:
    return T0 + timedelta(minutes=n)


def _building(block: dict, code: str) -> dict:
    return next(b for b in block["buildings"] if b["code"] == code)


# ------------------------------------------------------------------ energy per part


def test_a_building_drawing_4_mw_for_ten_minutes_at_15_parts_a_minute_shows_4_44_kwh_a_part_and_the_ideal_from_the_recipe(
        tmp_path, session, scope):
    _write(tmp_path, [_poll(_minutes(i), [_constructor()]) for i in range(11)],
           recipes=[IRON_ROD])
    reader.replay(tmp_path, scope, now=_minutes(10))
    block = energy_service.breakdown(session, T0, _minutes(10))
    rod = _building(block, "CONSTRUCTORMK1-1")
    # 4 MW x 10 min = 0.6667 MWh; 15 a minute x 10 min = 150 rods.
    assert rod["units"] == 150
    assert rod["mwh"] == pytest.approx(0.6667, abs=1e-4)
    assert rod["kwh_per_unit"] == 4.44
    # The recipe's ideal: 4 MW nominal over 15 a minute = 4000/60/15 kWh.
    assert rod["ideal_kwh_per_unit"] == 4.44
    assert rod["gap_kwh_per_unit"] == 0.0
    assert rod["mw_now"] == 4.0
    assert block["units"] == {"draw": "MW", "energy": "MWh", "per_part": "kWh"}


def test_a_building_fed_half_its_parts_burns_twice_the_ideal_a_part(tmp_path, session, scope):
    _write(tmp_path, [_poll(_minutes(i), [_constructor(rate=7.5)]) for i in range(11)],
           recipes=[IRON_ROD])
    reader.replay(tmp_path, scope, now=_minutes(10))
    rod = _building(energy_service.breakdown(session, T0, _minutes(10)), "CONSTRUCTORMK1-1")
    assert rod["units"] == 75
    assert rod["kwh_per_unit"] == 8.89
    assert rod["gap_kwh_per_unit"] == 4.44


def test_a_recipe_absent_from_the_reference_shows_its_ideal_as_unknown_and_says_why(
        tmp_path, session, scope):
    _write(tmp_path, [_poll(_minutes(i), [_constructor(recipe="Iron Rod")]) for i in range(3)],
           recipes=[])
    reader.replay(tmp_path, scope, now=_minutes(2))
    rod = _building(energy_service.breakdown(session, T0, _minutes(2)), "CONSTRUCTORMK1-1")
    assert rod["ideal_kwh_per_unit"] is None
    assert rod["gap_kwh_per_unit"] is None
    assert "not in the game's recipe reference" in rod["ideal_note"]
    # The energy and the parts are still measured; only the ideal is unknown.
    assert rod["kwh_per_unit"] == 4.44


def test_a_folder_with_no_recipe_reference_at_all_has_every_ideal_unknown(tmp_path, session, scope):
    _write(tmp_path, [_poll(_minutes(i), [_constructor()]) for i in range(3)])
    reader.replay(tmp_path, scope, now=_minutes(2))
    rod = _building(energy_service.breakdown(session, T0, _minutes(2)), "CONSTRUCTORMK1-1")
    assert rod["ideal_kwh_per_unit"] is None
    assert rod["ideal_note"].startswith("unknown")


# ------------------------------------------------------------------ nothing across a gap


def test_a_gap_in_the_polls_books_no_energy(tmp_path, session, scope):
    # Polls at 0 and 1 min, then nothing until 10 and 11: the game was closed.
    _write(tmp_path, [_poll(_minutes(i), [_constructor()]) for i in (0, 1, 10, 11)],
           recipes=[IRON_ROD])
    reader.replay(tmp_path, scope, now=_minutes(11))
    rod = _building(energy_service.breakdown(session, T0, _minutes(11)), "CONSTRUCTORMK1-1")
    # Two observed minutes at 4 MW, not eleven.
    assert rod["mwh"] == pytest.approx(4 * 2 / 60, abs=1e-4)


def test_a_paused_game_books_no_energy_for_the_time_it_was_paused(tmp_path, session, scope):
    polls = [_poll(_minutes(i), [_constructor()], paused=i in (2, 3)) for i in range(6)]
    _write(tmp_path, polls, recipes=[IRON_ROD])
    reader.replay(tmp_path, scope, now=_minutes(5))
    rod = _building(energy_service.breakdown(session, T0, _minutes(5)), "CONSTRUCTORMK1-1")
    # 0-1 and 4-5 only: the poll after the pause opens a new run, it does not
    # close one across it.
    assert rod["mwh"] == pytest.approx(4 * 2 / 60, abs=1e-4)


# ------------------------------------------------------------------ the circuit


def test_a_circuit_whose_peak_demand_exceeds_capacity_reads_tripped_with_the_demand_named(
        tmp_path, session, scope):
    _write(tmp_path, [
        _poll(_minutes(0), [_constructor()], power=[_circuit(capacity=100, peak=56)]),
        _poll(_minutes(1), [_constructor()],
              power=[_circuit(capacity=100, peak=141, consumed=0, tripped=True)]),
    ])
    reader.replay(tmp_path, scope, now=_minutes(1))
    block = energy_service.breakdown(session, T0 - timedelta(seconds=1), _minutes(1))
    (circuit,) = block["circuits"]
    assert circuit["tripped"] is True
    assert circuit["peak_demand_mw"] == 141
    assert circuit["headroom_mw"] == -41
    assert circuit["why"] == "fuse tripped: demand 141 MW over capacity 100 MW"
    assert block["last_trip"]["why"] == circuit["why"]
    assert block["tripped_polls"] == 1


def test_a_circuit_with_headroom_reads_its_capacity_less_its_peak_demand(tmp_path, session, scope):
    _write(tmp_path, [_poll(_minutes(0), [_constructor()],
                            power=[_circuit(capacity=120, peak=112, consumed=48.5)])])
    reader.replay(tmp_path, scope, now=_minutes(0))
    block = energy_service.breakdown(session, T0 - timedelta(seconds=1), _minutes(0))
    assert block["capacity_mw"] == 120
    assert block["consumed_mw"] == 48.5
    assert block["headroom_mw"] == 8
    assert block["tripped"] is False and block["last_trip"] is None
    assert block["circuits_total"] == 1


# ------------------------------------------------------------------ generators


def test_a_generators_fuel_and_output_read_as_readings(tmp_path, session, scope):
    shutil.copy(TONIGHT, tmp_path / TONIGHT.name)
    reader.replay(tmp_path, scope, now=datetime(2026, 10, 30, 10, 24))
    burner = session.scalar(select(Equipment).where(
        Equipment.code == "GENERATORBIOMASS_AUTOMATED-2147157945"))
    assert burner is not None
    assert burner.parent.code == "GENERATORS"
    tags = set(session.scalars(select(TagValue.tag).where(TagValue.equipment_id == burner.id)))
    assert {f"{burner.code}.{t}" for t in (energy.OUTPUT, energy.CAPACITY, energy.FUEL,
                                            energy.FUEL_ITEMS, energy.LOAD)} <= tags
    fuels = set(session.scalars(select(TagValue.value_text).where(
        TagValue.tag == f"{burner.code}.{energy.FUEL}")))
    assert "Biomass" in fuels


def test_a_generator_with_no_fuel_is_idle_and_starved_and_one_making_power_is_running():
    burning = {"RegulatedDemandProd": 17.2, "DynamicProdCapacity": 30, "FuelAmount": 0.79,
               "FuelInventory": [{"Name": "Biomass", "Amount": 200}]}
    empty = {"RegulatedDemandProd": 0, "DynamicProdCapacity": 0, "FuelAmount": 0,
             "FuelInventory": []}
    assert energy.generator(burning).running is True
    assert energy.generator(burning).fuel == "Biomass"
    assert energy.generator(empty).running is False
    assert energy.generator(empty).fuelled is False
    assert energy.generator(empty).fuel == "no fuel"


def test_a_poll_with_no_generator_answer_leaves_the_generators_seen_not_gone(
        tmp_path, session, scope):
    gen = {"ID": "Build_GeneratorBiomass_Automated_C_9", "Name": "Biomass Burner",
           "ClassName": "Build_GeneratorBiomass_Automated_C", "RegulatedDemandProd": 10,
           "DynamicProdCapacity": 30, "FuelAmount": 0.5,
           "FuelInventory": [{"Name": "Biomass", "Amount": 100}],
           "PowerInfo": {"CircuitID": 1, "FuseTriggered": False}}
    _write(tmp_path, [_poll(_minutes(0), [_constructor()], generators=[gen]),
                      _poll(_minutes(1), [_constructor()])])
    result = reader.replay(tmp_path, scope, now=_minutes(1))
    assert "no longer in the game's building list" not in result.report.unseen_reasons


# ------------------------------------------------------------------ the plant's tonight


def test_tonights_polls_replayed_give_every_powered_building_a_draw_and_the_circuit_its_capacity(
        tmp_path, session, scope):
    shutil.copy(TONIGHT, tmp_path / TONIGHT.name)
    (tmp_path / "ref").mkdir()
    shutil.copy(RECIPES, tmp_path / "ref" / RECIPES.name)
    reader.replay(tmp_path, scope, now=datetime(2026, 10, 30, 10, 24))
    block = energy_service.breakdown(session, datetime(2026, 10, 30, 9, 40),
                                     datetime(2026, 10, 30, 10, 24))
    assert block["circuits_total"] == 1
    assert block["capacity_mw"] is not None
    assert block["generators_total"] == 6
    assert block["buildings_total"] == len(block["buildings"]) > 0
    assert block["mwh"] > 0
    known = [b for b in block["buildings"] if b["ideal_kwh_per_unit"] is not None]
    assert known, "the committed recipe reference covers tonight's recipes"


def test_the_fixture_recipe_reference_is_the_games_own_rows_trimmed():
    with gzip.open(RECIPES, "rt", encoding="utf-8") as fh:
        rows = json.load(fh)
    rod = next(r for r in rows if r["Name"] == "Iron Rod")
    assert rod["Products"][0]["FactoryRate"] == 15


# ------------------------------------------------------------------ beside OEE, not in it


def test_a_plant_with_no_power_data_has_no_energy_block_on_its_oee(session):
    payload = analysis.oee_breakdown(session)
    assert "energy" not in payload
    assert analysis.energy(session)["energy"] is None


def test_a_plant_with_no_power_data_answers_energy_null_over_the_api(client):
    response = client.get("/analysis/energy")
    assert response.status_code == 200
    assert response.json()["energy"] is None


def test_energy_rides_beside_oee_and_leaves_quality_at_100(tmp_path, session, scope, monkeypatch):
    now = _minutes(10)
    monkeypatch.setattr("fsmes.services.analysis.utcnow", lambda: now)
    _write(tmp_path, [_poll(_minutes(i), [_constructor()]) for i in range(11)],
           recipes=[IRON_ROD])
    reader.replay(tmp_path, scope, now=now)
    payload = analysis.oee_breakdown(session, line_code="CONSTRUCTORS", hours=1)
    assert "energy" in payload
    (station,) = payload["stations"]
    assert "energy" not in station and "kwh_per_unit" not in station
    assert station["quality"] in (None, 1.0)
