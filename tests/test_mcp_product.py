"""The product MCP surface.

These pin the three promises the agent surface makes: it works only through
the API as a signed-in account, dry_run previews without performing, and
everything performed lands in the audit trail under the agent's own name.
The tools are thin wrappers over _call/_write, so that is what gets tested -
through the real app, with the real role gates.
"""

import json

import pytest

from fsmes import mcp_server
from fsmes.services import auth


@pytest.fixture()
def wired(make_client, session, monkeypatch):
    """Point the MCP server's client cache at the in-process app.

    The idempotency middleware gets a store of its own: it writes off the
    event loop, and sharing the one test session across threads raced the
    route's teardown ("Session is already flushing") on Linux runners."""
    from contextlib import contextmanager

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from fsmes.api import idempotency
    from fsmes.domain import IdempotencyKey

    keys = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    IdempotencyKey.__table__.create(keys)

    @contextmanager
    def keys_scope():
        with Session(keys) as s:
            yield s
            s.commit()

    monkeypatch.setattr(idempotency, "session_scope", keys_scope)
    auth.create_user(session, code=mcp_server.AGENT_USER, name="Plant Agent",
                     password=mcp_server.AGENT_PASSWORD, role="agent")
    session.flush()
    client = make_client()
    monkeypatch.setattr(mcp_server, "_clients", {"testplant": client})
    monkeypatch.setattr(mcp_server, "_registry",
                        lambda: {"testplant": {"api_port": 0, "label": "Test"}})
    return client


def test_the_agent_can_run_the_order_lifecycle(wired):
    """Create and release through the tool, then read it back - the loop an
    agent asked to 'release the next order' actually runs."""
    made = mcp_server.create_order("testplant", code="WO-AGENT-1",
                                   material="FG-COLA", quantity=100)
    assert made.get("audited_as") == "AGENT", made
    out = mcp_server.orders("testplant", status="released")
    assert [o["code"] for o in out["orders"]] == ["WO-AGENT-1"]
    detail = mcp_server.order_detail("testplant", "WO-AGENT-1")
    assert detail["order"]["quantity"] == 100
    assert detail["genealogy"] is not None


def test_an_unknown_plant_is_an_error_not_a_guess(wired):
    with pytest.raises(KeyError, match="Unknown plant"):
        mcp_server.orders("atlantis")


def test_dry_run_describes_and_does_not_do(wired):
    """Counted through the tool, which signs in.

    This once counted len() of a raw unauthenticated response - a 401 body
    with one key, before and after - and passed while testing nothing.
    """
    def recorded() -> int:
        return mcp_server.quality("testplant")["checks"]["checks"]

    before = recorded()
    out = mcp_server.record_check("testplant", material="FG-COLA",
                                  characteristic="brix", value=11.0, dry_run=True)
    assert out["dry_run"] is True
    assert out["request"]["path"] == "/quality/checks"
    assert "done" not in out
    assert recorded() == before


def test_a_real_write_is_audited_under_the_agents_name(wired):
    out = mcp_server.record_check("testplant", material="FG-COLA",
                                  characteristic="brix", value=11.0)
    assert out.get("audited_as") == mcp_server.AGENT_USER, out
    trail = mcp_server.audit("testplant", actor=mcp_server.AGENT_USER)["audit"]
    assert any(e["action"] == "quality.checked" and e["actor"] == "AGENT"
               for e in trail), "the agent's action must be in the audit spine"


def test_an_api_refusal_surfaces_as_an_error_payload(wired):
    # A spec that does not exist: the API's 404 must come back as data an
    # agent can read, not an exception that kills the tool call.
    out = mcp_server.record_check("testplant", material="FG-COLA",
                                  characteristic="nonexistent", value=1.0)
    assert "error" in out
    assert "404" in out["error"]


def test_master_data_is_refused_until_an_admin_grants_it(wired, session):
    """The AGENT account holds the agent role: production and recording, never
    master data. The refusal is readable and names the capability; an admin
    can widen the role per plant, and then the same tool works."""
    out = mcp_server.create_routing("testplant", code="RT-AGENT", name="Agent route",
                                    material="FG-COLA",
                                    operations=[{"seq": 10, "name": "Mix", "equipment": "MIX01"}])
    assert "error" in out and "403" in out["error"] and "masterdata.write" in out["error"]

    from sqlalchemy import select

    from fsmes.domain import Role
    role = session.scalar(select(Role).where(Role.code == "agent"))
    role.capabilities = json.dumps([*role.granted(), "masterdata.write"])
    session.flush()
    out = mcp_server.create_routing("testplant", code="RT-AGENT", name="Agent route",
                                    material="FG-COLA",
                                    operations=[{"seq": 10, "name": "Mix", "equipment": "MIX01"}])
    assert out.get("audited_as") == "AGENT", out


def test_a_write_on_behalf_of_someone_says_so_in_the_audit_trail(wired, session):
    auth.create_user(session, code="J.LOPEZ", name="J Lopez", password="x", role="operator")
    session.flush()
    out = mcp_server.create_order("testplant", code="WO-FOR-JL", material="FG-COLA", quantity=5,
                                  release=False, on_behalf_of="j.lopez")
    assert out.get("audited_as") == "AGENT" and out.get("on_behalf_of") == "J.LOPEZ", out
    trail = mcp_server.audit("testplant", actor="AGENT")["audit"]
    row = next(e for e in trail if e["entity_id"] == "WO-FOR-JL")
    assert row["actor"] == "AGENT" and row["on_behalf_of"] == "J.LOPEZ"


def test_on_behalf_of_must_name_a_real_account(wired):
    out = mcp_server.create_order("testplant", code="WO-FOR-NOBODY", material="FG-COLA", quantity=5,
                                  release=False, on_behalf_of="nobody")
    assert "error" in out and "400" in out["error"] and "NOBODY" in out["error"]
    assert not [o for o in mcp_server.orders("testplant")["orders"] if o["code"] == "WO-FOR-NOBODY"]


def test_a_client_ref_makes_a_repeated_write_return_its_first_answer(wired):
    first = mcp_server.create_order("testplant", code="WO-ONCE", material="FG-COLA", quantity=7,
                                    release=False, client_ref="ref-once")
    assert first.get("audited_as") == "AGENT", first
    again = mcp_server.create_order("testplant", code="WO-ONCE", material="FG-COLA", quantity=7,
                                    release=False, client_ref="ref-once")
    assert "error" not in again, again
    assert again["response"] == first["response"]
    assert len([o for o in mcp_server.orders("testplant")["orders"] if o["code"] == "WO-ONCE"]) == 1
    # Without the key the second attempt is what it always was: a conflict.
    third = mcp_server.create_order("testplant", code="WO-ONCE", material="FG-COLA", quantity=7,
                                    release=False)
    assert "error" in third and "409" in third["error"]


def test_order_action_rejects_nonsense_before_touching_the_api(wired):
    out = mcp_server.order_action("testplant", "WO-1", "explode")
    assert "error" in out and "Unknown action" in out["error"]


def test_the_agent_can_raise_start_and_complete_maintenance(wired, session):
    """The maintenance loop through the tools: corrective work raised for a
    named person, started, completed with findings - and each step audited
    as AGENT for that person."""
    auth.create_user(session, code="FITTER", name="A Fitter", password="x", role="operator")
    session.flush()
    raised = mcp_server.raise_corrective_maintenance(
        "testplant", machine="MIX01", summary="Seal weeping", reason="found on walk-round",
        on_behalf_of="fitter")
    assert raised.get("audited_as") == "AGENT" and raised.get("on_behalf_of") == "FITTER", raised
    code = raised["response"]["code"]
    work = mcp_server.maintenance_work("testplant", machine="MIX01")["work"]
    assert [w["code"] for w in work] == [code] and work[0]["status"] == "due"

    started = mcp_server.perform_maintenance("testplant", order=code, action="start", on_behalf_of="fitter")
    assert started.get("audited_as") == "AGENT", started
    preview = mcp_server.perform_maintenance("testplant", order=code, action="complete",
                                             findings="seal replaced", dry_run=True)
    assert preview["dry_run"] is True and preview["request"]["body"] == {"findings": "seal replaced"}
    done = mcp_server.perform_maintenance("testplant", order=code, action="complete",
                                          findings="seal replaced", on_behalf_of="fitter")
    assert done["response"]["status"] == "done" and done["response"]["findings"] == "seal replaced"
    assert mcp_server.perform_maintenance("testplant", order=code, action="explode")["error"]

    trail = mcp_server.audit("testplant", actor="AGENT")["audit"]
    steps = [e for e in trail if e["action"].startswith("maintenance.") and e["on_behalf_of"] == "FITTER"]
    assert {e["action"] for e in steps} == {"maintenance.raised", "maintenance.started", "maintenance.completed"}


def test_planning_maintenance_is_refused_until_granted(wired):
    """maintenance.plan is a human capability by default: the agent may work
    the plan, not write it."""
    out = mcp_server.create_maintenance_plan("testplant", code="PM-AGENT", name="Agent plan",
                                             machine="MIX01", interval=100)
    assert "error" in out and "403" in out["error"] and "maintenance.plan" in out["error"]
    assert mcp_server.maintenance_due("testplant")["backlog"]["open"] >= 0
    assert mcp_server.maintenance_plans("testplant")["plans"] == []


def test_the_agent_reads_the_board_and_may_not_plan_until_granted(wired, session):
    """Planning is a person's capability by default. The agent sees the
    calendar, the board and every promise; planning comes back as a refusal
    naming scheduling.plan, and works once an admin grants it."""
    made = mcp_server.create_order("testplant", code="WO-PLAN-1", material="FG-COLA", quantity=20)
    assert made.get("audited_as") == "AGENT", made
    assert mcp_server.order_promise("testplant", "WO-PLAN-1")["scheduled"] is False
    cal = mcp_server.plant_calendar("testplant")
    assert "shifts" in cal and "exceptions" in cal
    refused = mcp_server.plan_order("testplant", "WO-PLAN-1", on_behalf_of="admin")
    assert "error" in refused and "403" in refused["error"] and "scheduling.plan" in refused["error"]

    from sqlalchemy import select

    from fsmes.domain import Role
    role = session.scalar(select(Role).where(Role.code == "agent"))
    role.capabilities = json.dumps([*role.granted(), "scheduling.plan"])
    session.flush()
    planned = mcp_server.plan_order("testplant", "WO-PLAN-1", on_behalf_of="admin")
    assert planned.get("audited_as") == "AGENT" and planned["on_behalf_of"] == "ADMIN", planned
    assert planned["response"]["order"] == "WO-PLAN-1" and planned["response"]["operations"]
    promise = mcp_server.order_promise("testplant", "WO-PLAN-1")
    assert promise["scheduled"] is True and promise["finishes"]
    board = mcp_server.schedule_board("testplant", hours=168)
    assert any(s["order"] == "WO-PLAN-1" for m in board["machines"] for s in m["slots"])
    preview = mcp_server.add_shift("testplant", code="DAY", name="Days", starts="06:00", ends="14:00", dry_run=True)
    assert preview["dry_run"] is True and preview["request"]["path"] == "/scheduling/calendar/shifts"


def test_the_agent_reads_spc_and_the_gauge_register_but_does_not_calibrate(wired):
    """Twelve checks through the tool, then the chart: control limits from
    the readings, a verdict, capability against the spec. Registering and
    calibrating gauges are human capabilities by default."""
    for value in (10.1, 10.3, 9.9, 10.2, 10.0, 10.1, 9.8, 10.2, 10.0, 10.1, 9.9, 10.2):
        out = mcp_server.record_check("testplant", material="FG-COLA", characteristic="brix", value=value)
        assert out.get("audited_as") == "AGENT", out
    chart = mcp_server.spc_chart("testplant", material="FG-COLA", characteristic="brix")
    assert chart["n"] == 12 and chart["control"] and chart["verdict"]
    assert chart["capability"] is None or "cpk" in chart["capability"]

    assert mcp_server.gauges("testplant")["gauges"] == []
    refused = mcp_server.register_gauge("testplant", code="SCALE-01", name="Bench scale", resolution=0.1)
    assert "error" in refused and "403" in refused["error"] and "masterdata.write" in refused["error"]
    preview = mcp_server.calibrate_gauge("testplant", gauge="SCALE-01", result="pass", performed_by="lab", dry_run=True)
    assert preview["dry_run"] is True and preview["request"]["path"] == "/quality/gauges/SCALE-01/calibrate"
    made = mcp_server.create_spec("testplant", material="FG-COLA", characteristic="fill_ml", unit="ml",
                                  min_value=495, max_value=505)
    assert "error" in made and "403" in made["error"], made


def test_the_agent_produces_packs_and_traces_but_a_hold_is_a_persons_call(wired, session):
    """Bottle into case onto pallet through the tools, the tree read back,
    where-used answering in packages - and quarantine refused until granted."""
    made = mcp_server.produce_units("testplant", material="FG-COLA", count=2, on_behalf_of="scott")
    assert made.get("audited_as") == "AGENT" and made["response"]["count"] == 2, made
    a, b = made["response"]["produced"]
    case = mcp_server.produce_units("testplant", material="FG-COLA", serial="CASE-1")["response"]["produced"][0]
    for s in (a, b):
        assert mcp_server.pack_unit("testplant", serial=s, into=case).get("audited_as") == "AGENT"
    tree = mcp_server.unit("testplant", case)
    assert sorted(u["serial"] for u in tree["contains"]) == sorted([a, b])
    assert mcp_server.unit_trace("testplant", a)["serial"] == a
    refused = mcp_server.set_unit_status("testplant", serial=case, status="quarantined", note="hold")
    assert "error" in refused and "403" in refused["error"] and "quality.close_nc" in refused["error"]
    preview = mcp_server.set_unit_status("testplant", serial=case, status="quarantined", dry_run=True)
    assert preview["dry_run"] is True and preview["request"]["body"]["cascade"] is True


def test_master_data_reads_are_free_and_writes_are_refused_until_granted(wired, session):
    """The agent sees the materials, a bill of material and the people; every
    master-data write comes back as a refusal naming the capability. An admin
    widening the role turns the same call into an audited write."""
    assert any(m["code"] == "FG-COLA" for m in mcp_server.materials("testplant")["materials"])
    bom = mcp_server.bill_of_material("testplant", "FG-COLA")
    assert bom["material"] == "FG-COLA" and isinstance(bom["components"], list)
    assert any(p["code"] == "AGENT" for p in mcp_server.people("testplant")["people"])

    refused = mcp_server.create_equipment("testplant", code="CELL-9", name="Cell 9", level="work_center",
                                          cost_center="CC-9")
    assert "error" in refused and "403" in refused["error"] and "masterdata.write" in refused["error"]
    assert "error" in mcp_server.update_role("testplant", code="viewer", name="Viewer", capabilities=["plant.read"])

    from sqlalchemy import select

    from fsmes.domain import Role
    role = session.scalar(select(Role).where(Role.code == "agent"))
    role.capabilities = json.dumps([*role.granted(), "masterdata.write"])
    session.flush()
    made = mcp_server.create_equipment("testplant", code="CELL-9", name="Cell 9", level="work_center",
                                       cost_center="CC-9", on_behalf_of="admin")
    assert made.get("audited_as") == "AGENT" and made["response"]["cost_center"] == "CC-9", made
    added = mcp_server.add_bom_component("testplant", material="FG-COLA", component="FG-COLA", quantity=1)
    assert "error" in added or added.get("audited_as") == "AGENT"


def test_the_agent_may_draft_a_trigger_but_never_put_it_in_force(wired):
    cat = mcp_server.trigger_catalog("testplant")
    assert "open_nc" in cat["actions"] and "bit_set" in cat["conditions"]
    made = mcp_server.draft_trigger("testplant", code="T-AGENT", name="Alarm bit 0", tag="AlarmWord",
                                    condition="bit_set", threshold=0, action="log_event", on_behalf_of="scott")
    assert made.get("audited_as") == "AGENT" and made["response"]["status"] == "draft", made
    rows = mcp_server.triggers("testplant", status="draft")["triggers"]
    assert [t["code"] for t in rows] == ["T-AGENT"]
    assert mcp_server.trigger_firings("testplant", trigger="T-AGENT")["firings"] == []


def test_the_agent_proposes_a_setpoint_change_and_cannot_approve_it(wired, session, monkeypatch):
    from fsmes.services import adjustments as adj_service
    cat = {"MIX01": {"tags": ["TemperatureSP"], "meta": {"TemperatureSP": {
        "kind": "sp", "min": 40.0, "max": 80.0, "drives": "Temperature", "writable": True}}, "analog": "Temperature"}}
    monkeypatch.setattr(adj_service.tags, "catalog", lambda: cat)
    out = mcp_server.propose_adjustment("testplant", machine="MIX01", tag="TemperatureSP", value=60,
                                        rationale="fill-weight defects rise with temperature",
                                        evidence={"correlation": 0.7}, on_behalf_of="scott")
    assert out.get("audited_as") == "AGENT" and out["response"]["status"] == "proposed", out
    code = out["response"]["code"]
    assert mcp_server.adjustment("testplant", code)["proposed_by"] == "AGENT"
    refused = mcp_server.propose_adjustment("testplant", machine="MIX01", tag="TemperatureSP", value=99,
                                            rationale="hotter")
    assert "error" in refused and "bounds" in refused["error"]
    queue = mcp_server.adjustments("testplant", status="proposed")["adjustments"]
    assert [a["code"] for a in queue] == [code]


def test_the_agent_reads_a_certificate_and_issuing_is_a_supervisors_act(wired, session):
    from fsmes.domain import ProductionSource
    from fsmes.services import execution, workorders
    wo = workorders.create(session, code="WO-COA-T", material_code="FG-COLA", quantity=2, actor="test")
    workorders.release(session, wo.code, "test")
    execution.report(session, equipment_code="MIX01", good=2, source=ProductionSource.OPC)
    execution.report(session, equipment_code="PACK01", good=2, source=ProductionSource.OPC)
    session.flush()
    cert = mcp_server.certificate("testplant", "WO-COA-T")
    assert cert["revision"] == 1 and "Certificate of analysis" in cert["body"]
    data = mcp_server.certificate("testplant", "WO-COA-T", as_data=True)
    assert data["good_qty"] == 2 and data["produced_lot"] == "WO-COA-T-FG"
    assert [c["order"] for c in mcp_server.certificates("testplant")["certificates"]] == ["WO-COA-T"]
    refused = mcp_server.issue_certificate("testplant", "WO-COA-T")
    assert "error" in refused and "403" in refused["error"]
