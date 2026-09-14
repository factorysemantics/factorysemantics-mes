"""Which order the line said it was running, and how far past it the line ran.

For five runs every report in the lab carried the same sentence under *what
the runs could not answer*: which order a unit belonged to was the MES's own
inference, and the script's order numbers could not be matched to it. Seven
times, in five runs, about a line that had been publishing its order number
the whole time on a tag nobody read.

So the tag map gained a `line` block - where the line's own tags are, which
one carries the order it is running, and how the value it publishes names an
order in this MES. That last part is a plant-boundary translation (a PLC
publishes `4711`; this MES holds `WO-ACME-4711`), which is why it is config
and not code.

With the join read rather than guessed, over-production becomes scoreable per
order, which is the class of fault the lab was built for: sixteen units booked
against an order for fifteen.

Nothing here starts a plant.
"""

import json

import pytest

from fsmes.integrations.opc.tag_map import LineMap, read_line_map
from fsmes.lab import measure
from fsmes.lab import truth as lab_truth
from fsmes.sim.generate import generate

LINE = {
    "channel": "Tiny",
    "seed": 3,
    "duration_s": 120,
    "orders": [900, 901],
    "buffers": {"capacity": 10, "initial": 5},
    "stations": [
        {"name": "Cut", "rate_per_min": 60, "scrap_pct": 0.0},
        {"name": "Pack", "rate_per_min": 60, "scrap_pct": 0.0},
    ],
    # The changeover is what moves the line from order 900 to order 901.
    "events": [{"type": "changeover", "start": 60, "end": 70}],
}

MAPPING = {"Cut": "CUT01", "Pack": "PACK01"}
TIED = LineMap(object="Line", publishes_order="OrderId", order_code="WO-{value}")


@pytest.fixture
def truth(tmp_path) -> lab_truth.LineTruth:
    line = tmp_path / "line.json"
    line.write_text(json.dumps(LINE), encoding="utf-8")
    out = tmp_path / "out"
    generate(line, out, write_docs=False)
    return lab_truth.read(out, LINE, 120, overlap_s=10)


def _oee(truth) -> dict:
    return {"stations": [
        {"code": MAPPING[name], "good_qty": station.good, "scrap_qty": station.scrap,
         "availability": 0.5, "performance": 0.5, "quality": 1.0, "oee": 0.25,
         "runtime_seconds": station.running_seconds,
         "downtime_seconds": station.down_seconds,
         "ideal_cycle_seconds": station.ideal_cycle_seconds}
        for name, station in truth.stations.items()],
        "window": {"hours": 120 / 3600}, "line_oee": 0.25, "constraint": "CUT01"}


def _mes(truth, order="900", quantity=10, good=None, over=None) -> dict:
    """The MES's work order list, with one order the line published."""
    made = truth.good_by_order.get(order, 0) if good is None else good
    return {"items": [{"code": f"WO-{order}", "quantity": quantity, "good_qty": made,
                       "scrap_qty": 0,
                       "over_qty": max(0, made - quantity) if over is None else over}]}


def _rows(truth, mes, line_map=TIED) -> dict:
    out = measure.booking(truth, _oee(truth), mes, MAPPING, speed=10.0, line_map=line_map)
    return {row["order"]: row for row in out["orders"]["rows"]}


# ------------------------------------------------------- the tag map's block

def test_the_line_block_turns_what_the_line_published_into_this_mes_s_own_code():
    block = read_line_map({"line": {"object": "Line", "publishes_order": "OrderId",
                                    "order_code": "WO-ACME-{value}"}})
    assert block.code_for(4711) == "WO-ACME-4711"
    assert block.code_for("4711") == "WO-ACME-4711"


def test_a_line_that_publishes_the_code_outright_needs_no_translation():
    block = read_line_map({"line": {"object": "Line", "publishes_order": "Order",
                                    "order_code": "{value}"}})
    assert block.code_for("WO-ACME-4711") == "WO-ACME-4711"


def test_a_map_with_no_line_block_says_nothing_rather_than_guessing():
    assert read_line_map({"machines": []}) is None


def test_a_line_that_publishes_an_order_with_no_rule_for_reading_it_is_refused():
    """Reading a tag and being unable to do anything with it is the silent
    half-configuration this block exists to end."""
    with pytest.raises(ValueError) as raised:
        read_line_map({"line": {"object": "Line", "publishes_order": "OrderId"}})
    assert "order_code" in str(raised.value)


def test_an_order_code_with_no_value_in_it_is_refused_as_a_constant():
    with pytest.raises(ValueError) as raised:
        read_line_map({"line": {"object": "Line", "publishes_order": "OrderId",
                                "order_code": "WO-ACME-4711"}})
    assert "{value}" in str(raised.value)


def test_a_line_block_with_no_object_is_refused():
    with pytest.raises(ValueError):
        read_line_map({"line": {"publishes_order": "OrderId", "order_code": "{value}"}})


def test_the_packs_this_lab_runs_all_say_where_their_line_publishes_its_order(tmp_path):
    """The three maps the starters use. A map that lost the block would put
    every run back to reporting the same unknown seven times."""
    from pathlib import Path

    from fsmes.integrations.opc.tag_map import load_line_map

    root = Path(__file__).resolve().parents[1]
    for name, expected in (("labs/multiplant/bottling/tag_map.json", "WO-ACME-4711"),
                           ("config/tag_map_kepsim.json", "WO-ACME-4711"),
                           ("labs/multiplant/machining/tag_map.json", "WO-NG-4711")):
        block = load_line_map(root / name)
        assert block is not None, f"{name} has no line block"
        assert block.publishes_order == "OrderId"
        assert block.code_for(4711) == expected


# --------------------------------------------------------- the measurement

def test_the_order_the_line_published_is_matched_to_the_order_the_mes_holds(truth):
    out = measure.booking(truth, _oee(truth), _mes(truth), MAPPING, speed=10.0, line_map=TIED)
    orders = out["orders"]
    assert orders["tied_to_truth"] is True
    assert orders["why"] is None
    assert "Line.OrderId" in orders["tied_how"]
    row = _rows(truth, _mes(truth))["900"]
    assert row["code"] == "WO-900"
    assert row["truth_good"] == truth.good_by_order["900"]


def test_the_ordered_quantity_is_the_mes_s_own_and_is_never_copied_from_the_script(truth):
    """One number, one place. A quantity in the line description too would be
    a second place for it to be wrong, and a difference between the two would
    look like a fault in a plant that had none."""
    row = _rows(truth, _mes(truth, quantity=7))["900"]
    assert row["mes_quantity"] == 7


def test_how_far_past_the_order_the_line_ran_is_a_range_and_the_mes_is_read_against_it(truth):
    made = truth.good_by_order["900"]
    overlap = truth.overlap_good_by_order.get("900", 0)
    row = _rows(truth, _mes(truth, quantity=5))["900"]
    assert row["truth_over_run"] == made - 5
    assert row["truth_over_run_range"] == [made - 5, made + overlap - 5]
    assert row["over_run_verdict"] in ("matched", "inside the replay's overlap band")


def test_an_over_run_bigger_than_the_line_made_and_past_the_overlap_is_named(truth):
    """The sixteen-against-fifteen class, seen from the order rather than the
    station. The overlap can only ever add, so anything past it is the MES's
    own."""
    made = truth.good_by_order["900"]
    overlap = truth.overlap_good_by_order.get("900", 0)
    row = _rows(truth, _mes(truth, quantity=5, over=made + overlap - 5 + 9))["900"]
    assert "9 more past the order" in row["over_run_verdict"]


def test_an_over_run_smaller_than_the_line_made_is_named_too(truth):
    made = truth.good_by_order["900"]
    row = _rows(truth, _mes(truth, quantity=5, over=made - 5 - 4))["900"]
    assert "4 fewer past the order" in row["over_run_verdict"]


def test_an_order_the_line_published_that_the_mes_never_held_is_unknown_not_a_fault(truth):
    """The script runs two orders; the MES was told about one. That is a fact
    about how the plant was seeded, not a difference in its numbers."""
    rows = _rows(truth, _mes(truth, order="900"))
    assert rows["901"]["unknown_because"] is not None
    assert "holds no order with that code" in rows["901"]["unknown_because"]
    assert rows["901"]["verdict"].startswith("unknown")
    assert rows["901"]["over_run_verdict"].startswith("unknown")


def test_an_order_the_mes_holds_that_the_line_never_published_is_listed_and_not_a_difference(truth):
    mes = _mes(truth)
    mes["items"].append({"code": "WO-SOMETHING-ELSE", "quantity": 50, "good_qty": 0,
                         "scrap_qty": 0, "over_qty": 0})
    out = measure.booking(truth, _oee(truth), mes, MAPPING, speed=10.0, line_map=TIED)
    assert out["orders"]["mes_orders_the_line_never_published"] == ["WO-SOMETHING-ELSE"]
    plant = {"plant": "tiny", "measurements": {"booking": out}}
    assert not [row for row in measure.differences(plant)
                if row["measurement"] == "booking - orders"]


def test_an_over_run_outside_the_band_reaches_the_differences_and_the_roll_up(truth):
    made = truth.good_by_order["900"]
    overlap = truth.overlap_good_by_order.get("900", 0)
    out = measure.booking(truth, _oee(truth),
                          _mes(truth, quantity=5, over=made + overlap - 5 + 9),
                          MAPPING, speed=10.0, line_map=TIED)
    plant = {"plant": "tiny", "measurements": {"booking": out}}
    found = [row for row in measure.differences(plant)
             if row["measurement"] == "booking - orders"]
    assert len(found) == 1
    assert found[0]["what"] == "units past the order"
    assert "WO-900" in found[0]["where"]
    assert found[0]["numbers"]["mes_over_run"] == made + overlap - 5 + 9


def test_an_order_nobody_could_match_reaches_the_unknowns_rather_than_the_differences(truth):
    out = measure.booking(truth, _oee(truth), _mes(truth), MAPPING, speed=10.0, line_map=TIED)
    plant = {"plant": "tiny", "measurements": {"booking": out}}
    reasons = [row["because"] for row in measure.unknowns(plant)
               if row["measurement"] == "booking · orders"]
    assert any("holds no order with that code" in reason for reason in reasons)
    assert not [row for row in measure.differences(plant)
                if row["measurement"] == "booking - orders"]


def test_a_run_that_tied_one_order_does_not_print_the_whole_measurement_as_unknown(truth):
    """The order the MES never held is unknown on its own row. Repeating that
    as the reason the whole measurement is unknown would bury the order that
    did match."""
    out = measure.booking(truth, _oee(truth), _mes(truth), MAPPING, speed=10.0, line_map=TIED)
    assert out["orders"]["why"] is None
    assert out["orders"]["orders_tied"] == 1
    assert out["orders"]["truth_orders_total"] == 2


# --------------------------------------------- where the rest of it went

def test_production_no_order_took_is_printed_beside_the_orders(truth):
    """A report that says the MES booked two thousand fewer units than the
    line made, and cannot say whether they were dropped or kept, has named
    two very different faults and told them apart for neither."""
    out = measure.booking(truth, _oee(truth), _mes(truth), MAPPING, speed=10.0,
                          line_map=TIED,
                          unassigned={"good_total": 2103.0, "scrap_total": 4.0, "total": 17})
    said = out["orders"]["unassigned_production"]
    assert said["good"] == 2103.0
    assert said["entries"] == 17
    assert said["unknown_because"] is None


def test_a_run_that_never_asked_where_it_went_says_so_rather_than_nothing(truth):
    out = measure.booking(truth, _oee(truth), _mes(truth), MAPPING, speed=10.0, line_map=TIED)
    said = out["orders"]["unassigned_production"]
    assert said["good"] is None
    assert "did not ask" in said["unknown_because"]
