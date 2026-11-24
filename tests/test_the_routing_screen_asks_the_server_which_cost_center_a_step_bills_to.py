"""The routing screen asks the server which cost center a step bills to.

Structure report 2026-11-18, clean-up C1 ("line lookup, four copies",
`web/admin.js:242`). Setup > Admin > Routings names each step's cost center,
inherited up the tree: a machine with none takes its cell's, a cell with
none takes its line's. The screen worked that out itself, with a second copy
in JavaScript of `services/masterdata.cost_center`. Now the equipment list
carries the answer the server gives (`bills_to`), and the screen reads it,
so the two cannot disagree when the rule changes.
"""

from pathlib import Path

from sqlalchemy import select

from fsmes.domain import Equipment
from fsmes.services import masterdata

ADMIN_JS = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web" / "admin.js"


def test_every_node_says_the_cost_center_the_server_bills_it_to(client, session):
    line = session.scalar(select(Equipment).where(Equipment.code == "LINE1"))
    line.cost_center = "CC-LINE1"
    session.flush()

    listed = {e["code"]: e for e in client.get("/masterdata/equipment").json()}
    assert listed["LINE1"]["bills_to"] == "CC-LINE1"
    beneath = [e for e in listed.values() if e["parent"] == "LINE1"]
    assert beneath, "the test plant has machines on LINE1"
    for e in beneath:
        assert e["cost_center"] is None, "the machine inherits; only the line sets it"
        assert e["bills_to"] == "CC-LINE1", e["code"]
    for eq in session.scalars(select(Equipment)):
        assert listed[eq.code]["bills_to"] == masterdata.cost_center(session, eq), eq.code


def test_the_routing_screen_does_not_walk_the_tree_itself():
    source = ADMIN_JS.read_text(encoding="utf-8")
    assert "bills_to" in source, "admin.js reads the server's answer"
    assert "node.parent" not in source, (
        "admin.js walks up the equipment tree itself; read `bills_to` from "
        "/masterdata/equipment, which is services/masterdata.cost_center")
