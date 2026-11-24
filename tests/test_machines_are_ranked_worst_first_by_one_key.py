"""Machines are ranked worst OEE first by one key.

Structure report 2026-11-18, clean-up C1: "worst-first sort twice". The Floor
grid's "sort by OEE" and the Analysis screen's plant-wide list each wrote
their own sort key; the next rule about ranking (say, where a machine with no
figure goes) would have had to be written twice and would have been written
once. Both sort by `services/analysis.worst_oee_first` now, and no router
writes a ranking of its own.
"""

import re
from pathlib import Path

from fsmes.services.analysis import worst_oee_first

ROUTERS = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "api" / "routers"


def test_no_router_writes_its_own_worst_first_key():
    ranking = [path.name for path in sorted(ROUTERS.glob("*.py"))
               if re.search(r'\["oee"\]\s+is None', path.read_text(encoding="utf-8"))]
    assert ranking == [], f"{ranking} rank machines themselves; sort by analysis.worst_oee_first"


def test_unknown_is_not_ranked_as_a_zero():
    ranked = sorted([("M-NONE", None), ("M-ZERO", 0.0), ("M-HIGH", 0.9), ("M-LOW", 0.2)],
                    key=lambda m: worst_oee_first(m[1], m[0]))
    assert [code for code, _ in ranked] == ["M-ZERO", "M-LOW", "M-HIGH", "M-NONE"]


def test_the_floor_grid_ranks_by_the_one_key(client):
    machines = client.get("/dashboard/summary", params={"machine_sort": "oee"}).json()["machines"]
    assert len(machines) > 1, "the test plant has machines to rank"
    assert [m["code"] for m in machines] == [
        m["code"] for m in sorted(machines, key=lambda m: worst_oee_first(m["oee"]["oee"], m["code"]))]
