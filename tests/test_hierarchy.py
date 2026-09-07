"""A plant deeper than two levels.

The hierarchy has always been arbitrarily deep in the schema and exactly two
levels deep in practice: every reader did one `parent_id ==` hop. That is
fine for line→machine and loses every machine the day a plant is modelled
line→cell→machine, which is how six lines with cells are actually laid out.

These build that plant and assert the MES can still see it.
"""

import pytest

from fsmes.domain import Equipment, EquipmentLevel
from fsmes.services import analysis, masterdata
from fsmes.services import line as line_service


@pytest.fixture()
def deep_plant(session):
    """site → line → cell → machine, plus one machine hung straight off the
    line, because real plants are never uniform."""
    site = Equipment(code="SITE1", name="Kansas City", level=EquipmentLevel.SITE)
    session.add(site)
    session.flush()

    line = Equipment(code="LINE-A", name="Line A", level=EquipmentLevel.WORK_CENTER,
                     parent_id=site.id, cost_center="CC-100")
    session.add(line)
    session.flush()

    cell = Equipment(code="CELL-A1", name="Cell A1", level=EquipmentLevel.AREA,
                     parent_id=line.id)
    deep_cell = Equipment(code="CELL-A2", name="Cell A2", level=EquipmentLevel.AREA,
                          parent_id=line.id, cost_center="CC-200")
    session.add_all([cell, deep_cell])
    session.flush()

    machines = [
        Equipment(code="M-DEEP1", name="Deep 1", level=EquipmentLevel.WORK_UNIT,
                  parent_id=cell.id, ideal_cycle_seconds=4.0),
        Equipment(code="M-DEEP2", name="Deep 2", level=EquipmentLevel.WORK_UNIT,
                  parent_id=deep_cell.id, ideal_cycle_seconds=4.0),
        Equipment(code="M-FLAT", name="Flat", level=EquipmentLevel.WORK_UNIT,
                  parent_id=line.id, ideal_cycle_seconds=4.0),
    ]
    session.add_all(machines)
    session.flush()
    return line


# ------------------------------------------------------------- traversal

def test_machines_under_cells_are_still_the_lines_machines(session, deep_plant):
    """The bug this phase exists to fix: a single hop finds one of three."""
    found = {eq.code for eq in masterdata.work_units_under(session, deep_plant)}
    assert found == {"M-DEEP1", "M-DEEP2", "M-FLAT"}


def test_descendants_returns_the_whole_subtree_not_only_machines(session, deep_plant):
    found = {eq.code for eq in masterdata.descendants(session, deep_plant)}
    assert found == {"CELL-A1", "CELL-A2", "M-DEEP1", "M-DEEP2", "M-FLAT"}


def test_a_machine_has_no_descendants(session, deep_plant):
    machine = masterdata.get_equipment(session, "M-FLAT")
    assert masterdata.descendants(session, machine) == []


def test_a_cycle_cannot_hang_the_plant(session, deep_plant):
    """Bad master data must not be a denial of service."""
    cell = masterdata.get_equipment(session, "CELL-A1")
    cell.parent_id = masterdata.get_equipment(session, "M-DEEP1").id
    session.flush()

    masterdata.descendants(session, deep_plant)   # must terminate


# ----------------------------------------------------------- cost centers

def test_a_machine_inherits_its_lines_cost_center(session, deep_plant):
    """A plant sets it once per line; nothing repeats it per machine."""
    machine = masterdata.get_equipment(session, "M-DEEP1")
    assert machine.cost_center is None
    assert masterdata.cost_center(session, machine) == "CC-100"


def test_a_cell_can_be_accounted_for_separately(session, deep_plant):
    """The nearest ancestor with one wins, so an override is local."""
    machine = masterdata.get_equipment(session, "M-DEEP2")
    assert masterdata.cost_center(session, machine) == "CC-200"


def test_a_machine_outside_any_costed_line_reports_unknown(session):
    """Not a made-up default. Principle 4 on the accounting side: an
    uncosted machine is uncosted, and an ERP must not be told otherwise."""
    orphan = Equipment(code="ORPHAN", name="Orphan", level=EquipmentLevel.WORK_UNIT)
    session.add(orphan)
    session.flush()
    assert masterdata.cost_center(session, orphan) is None


def test_the_mes_holds_no_money(session, deep_plant):
    """Cost centers and quantities, never valuation. An MES that computes
    money will disagree with Finance, and Finance wins."""
    columns = set(Equipment.__table__.columns.keys())
    for forbidden in ("cost", "rate", "price", "currency", "value", "amount"):
        assert not any(forbidden in c and c != "cost_center" for c in columns), (
            f"a monetary-looking column {forbidden!r} appeared on Equipment")


# --------------------------------------------------------------- the views

def test_the_line_view_finds_a_line_whose_machines_hang_off_cells(session, deep_plant):
    centres = line_service._work_centres(session)
    codes = {centre.code: {u.code for u in units} for centre, units in centres}
    assert codes["LINE-A"] == {"M-DEEP1", "M-DEEP2", "M-FLAT"}


def test_the_analysis_screen_finds_them_too(session, deep_plant):
    centre, units = analysis._line_and_units(session, "LINE-A")
    assert centre.code == "LINE-A"
    assert {u.code for u in units} == {"M-DEEP1", "M-DEEP2", "M-FLAT"}


def test_the_floor_screen_can_be_scoped_to_one_line(session, deep_plant):
    """Six lines means five walls of machines you are not running."""
    from fsmes.api.routers import dashboard

    everything = {eq.code for eq in dashboard._machines(session, None)}
    just_a = {eq.code for eq in dashboard._machines(session, "LINE-A")}

    assert just_a == {"M-DEEP1", "M-DEEP2", "M-FLAT"}
    assert just_a <= everything


def test_a_deep_line_still_appears_in_the_picker(session, deep_plant):
    """Counted one hop down, a line whose machines hang off cells has zero
    stations and vanishes from the list — leaving no way to reach the line
    the screen cannot see."""
    picker = {entry["code"]: entry["stations"] for entry in analysis.lines(session)}
    assert picker["LINE-A"] == 3


# ------------------------------------------------------------- group tags

def test_a_tag_may_be_bound_to_a_cell_or_a_line(session, deep_plant, tmp_path):
    """Scott's "group notification tags for complex operations": a signal
    that belongs to a cell or a whole line, not to one machine — a batch
    complete, a cell-wide interlock, an area alarm.

    Nothing in the tag map or the equipment lookup requires a work unit, so
    the binding already works; this pins it, because a level check added
    later would silently break it.
    """
    import json

    from fsmes.integrations.opc.tag_map import load_tag_map

    path = tmp_path / "group_map.json"
    path.write_text(json.dumps({"machines": [{
        "equipment": "CELL-A1", "object": "CellA1", "cycle_seconds": 1.0,
        "analog": "BatchComplete", "order_tag": None,
        "state_map": {"0": "idle", "1": "running"},
    }]}), encoding="utf-8")

    [spec] = load_tag_map(path)
    assert spec.equipment == "CELL-A1"

    # And the equipment it names resolves, whatever level it sits at.
    cell = masterdata.get_equipment(session, spec.equipment)
    assert cell.level is EquipmentLevel.AREA
    assert masterdata.cost_center(session, cell) == "CC-100"
