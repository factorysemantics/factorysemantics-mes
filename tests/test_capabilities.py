"""Capability-based roles.

The question that drove this: can an admin give a new employee a quality
inspector role that lets them record inspections and nothing else? Under the
old ladder the answer was no - anyone who could inspect was an operator, and
every operator could also book production, issue material and change machine
states. These tests are that question, asked of the code.
"""

import pytest

from fsmes.services import capabilities as caps

# --------------------------------------------------------- the built-in five

def test_the_ladder_still_grants_exactly_what_it_used_to():
    """Existing accounts must not gain or lose a thing in the conversion."""
    operator = set(caps.BUILTIN_ROLES["operator"]["capabilities"])
    supervisor = set(caps.BUILTIN_ROLES["supervisor"]["capabilities"])
    admin = set(caps.BUILTIN_ROLES["admin"]["capabilities"])
    viewer = set(caps.BUILTIN_ROLES["viewer"]["capabilities"])

    assert viewer < operator < supervisor < admin, "the ladder is still a ladder"
    assert "orders.close" in supervisor and "orders.close" not in operator
    assert "masterdata.write" in admin and "masterdata.write" not in supervisor


def test_every_shipped_role_grants_only_real_capabilities():
    for code, spec in caps.BUILTIN_ROLES.items():
        assert not caps.unknown(spec["capabilities"]), f"{code} grants something unknown"


def test_quality_inspector_is_a_rung_the_ladder_could_not_hold():
    """The whole point.

    Inspector grants strictly less than operator, yet more than viewer in one
    specific way - it can record checks. The old ladder had no rung there:
    quality.record arrived with operator, and with it came booking production,
    issuing material and setting machine states. Bundles can express the gap;
    a ladder cannot.
    """
    inspector = set(caps.BUILTIN_ROLES["quality_inspector"]["capabilities"])
    operator = set(caps.BUILTIN_ROLES["operator"]["capabilities"])
    viewer = set(caps.BUILTIN_ROLES["viewer"]["capabilities"])

    assert "quality.record" in inspector and "quality.record" not in viewer
    assert inspector < operator, "an inspector can do strictly less than an operator"
    assert operator - inspector == {
        "production.book", "production.consume", "orders.create",
        "orders.release", "equipment.state",
        # An inspector inspects. Servicing a machine is a different job and a
        # different competence, whatever the org chart says about seniority.
        "maintenance.perform",
    }


# ------------------------------------------------- the inspector, end to end

@pytest.fixture()
def inspector(sign_in):
    return sign_in("INSPECTOR", role="quality_inspector")


def test_an_inspector_can_record_an_inspection(inspector):
    r = inspector.post("/quality/checks",
                       json={"material": "FG-COLA", "characteristic": "brix", "value": 11.0})
    assert r.status_code == 201, r.text


def test_an_inspector_cannot_book_production(inspector):
    r = inspector.post("/execution/report", json={"equipment": "MIX01", "good": 5})
    assert r.status_code == 403
    assert "production.book" in r.json()["detail"]


def test_an_inspector_cannot_issue_material(inspector):
    r = inspector.post("/execution/consume",
                       json={"order": "WO-1", "lot": "LOT-1", "quantity": 1})
    assert r.status_code == 403


def test_an_inspector_cannot_change_a_machine_state(inspector):
    r = inspector.post("/equipment/MIX01/state", json={"state": "down"})
    assert r.status_code == 403


def test_an_inspector_cannot_close_the_non_conformance_they_raised(inspector):
    """Raising is inspection; closing is disposition. Different jobs, and the
    role boundary is the point of separating them."""
    made = inspector.post("/quality/checks",
                          json={"material": "FG-COLA", "characteristic": "brix", "value": 99.0})
    assert made.status_code == 201
    code = made.json().get("non_conformance")
    assert code, "an out-of-spec check must raise one"
    assert inspector.post(f"/quality/nonconformances/{code}/close").status_code == 403


def test_an_inspector_can_still_read_the_plant(inspector):
    assert inspector.get("/workorders").status_code == 200
    assert inspector.get("/dashboard/summary").status_code == 200


# ------------------------------------------------------- administering roles

def test_an_admin_can_define_a_role_the_product_never_shipped(sign_in):
    admin = sign_in("ADMIN", "admin")
    r = admin.post("/admin/roles", json={
        "code": "line_lead", "name": "Line Lead",
        "description": "Runs the line and closes its orders, but no master data.",
        "capabilities": ["plant.read", "production.book", "orders.close"],
    })
    assert r.status_code == 201, r.text
    assert set(r.json()["capabilities"]) == {"plant.read", "production.book", "orders.close"}
    assert r.json()["builtin"] is False


def test_a_misspelt_capability_is_refused_rather_than_silently_granting_nothing(sign_in):
    admin = sign_in("ADMIN", "admin")
    r = admin.post("/admin/roles", json={
        "code": "typo", "name": "Typo", "capabilities": ["quality.recrod"]})
    assert r.status_code == 400
    assert "quality.recrod" in r.json()["detail"]


def test_the_admin_role_cannot_be_deleted(sign_in):
    admin = sign_in("ADMIN", "admin")
    r = admin.delete("/admin/roles/admin")
    assert r.status_code == 400
    assert "protected" in r.json()["detail"]


def test_a_role_still_held_by_someone_cannot_be_deleted(sign_in, session):
    from fsmes.services import auth
    auth.create_user(session, code="HOLDER", name="Holder",
                     password="x", role="quality_inspector")
    session.flush()
    admin = sign_in("ADMIN", "admin")
    r = admin.delete("/admin/roles/quality_inspector")
    assert r.status_code == 400
    assert "HOLDER" in r.json()["detail"]


def test_changing_a_users_role_takes_effect_on_their_next_call(sign_in, session):
    """Capabilities resolve per request rather than living in the token, so a
    revoked power is gone at once - not at the user's next sign-in."""
    from fsmes.services import auth
    auth.create_user(session, code="NEWHIRE", name="New Hire",
                     password="pw", role="operator")
    session.flush()

    hire = sign_in("NEWHIRE", "pw")
    assert hire.post("/equipment/MIX01/state", json={"state": "idle"}).status_code == 200

    admin = sign_in("ADMIN", "admin")
    assert admin.put("/admin/users/NEWHIRE/role",
                     json={"role": "quality_inspector"}).status_code == 200

    # Same token, no re-login: the power is already gone.
    assert hire.post("/equipment/MIX01/state", json={"state": "down"}).status_code == 403


def test_a_non_admin_cannot_administer_roles(client):
    assert client.get("/admin/users").status_code == 403
    assert client.post("/admin/roles", json={"code": "x", "name": "X"}).status_code == 403


def test_the_capability_vocabulary_is_readable_by_anyone_signed_in(client):
    """A screen has to explain what it is offering. The list is not a secret -
    what a person holds is."""
    r = client.get("/admin/capabilities")
    assert r.status_code == 200
    names = [c["name"] for c in r.json()["capabilities"]]
    assert "quality.record" in names
    assert all(c["description"] for c in r.json()["capabilities"])


# ------------------------------------------------- the ladder stays dead

WEB = __import__("pathlib").Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"


def test_no_screen_gates_on_a_role_name():
    """data-min-role was the last role-name gate in the product. The ladder
    version of the floor screen's can() put quality_inspector at index -1 and
    hid the one form that role exists to use."""
    for path in list(WEB.glob("*.html")) + list(WEB.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        assert "data-min-role" not in text, f"{path.name} still gates on a role name"
        assert "ROLES.indexOf" not in text, f"{path.name} still ranks roles"


def test_every_ui_capability_gate_names_a_real_capability():
    """A gate on a capability the product does not recognise hides the
    control from everybody, forever, silently."""
    import re

    from fsmes.services.capabilities import CAPABILITIES

    for path in list(WEB.glob("*.html")) + list(WEB.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        for cap in re.findall(r'data-needs-cap="([^"]+)"', text):
            assert cap in CAPABILITIES, f"{path.name} gates on unknown {cap!r}"
        for cap in re.findall(r'FS\.can\("([^"]+)"\)|\bcan\("([a-z_]+\.[a-z_]+)"\)', text):
            name = cap[0] or cap[1]
            if name:
                assert name in CAPABILITIES, f"{path.name} checks unknown {name!r}"


def test_the_floor_forms_gate_on_what_they_do():
    """Each form needs the capability it exercises - which is exactly what
    lets an inspector see the quality form and nothing else."""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'data-assist="quality-form" data-needs-cap="quality.record"' in html
    assert 'data-assist="report-form" data-needs-cap="production.book"' in html
    assert 'data-assist="consume-form" data-needs-cap="production.consume"' in html


def test_the_agent_role_runs_production_and_never_approves_or_administers():
    """Principle 3 mechanised: an agent token never earns a role of standing.
    It does what an operator does, reads the trail, drafts documents - and
    holds none of the powers a person must exercise on purpose."""
    from fsmes.services import capabilities as caps

    agent = set(caps.BUILTIN_ROLES["agent"]["capabilities"])
    operator = set(caps.BUILTIN_ROLES["operator"]["capabilities"])
    supervisor = set(caps.BUILTIN_ROLES["supervisor"]["capabilities"])
    assert operator < agent < supervisor | {"documents.write"}
    for held_by_people_only in ("users.manage", "masterdata.write", "documents.approve",
                                "orders.close", "quality.close_nc", "maintenance.plan",
                                "scheduling.plan"):
        assert held_by_people_only not in agent, held_by_people_only


def test_a_person_cannot_claim_to_act_for_someone_else(sign_in, session):
    """The on-behalf-of header is honoured for the agent role only. A
    supervisor sending it is acting as themselves, whatever the header says."""
    from sqlalchemy import select

    from fsmes.domain import AuditLog

    sup = sign_in("SUP-OBO", role="supervisor")
    r = sup.post("/workorders", json={"code": "WO-OBO-1", "material": "FG-COLA", "quantity": 3},
                 headers={"X-On-Behalf-Of": "ADMIN"})
    assert r.status_code == 201, r.text
    row = session.scalar(select(AuditLog).where(AuditLog.entity_id == "WO-OBO-1"))
    assert row.actor == "SUP-OBO" and row.on_behalf_of is None
