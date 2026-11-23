"""The line a machine is on is looked up in one place.

Structure report 2026-11-18, clean-up C1: "line lookup, four copies". The
Floor grid's summary walked up the equipment tree itself in the dashboard
router - a second copy of `services/masterdata.work_center_of`, the one
`CLAUDE.md` names, and one without its guard against a loop in the tree.
A router parses and calls; the walk is the service's. So no router compares
a node's level with a work centre's, and the Floor grid says the same line
for a machine as `work_center_of` does, however deep the machine sits.
"""

import re
from pathlib import Path

from fsmes.services import masterdata

ROUTERS = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "api" / "routers"


def test_no_router_walks_up_the_equipment_tree_to_find_a_line():
    walking = [path.name for path in sorted(ROUTERS.glob("*.py"))
               if re.search(r"EquipmentLevel\.WORK_CENTER", path.read_text(encoding="utf-8"))]
    assert walking == [], (
        f"{walking} find a machine's line themselves; call "
        "services/masterdata.work_center_of")


def test_the_floor_grid_says_the_line_work_center_of_says(client, session):
    machines = client.get("/dashboard/summary").json()["machines"]
    assert machines, "the test plant has machines on the floor"
    for machine in machines:
        centre = masterdata.work_center_of(session, masterdata.get_equipment(session, machine["code"]))
        expected = {"code": centre.code, "name": centre.name} if centre else None
        assert machine["line"] == expected, machine["code"]
