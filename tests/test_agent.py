"""The agent behind the floor assistant.

The promise under test: reads run freely, every write pauses as a proposal
until a person confirms, and a confirmed write runs on that person's behalf
with the proposal as its idempotency key. The model is scripted here - the
loop's discipline is what matters, not the model's judgement.
"""

import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from fsmes.services import agent, assistant
from fsmes.services import capabilities as caps

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"
PAGE_FILES = {"/dashboard": "index.html", "/dashboard/quality": "quality.html",
              "/dashboard/station": "station.html", "/dashboard/orders": "orders.html",
              "/dashboard/maintenance": "maintenance.html",
              "/dashboard/reasons": "reasons.html",
              "/dashboard/severities": "severities.html"}



# ------------------------------------------------------------ the catalogue

def test_catalogue_hides_the_loops_own_arguments():
    tools = agent.catalogue({"plant.read", "quality.record"})
    by_name = {t["name"]: t for t in tools}
    assert "record_check" in by_name and by_name["record_check"]["write"]
    props = by_name["record_check"]["input_schema"]["properties"]
    for hidden in ("plant", "dry_run", "on_behalf_of", "client_ref"):
        assert hidden not in props
    assert "plant" not in by_name["record_check"]["input_schema"]["required"]
    assert "list_plants" not in by_name
    assert by_name["machines"]["write"] is False


def test_catalogue_is_gated_by_capability():
    viewer = {t["name"] for t in agent.catalogue({"plant.read"})}
    assert "machines" in viewer and "record_check" not in viewer
    assert agent.catalogue(set()) == []


def test_every_need_is_a_capability_the_product_knows():
    unknown = {tool: cap for tool, cap in agent.NEEDS.items() if cap not in caps.CAPABILITIES}
    assert not unknown


def test_every_need_names_a_real_write_tool():
    names = {t.name for t in agent.registry_tools()}
    missing = sorted(set(agent.NEEDS) - names)
    assert not missing, f"NEEDS names tools that do not exist: {missing}"


# ---------------------------------------------------------------- the loop

def block_text(text):
    return SimpleNamespace(type="text", text=text)


def block_tool(id_, tool, **args):
    # `tool`, not `name`: a tool argument called `name` is ordinary (a reason's
    # name, a severity's) and would collide with the parameter.
    return SimpleNamespace(type="tool_use", id=id_, name=tool, input=args)


def response(*blocks, stop="end_turn"):
    usage = SimpleNamespace(input_tokens=1000, output_tokens=50, cache_read_input_tokens=800,
                            cache_creation_input_tokens=0)
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=usage)


@pytest.fixture()
def scripted(monkeypatch, tmp_path):
    """A model that follows a script, and tools that record what ran."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MES_AGENT_BRAIN", "claude")
    monkeypatch.setattr(agent, "sdk_installed", lambda: True)   # the model is scripted; no SDK needed
    monkeypatch.setattr(agent, "USAGE_FILE", tmp_path / "usage.jsonl")
    script: list = []
    calls: list[dict] = []

    def fake_model(sess):
        assert sess.history[-1]["role"] == "user"
        return script.pop(0)

    def fake_execute(name, args, *, plant, on_behalf_of=None, dry_run=None, client_ref=None):
        calls.append({"name": name, "args": args, "plant": plant, "on_behalf_of": on_behalf_of,
                      "dry_run": dry_run, "client_ref": client_ref})
        if name == "record_check" and dry_run:
            return {"dry_run": True, "would": f"record {args['characteristic']}={args['value']}",
                    "request": {"method": "POST", "path": "/quality/checks", "body": args}}
        if name == "record_check":
            return {"done": "recorded", "response": {"result": "pass"}, "audited_as": "AGENT"}
        if name == "draft_downtime_reason" and dry_run:
            return {"dry_run": True, "would": f"draft the downtime reason {args['code']}",
                    "request": {"method": "POST", "path": "/equipment/downtime-reasons", "body": args}}
        if name == "draft_downtime_reason":
            return {"done": f"draft the downtime reason {args['code']}", "audited_as": "AGENT",
                    "on_behalf_of": on_behalf_of,
                    "response": {"code": args["code"], "revision": 1, "status": "draft"}}
        return {"machines": [{"code": "WASH01"}]}

    monkeypatch.setattr(agent, "_call_model", fake_model)
    monkeypatch.setattr(agent, "execute", fake_execute)
    return script, calls


def test_reads_run_free_and_writes_pause_until_confirmed(scripted):
    script, calls = scripted
    script += [
        response(block_tool("t1", "machines"), stop="tool_use"),
        response(block_text("I will record it."),
                 block_tool("t2", "record_check", material="COLA-500", characteristic="fill_weight", value=495.0),
                 stop="tool_use"),
        response(block_text("Recorded: fill weight 495 on COLA-500, in spec.")),
    ]
    sess = agent.open_session("ADMIN", "bottling", {"plant.read", "quality.record"})
    out = agent.message(sess, "record fill weight 495 on the washer", name="Admin", role="admin")

    assert out["kind"] == "proposals" and out["say"] == "I will record it."
    assert [c["name"] for c in calls] == ["machines", "record_check"]
    assert calls[0]["dry_run"] is None                       # a read, run at once, as the agent
    assert calls[1]["dry_run"] is True and calls[1]["on_behalf_of"] == "ADMIN"
    proposal = out["proposals"][0]
    assert proposal["preview"]["would"] == "record fill_weight=495.0"
    assert proposal["surface"]["steps"][1]["fill"]["value"] == "COLA-500::fill_weight"
    assert proposal["surface"]["steps"][2]["fill"]["value"] == "495.0"
    assert sess.pending and script                           # paused: the last response is unread

    done = agent.confirm(sess, proposal["id"])
    assert done["kind"] == "reply" and done["say"].startswith("Recorded")
    real = calls[-1]
    assert real["name"] == "record_check" and real["dry_run"] is False
    assert real["on_behalf_of"] == "ADMIN" and real["client_ref"] == proposal["id"]
    assert done["done"][0]["evidence"]["anchor"] == "measurement-chart"
    assert not sess.pending and not script
    # the model saw both tool results in one user message, in call order
    results = sess.history[-2]["content"]
    assert [r["tool_use_id"] for r in results] == ["t2"]
    assert sess.history[2]["content"][0]["tool_use_id"] == "t1"


def test_drafting_a_reason_is_the_same_card_and_lands_on_the_drafters_own_screen(scripted):
    """Scott, 2026-09-24: "shouldn't the AGENT be able to perform those same
    actions and take me to that page with those actions performed?" It is the
    ordinary proposal card - the agent drafts on his behalf and nothing is in
    force, and the evidence step is the vocabulary list he would be looking at
    if he had typed the form himself."""
    script, calls = scripted
    script += [
        response(block_text("I will draft it for you to sign."),
                 block_tool("t1", "draft_downtime_reason", code="jam_infeed", name="Infeed jam",
                            description="Bottles bridged at the infeed guide"),
                 stop="tool_use"),
        response(block_text("Drafted jam_infeed. It waits for somebody who can sign it.")),
    ]
    sess = agent.open_session("SCOTT", "bottling", {"plant.read", "process.define"})
    out = agent.message(sess, "draft a downtime reason for a jam at the infeed")

    assert out["kind"] == "proposals"
    proposal = out["proposals"][0]
    assert proposal["preview"]["would"] == "draft the downtime reason jam_infeed"
    assert calls[0]["dry_run"] is True and calls[0]["on_behalf_of"] == "SCOTT"

    # "Show me" walks the real form on the real screen, already filled in.
    steps = proposal["surface"]["steps"]
    assert [s["page"] for s in steps] == ["/dashboard/reasons"] * 5
    assert [s["anchor"] for s in steps] == ["reason-form", "reason-code", "reason-name",
                                            "reason-description", "reason-submit"]
    assert steps[1]["fill"] == {"value": "jam_infeed"}
    assert steps[2]["fill"] == {"value": "Infeed jam"}
    assert steps[3]["fill"] == {"value": "Bottles bridged at the infeed guide"}
    assert "fill" not in steps[4]                            # the button is his to press

    # "Do it" runs it on his behalf and walks him to where his draft now sits.
    done = agent.confirm(sess, proposal["id"])
    real = calls[-1]
    assert real["dry_run"] is False and real["on_behalf_of"] == "SCOTT"
    assert real["client_ref"] == proposal["id"]
    assert done["done"][0]["evidence"]["page"] == "/dashboard/reasons"
    assert done["done"][0]["evidence"]["anchor"] == "reason-vocabulary"
    assert "process.approve" in done["done"][0]["evidence"]["body"]


def test_a_person_who_may_not_define_is_never_offered_the_drafting_tools():
    """The capability gate, before the API's: an operator's assistant does not
    carry a tool whose last step would refuse them."""
    operator = {t["name"] for t in agent.catalogue({"plant.read", "quality.record"})}
    assert "draft_downtime_reason" not in operator and "draft_nc_severity" not in operator
    # Reading the vocabulary is nobody's secret - a list nobody can read is a
    # list nobody can choose from.
    assert "downtime_reasons" in operator and "nc_severities" in operator
    engineer = {t["name"]: t for t in agent.catalogue({"plant.read", "process.define", "quality.define"})}
    assert engineer["draft_downtime_reason"]["write"] and engineer["draft_nc_severity"]["write"]
    for hidden in ("plant", "dry_run", "on_behalf_of", "client_ref"):
        assert hidden not in engineer["draft_downtime_reason"]["input_schema"]["properties"]
    assert set(engineer["draft_nc_severity"]["input_schema"]["required"]) == {"code", "name"}


def test_no_tool_anywhere_signs_a_vocabulary_off():
    """Decision 0035, and the `agent` role's own description: it never
    approves - not a reason code and not a severity."""
    names = {t.name for t in agent.registry_tools()}
    assert {n for n in names if "approve" in n or "sign" in n} == {"assign_role"}
    assert "process.approve" not in caps.BUILTIN_ROLES["agent"]["capabilities"]
    assert "quality.approve" not in caps.BUILTIN_ROLES["agent"]["capabilities"]
    assert "process.define" in caps.BUILTIN_ROLES["agent"]["capabilities"]
    assert "quality.define" in caps.BUILTIN_ROLES["agent"]["capabilities"]


def test_the_sentence_scott_typed_becomes_a_proposal_and_then_the_quality_call(scripted):
    """Scott at /dashboard, 2026-09-04: "submit a fill weight inspection for 495
    on WASH01."

    A machine, not a material — and a specification is held against a material,
    so the agent has to look the station up before it can propose anything. The
    reads run free; the write waits for him. Nothing here tests the model's
    judgement: the script stands in for it, and what is pinned is that his one
    sentence reaches POST /quality/checks exactly once, on his behalf, and only
    after he said yes.
    """
    script, calls = scripted
    script += [
        response(block_tool("t1", "machines"), stop="tool_use"),
        response(block_tool("t2", "quality"), stop="tool_use"),
        response(block_text("WASH01 is running COLA-500. I will record fill weight 495."),
                 block_tool("t3", "record_check", material="COLA-500",
                            characteristic="fill_weight", value=495.0, order="WO-7"),
                 stop="tool_use"),
        response(block_text("Recorded: fill weight 495 on COLA-500 against WO-7, in spec.")),
    ]
    sess = agent.open_session("SCOTT", "bottling", {"plant.read", "quality.record"})
    out = agent.message(sess, "submit a fill weight inspection for 495 on WASH01",
                        name="Scott", role="operator")

    # Looking things up needed no permission; the write did not run.
    assert out["kind"] == "proposals"
    assert [c["name"] for c in calls] == ["machines", "quality", "record_check"]
    assert [c["dry_run"] for c in calls] == [None, None, True]
    assert not any(c["dry_run"] is False for c in calls)

    proposal = out["proposals"][0]
    assert proposal["tool"] == "record_check"
    assert proposal["args"] == {"material": "COLA-500", "characteristic": "fill_weight",
                                "value": 495.0, "order": "WO-7"}
    # What he is shown is the plant's preview of the write, not the model's
    # summary of it. The real preview is built by the MCP write helper; that
    # it names this path is held by tests/test_mcp_parity.py.
    assert proposal["preview"]["request"]["path"] == "/quality/checks"

    done = agent.confirm(sess, proposal["id"])
    assert done["kind"] == "reply"
    written = [c for c in calls if c["dry_run"] is False]
    assert len(written) == 1
    assert written[0]["name"] == "record_check"
    assert written[0]["args"]["value"] == 495.0
    assert written[0]["on_behalf_of"] == "SCOTT"       # audited as him, not as the agent
    assert written[0]["client_ref"] == proposal["id"]  # a double click cannot double-record


def test_the_assistant_will_not_offer_to_record_a_check_to_somebody_who_may_not(scripted):
    """An operator without quality.record never sees the tool at all. Teaching
    somebody a task they will be refused at the last step is worse than saying
    it is not theirs to do."""
    tools = {t["name"] for t in agent.catalogue({"plant.read"})}
    assert "record_check" not in tools
    assert "close_nonconformance" not in tools
    assert "disposition_nonconformance" not in tools


def test_the_supervisor_steps_on_a_nonconformance_are_all_proposals(scripted):
    """Review, disposition and close change the plant's record of what happened
    to material. Every one of them is a write, so every one of them waits."""
    tools = {t["name"]: t for t in agent.catalogue({"plant.read", "quality.close_nc"})}
    for name in ("review_nonconformance", "disposition_nonconformance", "close_nonconformance"):
        assert name in tools, f"{name} is not offered to a supervisor"
        assert tools[name]["write"] is True, f"{name} must not run without a person"
    # Reading one is free: nobody needs permission to see what was decided.
    assert tools["nonconformance"]["write"] is False


def test_a_second_click_does_not_run_twice(scripted):
    script, calls = scripted
    script += [
        response(block_tool("t1", "record_check", material="M", characteristic="c", value=1.0), stop="tool_use"),
        response(block_text("Done.")),
    ]
    sess = agent.open_session("ADMIN", "bottling", {"plant.read", "quality.record"})
    pid = agent.message(sess, "record it")["proposals"][0]["id"]
    agent.confirm(sess, pid)
    again = agent.confirm(sess, pid)
    assert again["kind"] == "reply" and "no longer open" in again["say"]
    assert sum(1 for c in calls if c["dry_run"] is False) == 1


def test_declining_tells_the_model_and_runs_nothing(scripted):
    script, calls = scripted
    script += [
        response(block_tool("t1", "record_check", material="M", characteristic="c", value=1.0), stop="tool_use"),
        response(block_text("Understood, nothing recorded.")),
    ]
    sess = agent.open_session("OP1", "bottling", {"plant.read", "quality.record"})
    pid = agent.message(sess, "record it")["proposals"][0]["id"]
    out = agent.decline(sess, pid, "wrong machine")
    assert out["kind"] == "reply"
    assert not any(c["dry_run"] is False for c in calls)
    result = sess.history[-2]["content"][0]
    assert "wrong machine" in result["content"]
    assert "is_error" not in result  # a no is a fact, not a failure


def test_a_new_message_abandons_open_proposals(scripted):
    script, calls = scripted
    script += [
        response(block_tool("t1", "record_check", material="M", characteristic="c", value=1.0), stop="tool_use"),
        response(block_text("Okay, what next?")),
        response(block_text("Sure.")),
    ]
    sess = agent.open_session("OP1", "bottling", {"plant.read", "quality.record"})
    agent.message(sess, "record it")
    out = agent.message(sess, "never mind, what is running?")
    assert out["kind"] == "reply" and not sess.pending
    assert not any(c["dry_run"] is False for c in calls)


def test_a_tool_the_person_may_not_use_is_refused_not_run(scripted):
    script, calls = scripted
    script += [
        response(block_tool("t1", "record_check", material="M", characteristic="c", value=1.0), stop="tool_use"),
        response(block_text("You are not allowed to record checks here.")),
    ]
    sess = agent.open_session("VIEW", "bottling", {"plant.read"})
    out = agent.message(sess, "record it")
    assert out["kind"] == "reply" and not calls
    assert out["transcript"][0]["ok"] is False


def test_usage_is_logged_and_the_cap_switches_the_brain_off(scripted, monkeypatch):
    script, _ = scripted
    script += [response(block_text("Hello."))]
    sess = agent.open_session("ADMIN", "bottling", {"plant.read"})
    agent.message(sess, "hi")
    rows = agent.usage_rows()
    assert len(rows) == 1 and rows[0]["model"] == agent.MODEL
    assert rows[0]["usd"] == pytest.approx((1000 * 2 + 50 * 10 + 800 * 0.2) / 1e6)
    assert agent.spend_this_month() == pytest.approx(rows[0]["usd"])
    assert agent.spend_this_month(now=datetime(2000, 1, 1, tzinfo=UTC)) == 0

    monkeypatch.setenv("MES_AGENT_MONTHLY_USD", "0.000001")
    ok, why = agent.available()
    assert not ok and "budget is spent" in why
    out = agent.message(sess, "hi again")
    assert out["kind"] == "unavailable" and "budget" in out["say"]


def test_no_key_means_off_with_a_reason(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("MES_AGENT_BRAIN", "auto")
    ok, why = agent.available()
    assert not ok and "ANTHROPIC_API_KEY" in why
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(agent, "sdk_installed", lambda: False)
    ok, why = agent.available()
    assert not ok and "fsmes[agent]" in why
    monkeypatch.setenv("MES_AGENT_BRAIN", "off")
    assert agent.available()[0] is False


# ------------------------------------------------------------- the surfaces

def anchors_on(page: str) -> set[str]:
    html = (WEB / PAGE_FILES[page]).read_text(encoding="utf-8")
    return set(re.findall(r'data-assist="([^"]+)"', html))


@pytest.mark.parametrize("tool", sorted(assistant.SURFACES))
def test_every_surface_step_points_at_a_control_that_exists(tool):
    surface = assistant.SURFACES[tool]
    for i, step in enumerate([*surface["steps"], surface["evidence"]], 1):
        assert step["page"] in PAGE_FILES, f"{tool} step {i}: unknown page {step['page']}"
        assert step["anchor"] in anchors_on(step["page"]), (
            f"{tool} step {i} points at data-assist={step['anchor']!r} which "
            f"{PAGE_FILES[step['page']]} does not have")


@pytest.mark.parametrize("tool", sorted(assistant.SURFACES))
def test_every_page_a_walk_crosses_onto_carries_the_assistant(tool):
    """A walk that spans screens is resumed by assist.js on arrival. A page
    that does not load it would take the person there and stop."""
    surface = assistant.SURFACES[tool]
    for step in [*surface["steps"], surface["evidence"]]:
        html = (WEB / PAGE_FILES[step["page"]]).read_text(encoding="utf-8")
        assert "/static/assist.js" in html, (
            f"{tool} walks to {step['page']}, which does not load assist.js")


@pytest.mark.parametrize("tool", sorted(assistant.SURFACES))
def test_every_surface_is_a_real_write_tool_with_a_known_capability(tool):
    names = {t.name for t in agent.registry_tools()}
    assert tool in names
    assert assistant.SURFACES[tool]["needs"] in caps.CAPABILITIES
    assert agent.NEEDS.get(tool) == assistant.SURFACES[tool]["needs"]


def test_surface_fills_come_from_the_proposal():
    s = assistant.surface_for("record_check", {"material": "COLA-500", "characteristic": "fill_weight", "value": 495})
    assert s["steps"][1]["fill"] == {"value": "COLA-500::fill_weight"}
    assert s["steps"][2]["fill"] == {"value": "495"}
    assert "495" in s["steps"][2]["body"]
    assert assistant.surface_for("machines", {}) is None


# ------------------------------------------------------------------ the api

def test_status_and_a_message_without_a_key(admin, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("MES_AGENT_BRAIN", "auto")
    status = admin.get("/assist/agent/status").json()
    assert status["available"] is False and status["cap_usd"] == 10
    out = admin.post("/assist/agent", json={"message": "what is running?"}).json()
    assert out["kind"] == "unavailable"


def test_how_do_i_still_gets_a_guide(admin, monkeypatch):
    monkeypatch.setattr(assistant, "_ask_model", lambda prompt, timeout=60.0: "record-check")
    out = admin.post("/assist/agent", json={"message": "how do I record an inspection?"}).json()
    assert out["kind"] == "guide" and out["guide"]["id"] == "record-check"


def test_a_conversation_belongs_to_the_person_who_started_it(admin, supervisor, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MES_AGENT_BRAIN", "claude")
    monkeypatch.setattr(agent, "sdk_installed", lambda: True)
    monkeypatch.setattr(agent, "_call_model", lambda sess: response(block_text("Hello.")))
    monkeypatch.setattr(agent, "USAGE_FILE", Path("/nonexistent/usage.jsonl"))
    first = admin.post("/assist/agent", json={"message": "hi"}).json()
    assert first["kind"] == "reply" and first["session"]
    stolen = supervisor.post("/assist/agent/confirm", json={"session": first["session"], "proposal": "x"})
    assert stolen.status_code == 404


def test_book_output_is_a_write_tool_the_book_form_finally_has():
    tools = {t["name"]: t for t in agent.catalogue({"plant.read", "production.book"})}
    assert tools["book_output"]["write"]
    assert set(tools["book_output"]["input_schema"]["required"]) == {"equipment", "good"}


def test_surface_titles_and_defaults_render_from_the_proposal():
    s = assistant.surface_for("book_output", {"equipment": "FILL01", "good": 10})
    assert s["steps"][0]["fill"] == {"value": "FILL01"}
    assert s["steps"][3]["fill"] == {"value": "0"}          # scrap not proposed -> the form's default
    s = assistant.surface_for("set_machine_state", {"equipment": "WASH01", "state": "down", "reason": "jam"})
    assert s["steps"][1]["title"] == "Press down" and "jam" in s["steps"][1]["body"]
    s = assistant.surface_for("raise_corrective_maintenance", {"machine": "PAL01", "summary": "belt"})
    assert s["steps"][0]["tab"] == "work" and s["evidence"]["tab"] == "work"


def test_every_surface_names_pages_that_exist_and_an_example():
    for tool, surface in assistant.SURFACES.items():
        assert surface["pages"] and all(p in PAGE_FILES or p == "/dashboard/machines" for p in surface["pages"]), tool
        assert surface["example"], tool


def test_suggestions_put_this_screen_first_and_use_real_names():
    names = {"machine": "WASH01", "characteristic": "fill_weight", "mid": "500", "order": "WO-1",
             "planned_order": "WO-2", "lot": "LOT-9"}
    caps = {"plant.read", "quality.record", "equipment.state", "orders.release"}
    on_station = assistant.suggestions("/dashboard/station", caps, names)
    assert on_station[0] == "Mark WASH01 down for a jam at the infeed"
    assert "Record fill_weight 500 on WASH01" in on_station
    assert len(on_station) <= 4 and on_station[-1].startswith("What is running")
    assert assistant.suggestions("/dashboard", set(), names) == []
    generic = assistant.suggestions("/dashboard", {"plant.read", "quality.record"}, {})
    assert generic[0] == "Record a check a value on a machine"


def test_suggestions_endpoint_uses_the_plants_own_codes(admin):
    out = admin.get("/assist/suggestions?screen=/dashboard").json()["suggestions"]
    assert out and all(isinstance(s, str) for s in out)
    assert any("Record" in s for s in out)


def test_create_order_surface_opens_the_form_and_ticks_release():
    args = {"code": "WO-9", "material": "FG-BOTTLE", "quantity": 500, "release": True}
    s = assistant.surface_for("create_order", args)
    assert s["steps"][1]["open"] == "order-create" and s["steps"][1]["fill"] == {"value": "WO-9"}
    assert s["steps"][4]["fill"] == {"value": "True"}
    s = assistant.surface_for("create_order", {"code": "WO-9", "material": "FG-BOTTLE", "quantity": 500})
    assert s["steps"][4]["fill"] == {"value": "true"}   # the form's default
