"""The product MCP surface.

These pin the three promises the agent surface makes: it works only through
the API as a signed-in account, dry_run previews without performing, and
everything performed lands in the audit trail under the agent's own name.
The tools are thin wrappers over _call/_write, so that is what gets tested -
through the real app, with the real role gates.
"""

import asyncio
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
    # The lookup is a read and says so, so it has its own scope to point at.
    monkeypatch.setattr(idempotency, "read_only_session", keys_scope)
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
    for op in sorted(wo.operations, key=lambda o: o.seq):
        workorders.complete_operation(session, wo.code, op.seq, actor="test")
    session.flush()
    cert = mcp_server.certificate("testplant", "WO-COA-T")
    assert cert["revision"] == 1 and "Certificate of analysis" in cert["body"]
    data = mcp_server.certificate("testplant", "WO-COA-T", as_data=True)
    assert data["good_qty"] == 2 and data["produced_lot"] == "WO-COA-T-FG"
    assert [c["order"] for c in mcp_server.certificates("testplant")["certificates"]] == ["WO-COA-T"]
    refused = mcp_server.issue_certificate("testplant", "WO-COA-T")
    assert "error" in refused and "403" in refused["error"]


def test_the_agent_drafts_a_downtime_reason_for_a_person_and_cannot_sign_it(wired, session):
    """The vocabulary half of the write discipline: the agent holds
    `process.define`, so a draft is its to make; `process.approve` is nobody's
    tool, so the word never reaches the floor by an agent's doing."""
    auth.create_user(session, code="P.ENGEL", name="P Engel", password="x", role="supervisor")
    session.flush()

    before = mcp_server.downtime_reasons("testplant")
    assert "total" in before, before

    preview = mcp_server.draft_downtime_reason("testplant", code="jam_infeed", name="Infeed jam",
                                               description="Bottles bridged at the infeed guide",
                                               dry_run=True)
    assert preview["dry_run"] is True
    assert preview["request"]["path"] == "/equipment/downtime-reasons"
    assert preview["request"]["body"]["code"] == "jam_infeed" and "done" not in preview
    assert mcp_server.downtime_reasons("testplant")["total"] == before["total"]

    made = mcp_server.draft_downtime_reason("testplant", code="jam_infeed", name="Infeed jam",
                                            description="Bottles bridged at the infeed guide",
                                            on_behalf_of="p.engel")
    assert made.get("audited_as") == "AGENT" and made.get("on_behalf_of") == "P.ENGEL", made
    assert made["response"]["status"] == "draft" and made["response"]["revision"] == 1

    listed = mcp_server.downtime_reasons("testplant")
    row = next(r for r in listed["reasons"] if r["code"] == "jam_infeed")
    assert listed["total"] == before["total"] + 1
    # Nothing in force: a drafted word is on no operator's screen.
    assert row["in_force"] is None and row["draft"]["status"] == "draft"
    assert row["draft"]["created_by"] == "AGENT" and row["draft"]["on_behalf_of"] == "P.ENGEL"
    assert row["labels_intervals"] == 0

    trail = mcp_server.audit("testplant", actor="AGENT")["audit"]
    drafted = [e for e in trail if e["action"] == "downtime_reason.drafted"]
    assert drafted and drafted[0]["on_behalf_of"] == "P.ENGEL"

    # There is no approve tool to reach for, and the route behind one refuses
    # the agent role by name - so the refusal is a refusal, not a silence.
    names = {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}
    assert not [n for n in names if "reason" in n and "approve" in n]
    refused = mcp_server._call("testplant", "POST",
                               "/equipment/downtime-reasons/jam_infeed/approve/1")
    assert "error" in refused and "403" in refused["error"] and "process.approve" in refused["error"]
    still = next(r for r in mcp_server.downtime_reasons("testplant")["reasons"]
                 if r["code"] == "jam_infeed")
    assert still["in_force"] is None


def test_the_agent_drafts_a_severity_the_same_way_and_signs_nothing(wired):
    """The second vocabulary, in the same shape - which is the point of it
    being the same shape."""
    preview = mcp_server.draft_nc_severity("testplant", code="cosmetic", name="Cosmetic",
                                           description="Visible but fit for use", dry_run=True)
    assert preview["dry_run"] is True and preview["request"]["path"] == "/quality/severities"

    made = mcp_server.draft_nc_severity("testplant", code="cosmetic", name="Cosmetic",
                                        description="Visible but fit for use")
    assert made.get("audited_as") == "AGENT" and made["response"]["status"] == "draft", made

    listed = mcp_server.nc_severities("testplant")
    row = next(r for r in listed["severities"] if r["code"] == "cosmetic")
    assert row["in_force"] is None and row["draft"]["revision"] == 1
    assert listed["total"] >= 1 and row["labels_records"] == 0

    refused = mcp_server._call("testplant", "POST", "/quality/severities/cosmetic/approve/1")
    assert "error" in refused and "403" in refused["error"] and "quality.approve" in refused["error"]


def test_a_repeated_vocabulary_draft_with_one_client_ref_runs_once(wired):
    """A double click on the proposal card is one draft, not two revisions."""
    first = mcp_server.draft_downtime_reason("testplant", code="wash_cip", name="CIP wash",
                                            description="Clean-in-place cycle", client_ref="ref-cip")
    again = mcp_server.draft_downtime_reason("testplant", code="wash_cip", name="CIP wash",
                                            description="Clean-in-place cycle", client_ref="ref-cip")
    assert first["response"] == again["response"]
    rows = [r for r in mcp_server.downtime_reasons("testplant")["reasons"] if r["code"] == "wash_cip"]
    assert len(rows) == 1 and rows[0]["revisions"] == 1


# ------------------------------------- the settings a plant owns, any domain

def test_the_agent_reads_every_live_setting_in_a_workspace_with_its_total(wired):
    """One read for a whole workspace: what each setting is, what it is set to,
    and which capability writes it. The gate is named per key rather than per
    tool because it is the owning section's, which is what lets two tools serve
    every domain there will ever be."""
    out = mcp_server.plant_settings("testplant", "quality")
    assert out["domain"] == "quality" and out["workspace"] == "Quality"
    # Eleven sections, thirteen keys: two of those sections are one judgment
    # written as two numbers. Both totals are said, because a screen that reads
    # "eleven settings" and a list of thirteen rows is a screen nobody trusts.
    assert out["total"] == len(out["settings"]) == 13
    assert out["sections"] == 11
    by_name = {s["name"]: s for s in out["settings"]}
    capable = by_name["cpk_capable"]
    assert capable["value"] == "1.33" and capable["kind"] == "float"
    assert capable["is_default"] is True and capable["set_by"] is None
    assert capable["needs"] == "quality.define" and capable["agent_may_write"] is True
    assert capable["section"] == "cpk_bars"
    assert by_name["hold_rules"]["kind"] == "ints" and by_name["hold_rules"]["value"] == "1,2,3,4"
    # The other half of the list, stated even while nothing is in it: a key a
    # workspace shows and nothing can change while the plant runs.
    assert out["not_written_here_total"] == len(out["not_written_here"])


def test_the_same_two_tools_answer_for_a_workspace_with_nothing_live_in_it(wired, monkeypatch):
    """A Configuration page with no live settings yet answers with a total of
    nothing - not an error, and not a guess.

    Every real domain has something live in it now (Quality, Engineering,
    Administration, supply chain each built their own), so this pins the
    *empty* case by removing engineering's sections for the one call rather
    than by trusting a domain to stay empty - which is exactly the trust this
    tool's whole point was to not need."""
    from fsmes import modules

    real = modules.config_sections
    monkeypatch.setattr(modules, "config_sections",
                        lambda domain, served=None: () if domain == "engineering" else real(domain, served))

    out = mcp_server.plant_settings("testplant", "engineering")
    assert out["domain"] == "engineering" and out["total"] == 0

    refused = mcp_server.write_plant_setting("testplant", "engineering",
                                             key="cpk_capable", value="1.4")
    assert "error" in refused and "404" in refused["error"]
    assert "engineering" in refused["error"]


def test_a_workspace_this_version_does_not_have_is_refused_by_name(wired):
    out = mcp_server.plant_settings("testplant", "atlantis")
    assert "error" in out and "404" in out["error"]
    # The refusal lists what there is, so finding out is asking rather than guessing.
    assert "engineering" in out["error"] and "quality" in out["error"]


def test_a_dry_run_setting_write_previews_a_patch_and_changes_nothing(wired):
    preview = mcp_server.write_plant_setting("testplant", "quality", key="cpk_capable",
                                             value="1.5", dry_run=True)
    assert preview["dry_run"] is True and "done" not in preview
    assert preview["request"]["method"] == "PATCH"
    assert preview["request"]["path"] == "/dashboard/config/quality/settings/cpk_capable"
    assert preview["request"]["body"] == {"value": "1.5"}
    assert "1.5" in preview["would"] and "cpk_capable" in preview["would"]
    still = mcp_server.plant_settings("testplant", "quality")
    assert next(s for s in still["settings"] if s["name"] == "cpk_capable")["value"] == "1.33"


def test_a_setting_written_for_somebody_is_in_force_at_once_and_audited(wired, session):
    """No draft and nothing to sign: the next read is the new value. The write
    is the agent's, for the person who asked for it, and the audit row keeps
    what it was so typing the old number back is the undo."""
    auth.create_user(session, code="R.OKON", name="R Okon", password="x", role="supervisor")
    session.flush()
    done = mcp_server.write_plant_setting("testplant", "quality", key="cpk_capable",
                                          value="1.5", on_behalf_of="r.okon")
    assert done.get("audited_as") == "AGENT" and done.get("on_behalf_of") == "R.OKON", done
    assert done["response"]["value"] == "1.5" and done["response"]["is_default"] is False
    assert done["response"]["set_by"] == "AGENT"

    now = mcp_server.plant_settings("testplant", "quality")
    assert next(s for s in now["settings"] if s["name"] == "cpk_capable")["value"] == "1.5"

    trail = mcp_server.audit("testplant", actor="AGENT")["audit"]
    row = next(e for e in trail if e["action"] == "plant_setting.set")
    assert row["on_behalf_of"] == "R.OKON" and row["entity_id"] == "[quality] cpk_capable"
    assert row["after"]["value"] == "1.5"
    # `before` is null and not "1.33": this plant had no row for the key, so
    # the product's default was standing. Writing the shipped number in as the
    # value somebody changed would be inventing a decision nobody made.
    assert row["before"] is None

    # The second write has a before, because now there is one.
    mcp_server.write_plant_setting("testplant", "quality", key="cpk_capable", value="1.6")
    trail = mcp_server.audit("testplant", actor="AGENT")["audit"]
    again = next(e for e in trail if e["action"] == "plant_setting.set")
    assert again["before"]["value"] == "1.5" and again["after"]["value"] == "1.6"


def test_a_value_the_checker_refuses_comes_back_as_the_plants_own_sentence(wired):
    """A marginal Cpk bar above the capable one is not a crash and not a silent
    save: it is the sentence `fsmes pack check` prints for the same value in a
    file, judged against the capable bar this plant is actually running on."""
    out = mcp_server.write_plant_setting("testplant", "quality", key="cpk_marginal", value="1.5")
    assert "error" in out and "422" in out["error"]
    assert "marginal" in out["error"]
    unchanged = mcp_server.plant_settings("testplant", "quality")
    assert next(s for s in unchanged["settings"] if s["name"] == "cpk_marginal")["value"] == "1.0"


def test_a_value_that_is_not_the_kind_of_thing_at_all_is_refused_too(wired):
    out = mcp_server.write_plant_setting("testplant", "quality", key="spc_min_points",
                                         value="a dozen")
    assert "error" in out and "422" in out["error"] and "whole number" in out["error"]


def test_a_setting_is_refused_when_the_role_does_not_grant_its_sections_capability(wired, session):
    """The gate is the section's, so the refusal names the section's capability
    and not this endpoint's - there is no such thing as this endpoint's."""
    from sqlalchemy import select

    from fsmes.domain import Role
    role = session.scalar(select(Role).where(Role.code == "agent"))
    role.capabilities = json.dumps([c for c in role.granted() if c != "quality.define"])
    session.flush()

    out = mcp_server.write_plant_setting("testplant", "quality", key="cpk_capable", value="1.4")
    assert "error" in out and "403" in out["error"] and "quality.define" in out["error"]
    # Reading is free: losing the capability to write one does not hide it.
    listed = mcp_server.plant_settings("testplant", "quality")
    assert listed["total"] == 13
    assert all(s["agent_may_write"] is False for s in listed["settings"])


def test_a_repeated_setting_write_with_one_client_ref_runs_once(wired):
    """A double click on the proposal card writes one audit row, not two."""
    first = mcp_server.write_plant_setting("testplant", "quality", key="spc_min_points",
                                           value="20", client_ref="ref-min-points")
    again = mcp_server.write_plant_setting("testplant", "quality", key="spc_min_points",
                                           value="20", client_ref="ref-min-points")
    assert first["response"] == again["response"]
    trail = mcp_server.audit("testplant", actor="AGENT")["audit"]
    rows = [e for e in trail if e["entity_id"] == "[quality] spc_min_points"]
    assert len(rows) == 1


# --------------------- finding one by what a person calls it, and its history

def test_a_setting_is_found_by_the_words_a_person_uses_for_it(wired):
    """Scott, 2026-09-26: *"I want to change the default reporting window to
    10 hrs"*. He named the section's label word for word, and the old list
    carried the key name and no label at all - so five calls later the
    assistant said no workspace held such a setting.

    One call now, no workspace named, and the answer is the key."""
    found = mcp_server.plant_settings("testplant", find="reporting window")
    assert found["total"] == 1 and found["showing"] == 1
    row = found["settings"][0]
    assert row["name"] == "default_report_hours" and row["domain"] == "engineering"
    assert row["label"] == "The default reporting window"
    assert row["value"] == "8.0" and row["agent_may_write"] is True
    # It searched every workspace and says which, so "not found" is a
    # statement about the whole plant rather than about one page.
    assert found["searched_total"] == len(found["searched"]) >= 4


def test_a_search_says_where_it_looked_when_nothing_matches(wired):
    nothing = mcp_server.plant_settings("testplant", find="carburettor")
    assert nothing["total"] == 0 and nothing["settings"] == []
    assert "carburettor" in nothing["nothing_matched"] or "carburettor" in nothing["find"]
    assert "engineering" in nothing["nothing_matched"]


def test_one_setting_comes_back_in_full_with_the_paragraph_that_describes_it(wired):
    """`about` is out of every list and one call away for the setting somebody
    is actually reading - including when nobody said which workspace has it."""
    one = mcp_server.plant_settings("testplant", key="default_report_hours")
    assert one["domain"] == "engineering" and one["workspace"] == "Engineering"
    assert one["setting"]["key"] == "[process] default_report_hours"
    assert "hours" in one["setting"]["about"]
    assert one["setting"]["kind"] == "float"

    missing = mcp_server.plant_settings("testplant", key="carburettor_gap")
    assert "error" in missing and "find=" in missing["error"]


def test_a_long_workspace_holds_rows_back_and_says_how_many_it_has(wired):
    """Administration has thirty-nine live settings, which is more text than
    one tool result carries. The list that comes back is short, says it is
    short, says how many there are, and names the call that reaches the rest -
    which is the whole difference from the answer that told Scott the setting
    did not exist."""
    first = mcp_server.plant_settings("testplant", "administration")
    assert first["total"] > first["showing"] > 0
    assert str(first["total"]) in first["more"] and "offset=" in first["more"]

    seen = [row["name"] for row in first["settings"]]
    offset = first["showing"]
    while offset < first["total"]:
        page = mcp_server.plant_settings("testplant", "administration", offset=offset)
        assert page["total"] == first["total"] and page["showing"] > 0
        seen += [row["name"] for row in page["settings"]]
        offset += page["showing"]
    assert len(seen) == len(set(seen)) == first["total"]
    assert "default_new_account_role" in seen


def test_calling_it_with_no_workspace_lists_the_workspaces_and_their_totals(wired):
    """The cheapest first move, and the one that used to be reachable only by
    naming a workspace that does not exist and reading the 404."""
    index = mcp_server.plant_settings("testplant")
    by_slug = {w["domain"]: w for w in index["workspaces"]}
    assert index["total"] == len(index["workspaces"]) >= 4
    assert by_slug["quality"]["settings"] == 13
    assert by_slug["administration"]["settings"] > by_slug["supply_chain"]["settings"]
    assert by_slug["engineering"]["href"] == "/dashboard/config/engineering"


def test_the_history_of_one_setting_says_who_set_it_from_what_to_what(wired, session):
    """The second half of what went wrong: Scott changed the reporting window
    himself and asked for it back, and the assistant answered *"I don't see
    any change recorded"* without making a single tool call - because there
    was no tool through which it could have looked. There is one now."""
    auth.create_user(session, code="R.OKON", name="R Okon", password="x", role="supervisor")
    session.flush()
    mcp_server.write_plant_setting("testplant", "engineering",
                                   key="default_report_hours", value="10",
                                   on_behalf_of="r.okon")

    seen = mcp_server.setting_changes("testplant", key="default_report_hours")
    assert seen["total"] == 1
    change = seen["changes"][0]
    assert change["setting"] == "[process] default_report_hours"
    assert change["name"] == "default_report_hours"
    assert change["who"] == "R.OKON" and change["as"] == "AGENT"
    # `from` is null because the plant had no row: the product's default was
    # standing. Printing 8.0 here would be inventing a decision nobody made.
    assert change["from"] is None and change["to"] == "10.0"

    mcp_server.write_plant_setting("testplant", "engineering",
                                   key="default_report_hours", value="8")
    again = mcp_server.setting_changes("testplant", key="default_report_hours")
    assert again["total"] == 2
    assert again["changes"][0]["from"] == "10.0" and again["changes"][0]["to"] == "8.0"


def test_the_history_of_a_workspace_says_how_much_of_the_trail_it_read(wired):
    """A filtered list that does not say what it filtered from reads like the
    whole trail. `/audit` has no filter for a set of keys, so the filtering is
    this tool's and it says so."""
    mcp_server.write_plant_setting("testplant", "quality", key="cpk_capable", value="1.4")
    mcp_server.write_plant_setting("testplant", "engineering",
                                   key="default_report_hours", value="10")

    quality_only = mcp_server.setting_changes("testplant", domain="quality")
    assert [c["name"] for c in quality_only["changes"]] == ["cpk_capable"]
    assert quality_only["audit_rows_read"] >= 2

    everything = mcp_server.setting_changes("testplant")
    assert {c["name"] for c in everything["changes"]} == {"cpk_capable",
                                                          "default_report_hours"}


def test_a_setting_nobody_has_written_says_the_record_is_empty_not_that_it_looked(wired):
    """*No change is recorded* is only an honest sentence after the record has
    been read, and this is the sentence that has read it."""
    quiet = mcp_server.setting_changes("testplant", key="cpk_capable")
    assert quiet["total"] == 0 and quiet["changes"] == []
    assert "cpk_capable" in quiet["nothing_recorded"]
    assert "audit trail" in quiet["nothing_recorded"]


def test_there_is_no_tool_that_approves_a_setting(wired):
    """There is nothing to approve. A number in force the moment it is saved
    has no pending state (decision 0035, rule three), so an approve tool here
    would be a tool for a step that does not exist."""
    names = {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}
    assert "write_plant_setting" in names and "plant_settings" in names
    assert not [n for n in names if "setting" in n and "approve" in n]


# ------------------------------------------------- the operator's own actions
# `order_action` released, held, resumed, closed and cancelled an order and
# stopped there, so the assistant could do everything to an order except the
# thing somebody standing at a machine actually does: start the step in front
# of them and say it is done. Found on 2026-09-26 by measuring the tools
# against the write routes rather than by anybody asking.

def test_the_agent_starts_and_completes_a_step_of_an_order(wired):
    mcp_server.create_order("testplant", code="WO-STEP", material="FG-COLA", quantity=10)

    started = mcp_server.start_operation("testplant", order="WO-STEP", seq=10)
    assert started.get("audited_as") == "AGENT", started
    detail = mcp_server.order_detail("testplant", "WO-STEP")
    step = next(o for o in detail["order"]["operations"] if o["seq"] == 10)
    assert step["status"] == "running"

    done = mcp_server.complete_operation("testplant", order="WO-STEP", seq=10)
    assert done.get("audited_as") == "AGENT", done
    detail = mcp_server.order_detail("testplant", "WO-STEP")
    step = next(o for o in detail["order"]["operations"] if o["seq"] == 10)
    assert step["status"] == "done"


def test_the_preview_of_a_step_names_the_step_and_says_what_it_is_doing_now(wired):
    """A person confirming "start step 10" is being asked about a sequence
    number. The tool reads the step first, so the sentence says which step,
    on which machine, and what state it is in."""
    mcp_server.create_order("testplant", code="WO-WORDS", material="FG-COLA", quantity=4)
    out = mcp_server.start_operation("testplant", order="WO-WORDS", seq=20, dry_run=True)
    assert out["dry_run"] is True
    assert out["would"] == ("start step 20 (Pack) on PACK01 of WO-WORDS, "
                            "which is pending now")
    assert out["request"]["path"] == "/workorders/WO-WORDS/operations/20/start"

    # Counts land on the first step that has been started, so the step that
    # gets the booking is the one the preview must talk about.
    mcp_server.start_operation("testplant", order="WO-WORDS", seq=10)
    booked = mcp_server.book_output("testplant", equipment="MIX01", good=3, scrap=1,
                                    order="WO-WORDS")
    assert "error" not in booked, booked
    finishing = mcp_server.complete_operation("testplant", order="WO-WORDS", seq=10,
                                              dry_run=True)
    assert finishing["would"] == ("complete step 10 (Mix) on MIX01 of WO-WORDS, "
                                  "with 3.0 good and 1.0 scrap booked")


def test_a_step_the_plant_will_not_start_is_refused_in_the_plants_own_words(wired):
    mcp_server.create_order("testplant", code="WO-TWICE", material="FG-COLA", quantity=2)
    mcp_server.start_operation("testplant", order="WO-TWICE", seq=10)
    again = mcp_server.start_operation("testplant", order="WO-TWICE", seq=10)
    assert "error" in again and "already running" in again["error"]


def test_a_step_of_an_order_nobody_has_heard_of_is_still_describable(wired):
    """The read that makes the sentence better must not be the thing that
    breaks it. With no order to read, the preview says less and still says
    what would be sent."""
    out = mcp_server.start_operation("testplant", order="WO-NOPE", seq=10, dry_run=True)
    assert out["would"] == "start step 10 of WO-NOPE"
    assert out["request"]["path"] == "/workorders/WO-NOPE/operations/10/start"


def test_the_agent_books_in_a_lot_in_the_materials_own_unit(wired):
    out = mcp_server.create_lot("testplant", code="LOT-SUGAR-002",
                                material="RAW-SUGAR", quantity=240, dry_run=True)
    assert out["would"] == "book in lot LOT-SUGAR-002: 240 kg of RAW-SUGAR"
    assert out["request"]["path"] == "/execution/lots"

    done = mcp_server.create_lot("testplant", code="LOT-SUGAR-002",
                                 material="RAW-SUGAR", quantity=240)
    assert done.get("audited_as") == "AGENT", done
    lots = mcp_server.lots("testplant")["lots"]
    booked = next(lot for lot in lots if lot["code"] == "LOT-SUGAR-002")
    assert booked["quantity"] == 240

    # And it can then be issued, which is the point of booking it in.
    mcp_server.create_order("testplant", code="WO-USES-LOT", material="FG-COLA", quantity=5)
    issued = mcp_server.issue_material("testplant", order="WO-USES-LOT",
                                       lot="LOT-SUGAR-002", quantity=10)
    assert "error" not in issued, issued


def test_a_lot_of_a_material_the_plant_never_heard_of_is_refused(wired):
    out = mcp_server.create_lot("testplant", code="LOT-GHOST", material="RAW-GHOST",
                                quantity=1)
    assert "error" in out and "RAW-GHOST" in out["error"]


def test_revising_an_instruction_opens_the_next_revision_as_a_draft(wired):
    mcp_server.draft_instruction("testplant", code="WI-REV", title="Changeover",
                                 body="Old text.")
    out = mcp_server.revise_document("testplant", code="WI-REV", body="New text.",
                                     dry_run=True)
    # Nothing is approved yet, so this edits the draft rather than opening one.
    assert out["would"] == ("edit the open draft revision 1 of WI-REV - still "
                            "unapproved, changing its text")
    assert out["request"]["path"] == "/documents/WI-REV/revise"

    done = mcp_server.revise_document("testplant", code="WI-REV", body="New text.")
    assert done.get("audited_as") == "AGENT", done
    assert mcp_server.instruction("testplant", "WI-REV")["instruction"]["body"] == "New text."


def test_revising_an_approved_instruction_says_what_it_is_copying(wired, session):
    """The from and the to, in the sentence. An approved revision is never
    edited in place, so this is a different act from editing a draft and the
    preview has to say which one it is."""
    from sqlalchemy import select

    from fsmes.domain import Role

    mcp_server.draft_instruction("testplant", code="WI-FORCE", title="Cleaning",
                                 body="Rev one.")
    role = session.scalar(select(Role).where(Role.code == "agent"))
    role.capabilities = json.dumps([*role.granted(), "documents.approve"])
    session.flush()
    approved = mcp_server._call("testplant", "POST", "/documents/WI-FORCE/approve/1")
    assert "error" not in approved, approved

    out = mcp_server.revise_document("testplant", code="WI-FORCE", title="Cleaning v2",
                                     dry_run=True)
    assert out["would"] == ("open revision 2 of WI-FORCE as a draft, copying "
                            "revision 1 which is in force, changing its title")


def test_revising_a_document_that_does_not_exist_is_refused_by_name(wired):
    out = mcp_server.revise_document("testplant", code="WI-NOPE", body="x")
    assert "error" in out and "WI-NOPE" in out["error"]


def test_there_is_still_no_tool_that_approves_a_document(wired):
    """`revise_document` drafts; putting a revision in force stays the
    approver's own signature (decision 0035)."""
    names = {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}
    assert {"revise_document", "create_document", "draft_instruction"} <= names
    assert not [n for n in names if "approve" in n or "withdraw" in n]
