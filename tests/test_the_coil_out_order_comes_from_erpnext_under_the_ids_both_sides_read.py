"""The coil-out scene with its fork order dropped by ERPNext, from one set of ids.

`labs/cutlery/scenes/coil-out-erp.json` extends coil-out and says two things
more: the pack's own FORK1 orders are left out of the copy, so the MES has
its FORK1 order only because ERPNext submitted it, and its `erp` section is
the one file of ids that `labs/erpnext/seed_cutlery.py`, the film's two
takes and the answer key all read. These tests hold the four to one another
without an ERPNext; what ERPNext does with the order is read back by hand,
as the handoff's Result says, because it needs a running ERPNext.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CUTLERY = ROOT / "labs" / "cutlery"
SCENES = CUTLERY / "scenes"
SCENE = SCENES / "coil-out-erp.json"
FILM = ROOT / "scripts" / "scenes" / "film"


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def builder():
    """`labs/cutlery/scene.py`, imported by path: a lab tool, not a package."""
    return _module("cutlery_scene", CUTLERY / "scene.py")


def ids() -> dict:
    return json.loads(SCENE.read_text(encoding="utf-8"))["erp"]


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    return builder().build(SCENE, tmp_path_factory.mktemp("coil-erp") / "coil-out-erp")


def _orders(folder: Path) -> list[dict]:
    return json.loads((folder / "masterdata" / "work_orders.json").read_text(encoding="utf-8"))


def test_the_erp_scene_is_coil_out_with_only_its_name_its_left_out_orders_and_its_ids_added():
    base = json.loads((SCENES / "coil-out.json").read_text(encoding="utf-8"))
    scene = builder().load_scene(SCENE)
    assert set(scene) - set(base) == {"masterdata_left_out", "erp"}
    assert scene["name"] == "coil-out-erp"
    for key in set(base) - {"name", "_what"}:
        assert scene[key] == base[key], key


def test_the_built_copy_has_no_fork1_order_of_the_packs_and_every_other_line_keeps_its_own(built):
    pack = {o["code"] for o in _orders(CUTLERY)}
    copy = {o["code"] for o in _orders(built)}
    assert pack - copy == {"WO-FORK1-0001", "WO-FORK1-0002"}
    assert copy == pack - {"WO-FORK1-0001", "WO-FORK1-0002"}
    assert ids()["work_order"] not in copy


def test_a_scene_that_leaves_out_an_order_the_pack_does_not_have_is_refused(tmp_path):
    (tmp_path / "work_orders.json").write_text(json.dumps([{"code": "WO-A"}]), encoding="utf-8")
    with pytest.raises(SystemExit, match="WO-TYPO"):
        builder().leave_out_masterdata(tmp_path, {"masterdata_left_out": {"work_orders.json": ["WO-TYPO"]}})
    assert json.loads((tmp_path / "work_orders.json").read_text(encoding="utf-8")) == [{"code": "WO-A"}]


def test_a_scene_extends_one_level_and_no_further(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({"name": "a", "speed": 10}), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps({"extends": "a.json", "name": "b"}), encoding="utf-8")
    (tmp_path / "c.json").write_text(json.dumps({"extends": "b.json", "name": "c"}), encoding="utf-8")
    assert builder().load_scene(tmp_path / "b.json") == {"name": "b", "speed": 10}
    with pytest.raises(SystemExit, match="one level"):
        builder().load_scene(tmp_path / "c.json")


def test_the_ids_name_the_packs_own_line_machines_and_cost_center():
    erp = ids()
    equipment = {e["code"]: e for e in json.loads(
        (CUTLERY / "masterdata" / "equipment.json").read_text(encoding="utf-8"))}
    line = equipment[erp["work_center"]]
    assert line["cost_center"] == erp["cost_center"]
    for machine in erp["machines"]:
        assert machine in equipment
    routing = next(r for r in json.loads((CUTLERY / "masterdata" / "routings.json").read_text(encoding="utf-8"))
                   if r["code"] == "RT-FORK1")
    assert [op["equipment"] for op in routing["operations"]] == erp["machines"]
    assert [op["name"] for op in routing["operations"]] == erp["operations"]
    assert routing["material"] == erp["item"]


def test_the_prices_are_the_answer_keys_and_the_order_is_its_line_and_machine(built):
    erp = ids()
    key = json.loads((built / "answer-key.json").read_text(encoding="utf-8"))
    happened, erp_half = key["what_happened"], key["what_the_erp_should_show"]
    assert erp["spare"]["price"] == erp_half["part"]["price"] == 140.0
    assert erp["spare"]["on_hand"] == erp_half["part"]["quantity"] == 1
    assert erp["material"]["price"] == erp_half["scrap"]["material_price_per_kg"]
    assert erp["material"]["per_unit"] * 1000 == pytest.approx(erp_half["scrap"]["grams_each"])
    assert erp["currency"] == erp_half["currency"]
    assert (erp["work_center"], erp["cost_center"]) == (happened["work_center"], happened["cost_center"])
    assert happened["machine"] in erp["machines"]


def test_the_order_is_finished_after_the_mould_restarts_and_not_before_it_stops(built):
    """The floor finishes an order when its last step has counted its quantity.

    Too small and the order is done before the fault, so the film's order
    never sees it; too large and it is never done in the hour, so ERPNext
    gets no Manufacture entry to look at.
    """
    erp = ids()
    key = json.loads((built / "answer-key.json").read_text(encoding="utf-8"))["what_happened"]
    last = erp["machines"][-1]
    with (built / "out" / f"{last}.csv").open(encoding="utf-8") as handle:
        good = [int(row["GoodCount"]) for row in csv.DictReader(handle)]
    assert good[int(key["minute_stopped"] * 60)] < erp["quantity"]
    assert good[int(key["minute_restarted"] * 60)] < erp["quantity"] <= good[-1]
    # And within ERPNext's allowance however far past it the floor finishes.
    over = good[-1] - erp["quantity"]
    assert over <= erp["quantity"] * erp["overproduction_percent"] / 100


def test_the_seed_reads_the_scenes_ids_and_names_the_cost_center_as_erpnext_does():
    seed = _module("seed_cutlery", ROOT / "labs" / "erpnext" / "seed_cutlery.py")
    assert seed.SCENE == SCENE
    assert seed.ids() == ids()
    assert seed.cost_center_name(ids()) == "CC-MOULD01 - CW"
    # The series gives numbers of the scene's shape: WO-FORK1- and four digits.
    series = ids()["naming_series"]
    number = ids()["work_order"].removeprefix(series[:-5])
    assert series.endswith(".####") and len(number) == 4 and number.isdigit()


@pytest.mark.parametrize("take", ["coil-out-mes.toml", "coil-out-erp.toml"])
def test_both_takes_film_the_same_order_into_the_coil_out_folder_with_the_corner_clear(take):
    scene = tomllib.loads((FILM / take).read_text(encoding="utf-8"))
    text = (FILM / take).read_text(encoding="utf-8")
    assert ids()["work_order"] in text
    assert scene["out"].startswith("~/Work/gifs/film/coil-out/")
    assert (scene["width"], scene["height"]) == (1280, 720)
    assert not scene.get("allow_writes")


@pytest.mark.parametrize(("method", "path", "writes"), [
    ("GET", "/api/resource/Work Order/WO-FORK1-0001", False),
    ("POST", "/api/method/frappe.desk.reportview.get", False),
    ("POST", "/api/method/frappe.desk.form.load.getdoc", False),
    ("POST", "/api/method/login", False),
    ("POST", "/api/method/frappe.desk.form.save.savedocs", True),
    ("POST", "/api/method/frappe.client.set_value", True),
    ("POST", "/api/method/frappe.client.cancel", True),
    ("POST", "/api/resource/Stock Entry", True),
    ("PUT", "/api/resource/Work Order/WO-FORK1-0001", True),
    ("DELETE", "/api/resource/Work Order/WO-FORK1-0001", True),
])
def test_the_erp_take_calls_a_desk_request_a_write_by_what_it_would_change(method, path, writes):
    take = _module("record_erp_take", ROOT / "scripts" / "record-erp-take.py")
    assert take.is_a_write(method, "http://mes.localhost:8085" + path) is writes
