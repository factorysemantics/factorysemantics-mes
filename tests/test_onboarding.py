"""The real-plant onboarding path: worksheet -> tag map -> seeded line.

What is guarded here is the honesty of the pipeline. A worksheet is filled in
by a person at a plant, and every allowance it makes (no scrap counter, no
cycle time) must flow through as "unknown" rather than being papered over with
a default that looks like a measurement.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from fsmes.domain import Equipment, EquipmentStateName, Routing
from fsmes.integrations.opc.tag_map import load_tag_map
from fsmes.integrations.opc.worksheet import WorksheetError, worksheet_to_tag_map, write_tag_map
from fsmes.seed_line import seed_line_from_tag_map

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "docs" / "onboarding" / "worksheet-example.csv"
TEMPLATE = REPO / "docs" / "onboarding" / "worksheet-template.csv"


def _sheet(tmp_path: Path, *rows: str) -> Path:
    header = TEMPLATE.read_text(encoding="utf-8").strip()
    path = tmp_path / "worksheet.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


# ----------------------------------------------------------- per-tag node ids


def test_nodes_listing_defines_what_exists():
    """A machine addressed by a `nodes` listing subscribes to exactly what is
    listed: real plants have machines without scrap counters, and one missing
    counter must not turn into a machine that reports nothing."""
    data, _ = worksheet_to_tag_map(EXAMPLE)
    by_code = {m["equipment"]: m for m in data["machines"]}

    depal = by_code["DEP01"]
    assert "ScrapCount" not in depal["nodes"]  # the worksheet left it blank
    assert set(depal["nodes"]) == {"State", "GoodCount", "HydraulicPressure"}

    labeller = by_code["LAB01"]
    assert set(labeller["nodes"]) == {"State", "GoodCount"}  # no analog either
    assert "cycle_seconds" not in labeller  # blank stays blank — no invented rate


def test_every_worksheet_machine_is_read_only():
    data, _ = worksheet_to_tag_map(EXAMPLE)
    assert all(m["order_tag"] is None for m in data["machines"])


def test_nodes_win_over_the_template_per_tag():
    """A mostly-uniform machine with one odd tag: the template covers the
    rest, the explicit entry wins for the exception."""
    from fsmes.integrations.opc.tag_map import MachineMap

    spec = MachineMap(
        equipment="FIL01",
        object="FIL01",
        cycle_seconds=1.0,
        node_id="ns=2;s=Line1.Filler.{tag}",
        nodes={"GoodCount": "ns=2;s=Line1.Filler.ProdCount"},
    )
    assert spec.node("GoodCount") == "ns=2;s=Line1.Filler.ProdCount"
    assert spec.node("State") == "ns=2;s=Line1.Filler.State"
    assert spec.by_node_id
    assert spec.tags == ("State", "GoodCount", "ScrapCount", "Temperature")


def test_a_machine_subscribing_to_nothing_is_refused(tmp_path):
    path = tmp_path / "map.json"
    path.write_text(
        json.dumps({"machines": [{"equipment": "X1", "nodes": {"NotATag": "ns=2;s=x"}}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="subscribes to nothing"):
        load_tag_map(path)


# ------------------------------------------------------------- the worksheet


def test_worksheet_round_trips_through_the_real_loader(tmp_path):
    """What make-tag-map writes is only correct if the agent's own loader
    accepts it — so the writer refuses to produce anything the loader would
    reject, and this test walks the full path."""
    out = tmp_path / "tag_map.json"
    warnings = write_tag_map(EXAMPLE, out)
    machines = {m.equipment: m for m in load_tag_map(out)}

    assert list(machines) == ["DEP01", "FIL01", "CAP01", "LAB01", "PCK01"]  # worksheet order
    filler = machines["FIL01"]
    assert filler.name == "Filler"
    assert filler.analog == "FillTemp"
    assert filler.tags == ("State", "GoodCount", "ScrapCount", "FillTemp")
    assert filler.to_state(2) is EquipmentStateName.IDLE  # starved = idle, per the sheet
    assert machines["CAP01"].to_state(9) is EquipmentStateName.DOWN
    assert machines["DEP01"].tags == ("State", "GoodCount", "HydraulicPressure")

    # The blanks came through as warnings, not silently.
    assert any("LAB01" in w and "cycle" in w for w in warnings)
    assert any("DEP01" in w and "scrap" in w for w in warnings)


def test_an_unknown_mes_state_is_refused_with_the_row(tmp_path):
    sheet = _sheet(tmp_path, "M1,Mach,ns=2;s=a.State,ns=2;s=a.Good,,,,0=idle; 1=producing,1.0,")
    with pytest.raises(WorksheetError, match=r"row 2.*'producing'.*not an MES state"):
        worksheet_to_tag_map(sheet)


def test_a_nameless_analog_is_refused(tmp_path):
    sheet = _sheet(tmp_path, "M1,Mach,ns=2;s=a.State,,,ns=2;s=a.Temp,,1=running,1.0,")
    with pytest.raises(WorksheetError, match="analog_name is blank"):
        worksheet_to_tag_map(sheet)


def test_a_duplicate_machine_is_refused(tmp_path):
    sheet = _sheet(
        tmp_path,
        "M1,Mach,ns=2;s=a.State,,,,,1=running,1.0,",
        "M1,Again,ns=2;s=b.State,,,,,1=running,1.0,",
    )
    with pytest.raises(WorksheetError, match="appears twice"):
        worksheet_to_tag_map(sheet)


def test_a_bad_cycle_time_is_refused_not_defaulted(tmp_path):
    sheet = _sheet(tmp_path, "M1,Mach,ns=2;s=a.State,,,,,1=running,fast,")
    with pytest.raises(WorksheetError, match="'fast' is not a number"):
        worksheet_to_tag_map(sheet)


# ---------------------------------------------------------------- seed-line


@pytest.fixture()
def plant_map(tmp_path) -> Path:
    out = tmp_path / "tag_map_plant.json"
    write_tag_map(EXAMPLE, out)
    return out


def test_seed_line_creates_exactly_what_the_map_describes(session, plant_map):
    assert seed_line_from_tag_map(session, plant_map, line_code="L9", line_name="Line Nine") is True
    session.flush()

    line = session.scalar(select(Equipment).where(Equipment.code == "L9"))
    stations = sorted(line.children, key=lambda e: e.id)
    assert [s.code for s in stations] == ["DEP01", "FIL01", "CAP01", "LAB01", "PCK01"]
    assert stations[0].name == "Depalletizer"

    # Cycle time only where the worksheet had one — an invented rate would
    # quietly turn OEE performance into fiction for that machine.
    by_code = {s.code: s for s in stations}
    assert by_code["FIL01"].ideal_cycle_seconds == 0.6
    assert by_code["LAB01"].ideal_cycle_seconds is None

    routing = session.scalar(select(Routing).where(Routing.code == "RT-L9"))
    assert [op.equipment.code for op in routing.operations] == [s.code for s in stations]


def test_seed_line_is_idempotent_and_can_skip_the_routing(session, plant_map):
    assert seed_line_from_tag_map(session, plant_map, line_code="L8", with_routing=False) is True
    session.flush()
    assert seed_line_from_tag_map(session, plant_map, line_code="L8") is False
    assert session.scalar(select(Routing).where(Routing.code == "RT-L8")) is None


def test_seed_line_reuses_an_existing_hierarchy(session, plant_map):
    """The demo plant is already seeded by the fixture; a new line joins it
    rather than duplicating enterprise/site/area rows."""
    seed_line_from_tag_map(
        session, plant_map, line_code="L7", enterprise_code="ACME", site_code="KC1", area_code="PKG"
    )
    session.flush()
    assert session.scalar(select(Equipment).where(Equipment.code == "L7")).parent.code == "PKG"
    codes = [c for (c,) in session.execute(select(Equipment.code).where(Equipment.code == "ACME"))]
    assert codes == ["ACME"]  # still exactly one
