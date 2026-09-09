"""Seed workforce and quality-measurement breadth through the public API —
the one thing init.py (which runs before the API exists, seeding equipment/
materials/routings/one baseline spec per line directly via the database,
following seed.py's precedent) cannot reach. Adding ~35 people and 3 more
quality characteristics through POST /personnel and POST /specs is also,
deliberately, the load-bearing part of this spike: it exercises the real
write-API path at a headcount/measurement-type volume nobody has tried yet,
per the dogfood rule (fsmes.sim.runner's own docstring: the MES is fed and
read only through OPC UA and its HTTP API — a scenario this runner cannot
express is a gap in the product's own surface).

Passed to fsmes.sim.runner.scored_run(..., post_boot=seed_breadth).
"""
import json
import urllib.request

from fsmes.sim.runner import _login

ROLES = ["operator", "operator", "operator", "quality_inspector", "supervisor"]

# 7 shift crews x 5 people = 35 - a proportional slice toward the eventual
# 200+, sized to what one spike run needs to measure /personnel write cost.
PEOPLE = [
    (f"MF-{crew:02d}-{n:02d}", f"Crew {crew} Operator {n}", ROLES[n % len(ROLES)])
    for crew in range(1, 8) for n in range(1, 6)
]

# Beyond the 5 baseline specs init.py seeds (one per line), 3 more distinct
# measurement types spanning breadth a real factory has - a proportional
# slice toward the eventual 30+, not the full spread (that gap is named in
# the plan's own Open questions: today's SPC module is variable-data-only,
# and visual_defect_count below is attribute-shaped on purpose, to surface
# that limitation early rather than waiting for the full build).
EXTRA_SPECS = [
    {"material": "FG-BOTTLE2", "characteristic": "cap_torque", "unit": "Nm",
     "min_value": 8.0, "max_value": 16.0},
    {"material": "FG-BRACKET2", "characteristic": "surface_finish", "unit": "Ra",
     "min_value": 0.4, "max_value": 3.2},
    {"material": "FG-ASSY", "characteristic": "visual_defect_count", "unit": "count",
     "min_value": 0.0, "max_value": 0.0},
]


def _post(base: str, path: str, token: str, body: dict) -> None:
    req = urllib.request.Request(
        f"{base}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def seed_breadth(base: str, _token: str) -> None:
    """post_boot hook. Ignores the SCOTT token scored_run hands it - SCOTT is
    `operator`, and both /personnel and /specs require admin capabilities -
    and signs in again as the plant's seeded ADMIN account instead."""
    admin_token = _login(base, code="ADMIN", password="admin")
    for code, name, role in PEOPLE:
        _post(base, "/masterdata/personnel", admin_token, {"code": code, "name": name, "role": role})
    for spec in EXTRA_SPECS:
        _post(base, "/quality/specs", admin_token, spec)
    print(f"seed_breadth: {len(PEOPLE)} personnel, {len(EXTRA_SPECS)} extra specs added via API")
