"""The agent behind the floor assistant.

The promise under test: reads run freely, every write pauses as a proposal
until a person confirms, and a confirmed write runs on that person's behalf
with the proposal as its idempotency key. The model is scripted here - the
loop's discipline is what matters, not the model's judgement.
"""

import json
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
              "/dashboard/severities": "severities.html",
              # One file serves every workspace's Configuration page and reads
              # the workspace out of its own address, so the authored step
              # names the template and the proposal fills it in.
              "/dashboard/config/{domain}": "config.html"}



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


# ------------------------------------------- what the model is actually shown

def a_settings_payload(n: int) -> dict:
    """A list shaped like the one that broke: a workspace, its rows, and the
    totals around them."""
    return {"plant": "bottling", "domain": "engineering", "workspace": "Engineering",
            "settings": [{"name": f"setting_{i}", "label": f"The {i}th number",
                          "value": "8.0", "default": "8.0", "is_default": True,
                          "set_by": None, "kind": "float", "section": f"sec_{i}",
                          "needs": "process.define", "agent_may_write": True}
                         for i in range(n)],
            "total": n, "sections": n}


def test_a_result_that_fits_reaches_the_model_untouched():
    block = agent._tool_result("t1", a_settings_payload(3), limit=6000)
    assert json.loads(block["content"]) == a_settings_payload(3)
    assert "truncated" not in block["content"]


def test_a_long_list_loses_whole_items_and_says_how_many_of_how_many():
    """Scott, 2026-09-26: the assistant was handed twenty-two settings, shown
    the first six thousand characters of them, and told Scott the seventeenth
    did not exist. The cut landed inside the eleventh row - the JSON did not
    even parse - and the only sign of it was the word `…(truncated)`.

    What the model sees now is a shorter list that says it is shorter."""
    block = agent._tool_result("t1", a_settings_payload(22), limit=2000)
    shown = json.loads(block["content"])                 # it still parses: whole items
    assert 0 < len(shown["settings"]) < 22
    assert shown["total"] == 22                          # the plant's number, not the page's
    assert shown["truncated"].startswith(
        f"showing {len(shown['settings'])} of 22 settings")
    assert "do not report these as all there are" in shown["truncated"]
    assert len(block["content"]) <= 2000
    # Whole rows, from the front, in order - not a sample and not the tail.
    assert [row["name"] for row in shown["settings"]] == [
        f"setting_{i}" for i in range(len(shown["settings"]))]


def test_the_longest_list_is_the_one_that_gives_way():
    """A payload with two lists drops from the one carrying the bulk, so the
    short list beside it survives whole."""
    payload = {"changes": [{"name": f"c{i}", "from": "1.0", "to": "2.0",
                            "who": "ADMIN", "when": "2026-09-26T10:12:40Z"}
                           for i in range(60)],
               "switched_off": ["Recipes", "Serialisation"]}
    shown = json.loads(agent._tool_result("t1", payload, limit=1500)["content"])
    assert shown["switched_off"] == ["Recipes", "Serialisation"]
    assert 0 < len(shown["changes"]) < 60
    assert "changes" in shown["truncated"]


def test_a_result_that_is_not_a_list_still_cuts_but_says_what_it_dropped():
    """Some answers are one long string, and there is nothing whole to drop
    from them. The cut stays; what is new is that the marker is a number
    rather than a shrug."""
    block = agent._tool_result("t1", "x" * 9000, limit=1000)
    assert len(block["content"]) <= 1000
    assert "characters not shown" in block["content"]
    assert "9,000" in block["content"] or "9000" in block["content"]


def test_an_error_is_still_marked_an_error_after_being_shortened():
    payload = {"error": "no", "items": [{"a": "x" * 80} for _ in range(50)]}
    block = agent._tool_result("t1", payload, limit=900)
    assert block["is_error"] is True
    assert json.loads(block["content"])["error"] == "no"


# ------------------------------------------------- what the model is told to do

def test_the_prompt_says_a_proposal_is_not_a_change_already_under_way():
    """Seen 2026-09-25 22:40: *"Found it in Engineering: `default_job_minutes`
    is currently 60.0 (the default). Updating it now."* Nothing was being
    updated - a card was waiting for him to press "Do it". The prompt now
    says so, and the card says it too, in words the model cannot overrule."""
    assert '"Do it"' in agent.SYSTEM
    assert "never \"updating it now\"" in agent.SYSTEM
    assert "A proposal is not a change." in agent.SYSTEM
    card = (WEB / "assist.js").read_text(encoding="utf-8")
    assert "Nothing has changed yet." in card


def test_the_prompt_says_to_read_the_plant_again_before_saying_what_is_recorded():
    """The second half of 2026-09-26: he said he had changed it, and the
    assistant answered *"I don't see any change recorded"* having made no tool
    call at all."""
    assert "read the plant again before you answer" in agent.SYSTEM
    assert "no change is recorded" in agent.SYSTEM
    assert "setting_changes" in agent.SYSTEM


def test_the_prompt_says_a_part_of_a_list_is_not_the_whole_plant():
    assert "truncated" in agent.SYSTEM and "showing" in agent.SYSTEM
    assert "Never answer that the plant has no such thing" in agent.SYSTEM


def test_reading_what_was_recorded_needs_no_capability_beyond_seeing_the_plant():
    """`setting_changes` is a read, and a read a person cannot make is a
    question they cannot answer about their own plant."""
    viewer = {t["name"]: t for t in agent.catalogue({"plant.read"})}
    assert "setting_changes" in viewer and viewer["setting_changes"]["write"] is False
    assert "plant_settings" in viewer
    for hidden in ("plant", "dry_run", "on_behalf_of", "client_ref"):
        assert hidden not in viewer["setting_changes"]["input_schema"]["properties"]


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
        would = (f"set {args.get('key')} to {args.get('value')} in the "
                 f"{args.get('domain')} configuration")
        if name == "write_plant_setting" and dry_run:
            return {"dry_run": True, "would": would,
                    "request": {"method": "PATCH",
                                "path": f"/dashboard/config/{args['domain']}/settings/{args['key']}",
                                "body": {"value": args["value"]}}}
        if name == "write_plant_setting":
            return {"done": would, "audited_as": "AGENT", "on_behalf_of": on_behalf_of,
                    "response": {"name": args["key"], "value": args["value"],
                                 "is_default": False, "set_by": "AGENT"}}
        if name == "setting_changes":
            return {"plant": plant, "changes": [
                {"when": "2026-09-26T10:12:40Z", "who": "ADMIN", "as": "ADMIN",
                 "setting": "[process] default_report_hours",
                 "name": "default_report_hours", "from": None, "to": "10.0"}],
                "total": 1, "showing": 1, "audit_rows_read": 1}
        if name == "plant_settings":
            return {"plant": plant, "find": args.get("find"), "total": 1, "showing": 1,
                    "settings": [{"name": "default_report_hours",
                                  "label": "The default reporting window",
                                  "value": "10.0", "default": "8.0", "is_default": False,
                                  "set_by": "ADMIN", "kind": "float",
                                  "section": "default_report_hours",
                                  "needs": "process.define", "agent_may_write": True,
                                  "domain": "engineering"}]}
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


def test_when_the_person_says_they_changed_it_the_answer_comes_from_a_fresh_read(scripted):
    """Scott, 2026-09-26: he changed `default_report_hours` to 10 on the
    Configuration page, said so, and asked for it back. The assistant made no
    tool call and answered *"I don't see any change recorded"* out of a
    conversation whose only read had been cut in half.

    The tool that reads the record exists now and the prompt says to use it.
    What is pinned here is the rest of it: the read runs free, it runs before
    anything is proposed, and what it returns is what the person is told."""
    script, calls = scripted
    script += [
        response(block_tool("t1", "setting_changes", key="default_report_hours"),
                 stop="tool_use"),
        response(block_text("ADMIN set the default reporting window to 10.0 at 10:12 UTC; "
                            "it was on the product's 8.0 before that.")),
    ]
    sess = agent.open_session("SCOTT", "bottling", {"plant.read", "process.define"})
    out = agent.message(sess, "I just changed it to 10 - put it back to 8")

    assert [c["name"] for c in calls] == ["setting_changes"]
    assert calls[0]["dry_run"] is None               # a read: no permission asked
    assert out["kind"] == "reply" and "10.0" in out["say"]
    # The model answered with the record in front of it, not from memory.
    handed_back = json.loads(sess.history[2]["content"][0]["content"])
    assert handed_back["changes"][0]["to"] == "10.0"
    assert out["transcript"][0]["tool"] == "setting_changes"


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


def test_the_month_is_counted_in_tokens_as_well_as_in_dollars(scripted):
    """The dollars are this repository's price list; the tokens are the bill's
    own unit. A faithfulness run reports both, because a price change would
    otherwise read as a change in how much the assistant does."""
    script, _ = scripted
    script += [response(block_text("Hello.")), response(block_text("Hello again."))]
    sess = agent.open_session("ADMIN", "bottling", {"plant.read"})
    agent.message(sess, "hi")
    agent.message(sess, "hi again")

    tokens = agent.tokens_this_month()
    assert tokens == {"input": 2000, "output": 100, "cache_read": 1600, "cache_write": 0}
    assert agent.tokens_this_month(now=datetime(2000, 1, 1, tzinfo=UTC)) == \
        dict.fromkeys(agent.TOKEN_KINDS, 0)
    assert agent.status()["tokens_this_month"] == tokens


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

def page_file(page: str) -> str:
    """The file behind a step's page. A step may carry a query string - which
    setting, which machine - and that is not part of which file serves it."""
    return PAGE_FILES[page.split("?")[0]]


def anchors_on(page: str) -> set[str]:
    """Every anchor a page actually has: the ones in its markup, and the ones
    its own scripts put on controls they build. A page whose rows are drawn
    from an API has no anchor in its HTML at all, and a check that only read
    the HTML would call every one of those steps broken."""
    html = (WEB / page_file(page)).read_text(encoding="utf-8")
    found = set(re.findall(r'data-assist="([^"]+)"', html))
    for script in re.findall(r'<script src="/static/([^"]+\.js)"', html):
        source = (WEB / script).read_text(encoding="utf-8")
        found |= set(re.findall(r'data-assist="([^"]+)"', source))
        found |= set(re.findall(r'dataset\.assist\s*=\s*"([^"]+)"', source))
    return found


@pytest.mark.parametrize("tool", sorted(assistant.SURFACES))
def test_every_surface_step_points_at_a_control_that_exists(tool):
    surface = assistant.SURFACES[tool]
    for i, step in enumerate([*surface["steps"], surface["evidence"]], 1):
        assert step["page"].split("?")[0] in PAGE_FILES, (
            f"{tool} step {i}: unknown page {step['page']}")
        assert step["anchor"] in anchors_on(step["page"]), (
            f"{tool} step {i} points at data-assist={step['anchor']!r} which "
            f"{page_file(step['page'])} does not have")


@pytest.mark.parametrize("tool", sorted(assistant.SURFACES))
def test_every_page_a_walk_crosses_onto_carries_the_assistant(tool):
    """A walk that spans screens is resumed by assist.js on arrival. A page
    that does not load it would take the person there and stop."""
    surface = assistant.SURFACES[tool]
    for step in [*surface["steps"], surface["evidence"]]:
        html = (WEB / page_file(step["page"])).read_text(encoding="utf-8")
        assert "/static/assist.js" in html, (
            f"{tool} walks to {step['page']}, which does not load assist.js")


@pytest.mark.parametrize("tool", sorted(assistant.SURFACES))
def test_every_surface_is_a_real_write_tool_with_a_known_capability(tool):
    names = {t.name for t in agent.registry_tools()}
    assert tool in names
    needs = assistant.SURFACES[tool]["needs"]
    if needs is None:
        # A surface whose capability is decided per call says so in one place
        # and gives its reason there, and the one dict that cannot hold it does
        # not pretend to: naming any single capability in NEEDS would be naming
        # the wrong one for every other domain.
        assert agent.PER_CALL_NEEDS.get(tool)
        assert tool not in agent.NEEDS
        assert agent.needs_any(tool) <= set(caps.CAPABILITIES)
        assert agent.needs_any(tool), f"{tool} could be gated by nothing at all"
    else:
        assert needs in caps.CAPABILITIES
        assert agent.NEEDS.get(tool) == needs


def test_a_setting_is_offered_to_whoever_may_write_one_and_to_nobody_else():
    """`write_plant_setting` is in no capability list, because there is no one
    capability: it is the owning section's `define`, per key. So the offer is
    "you hold at least one of them" and the API decides the rest - and somebody
    who holds none of them never sees the tool, rather than being handed one
    that always refuses."""
    from fsmes.services import plant_settings

    assert "write_plant_setting" not in agent.NEEDS
    assert agent.PER_CALL_NEEDS["write_plant_setting"]
    # Read the same registry `needs_any` reads, not a fixed enumeration: this
    # test would otherwise need editing every time another domain's settings
    # go live, which is exactly the retrofit rule three exists to prevent.
    live_defines = {section.define for section in plant_settings.live_sections()
                    if section.define}
    assert agent.needs_any("write_plant_setting") == live_defines
    assert "quality.define" in live_defines
    assert agent.needs_any("record_check") is None

    engineer = {t["name"] for t in agent.catalogue({"plant.read", "quality.define"})}
    assert "write_plant_setting" in engineer
    # Reading is free, so the read tool is there either way.
    viewer = {t["name"] for t in agent.catalogue({"plant.read"})}
    assert "plant_settings" in viewer and "write_plant_setting" not in viewer


def test_a_settings_walk_points_at_the_workspace_it_was_asked_about():
    """One authored walk, every domain. The page carries the workspace and the
    key it was asked about, and the words carry the section the key is listed
    under - read from the registry the API reads, not written out per domain."""
    s = assistant.surface_for("write_plant_setting",
                              {"domain": "quality", "key": "cpk_capable", "value": "1.4"})
    assert s["steps"][0]["page"] == "/dashboard/config/quality?setting=cpk_capable"
    assert s["steps"][0]["anchor"] == "setting-in-focus"
    assert s["steps"][0]["fill"] == {"value": "1.4"}
    assert "Where a process is called capable" in s["steps"][0]["body"]
    assert "[quality] cpk_capable" in s["steps"][0]["body"]
    assert "quality.define" in s["steps"][1]["body"]
    assert s["evidence"]["page"] == "/dashboard/config/quality?setting=cpk_capable"
    # A key this version does not have still walks somewhere readable rather
    # than raising on the way to the refusal the API is about to give.
    blank = assistant.surface_for("write_plant_setting",
                                  {"domain": "quality", "key": "not_a_key", "value": "1"})
    assert blank["steps"][0]["page"] == "/dashboard/config/quality?setting=not_a_key"


def test_a_settings_walk_says_whether_this_person_may_press_save():
    """Scott, 2026-09-25, as ADMIN: the walk's last step said "Saving needs
    process.define." and the step before it had just failed to find the box, and
    together he read the two as a refusal of permission he had not been refused.
    A card that is built for a named person knows their capabilities - the same
    set the offer was filtered on - so it says which side of the gate they are
    on instead of leaving them to wonder."""
    args = {"domain": "engineering", "key": "default_job_minutes", "value": "55"}

    held = assistant.surface_for("write_plant_setting", args,
                                 {"plant.read", "process.define"})
    assert "Saving needs process.define - you hold it" in held["steps"][1]["body"]
    assert "pressing Save is yours to do" in held["steps"][1]["body"]

    without = assistant.surface_for("write_plant_setting", args, {"plant.read"})
    assert "which you do not hold" in without["steps"][1]["body"]
    # And it says what would change that, rather than stopping at the refusal.
    assert "a plant administrator can grant it" in without["steps"][1]["body"]

    # Nobody named: the words claim nothing about anybody, which is the shape
    # every caller before this one relied on.
    anonymous = assistant.surface_for("write_plant_setting", args)
    assert anonymous["steps"][1]["body"].endswith("Saving needs process.define.")


def test_a_settings_step_carries_the_capability_its_control_is_drawn_for():
    """Both controls a settings walk points at are only drawn for somebody
    holding the section's `define` capability - the page offers a reader the
    value and no input. The step says which capability that is, so a screen that
    cannot find the control can tell "not drawn yet" from "not yours to see"
    rather than asserting one of them."""
    s = assistant.surface_for("write_plant_setting",
                              {"domain": "engineering", "key": "default_job_minutes",
                               "value": "55"})
    assert [step["needs"] for step in s["steps"]] == ["process.define"] * 2
    assert s["evidence"]["needs"] == "process.define"
    # A key this version does not have names no capability rather than a wrong
    # one, and an empty string is what the screen reads as "unknown".
    blank = assistant.surface_for("write_plant_setting",
                                  {"domain": "engineering", "key": "not_a_key", "value": "1"})
    assert blank["steps"][0]["needs"] == ""


def test_a_setting_change_is_a_card_a_person_clicks_not_a_draft_to_sign(scripted):
    """The same card `propose_adjustment` gets, for the same reason: the value is
    in force the moment it is written, so it waits for a person rather than for
    an approver. "Show me" has steps to walk, "Do it" runs it once on their
    behalf, and the evidence afterwards is the box on the Configuration page."""
    script, calls = scripted
    script += [
        response(block_tool("t1", "plant_settings", domain="quality"), stop="tool_use"),
        response(block_text("It is 1.33, the product's default. I will set it to 1.4."),
                 block_tool("t2", "write_plant_setting", domain="quality",
                            key="cpk_capable", value="1.4"),
                 stop="tool_use"),
        response(block_text("Set: capable is now Cpk 1.4, in force now.")),
    ]
    sess = agent.open_session("R.OKON", "bottling", {"plant.read", "quality.define"})
    out = agent.message(sess, "raise the capable Cpk bar to 1.4", name="R Okon", role="supervisor")

    # The read ran free; the write was previewed and stopped.
    assert out["kind"] == "proposals"
    assert [c["dry_run"] for c in calls] == [None, True]
    proposal = out["proposals"][0]
    assert proposal["preview"]["request"]["method"] == "PATCH"
    assert proposal["preview"]["request"]["body"] == {"value": "1.4"}
    # Both buttons: "Do it" is always there, "Show me" needs steps to walk.
    assert proposal["surface"]["steps"] and proposal["surface"]["evidence"]

    done = agent.confirm(sess, proposal["id"])
    assert done["kind"] == "reply"
    written = [c for c in calls if c["dry_run"] is False]
    assert len(written) == 1 and written[0]["name"] == "write_plant_setting"
    assert written[0]["on_behalf_of"] == "R.OKON"
    assert written[0]["client_ref"] == proposal["id"]
    evidence = done["done"][0]["evidence"]
    assert evidence["page"] == "/dashboard/config/quality?setting=cpk_capable"


def test_surface_fills_come_from_the_proposal():
    s = assistant.surface_for("record_check", {"material": "COLA-500", "characteristic": "fill_weight", "value": 495})
    assert s["steps"][1]["fill"] == {"value": "COLA-500::fill_weight"}
    assert s["steps"][2]["fill"] == {"value": "495"}
    assert "495" in s["steps"][2]["body"]
    assert assistant.surface_for("machines", {}) is None


# ------------------------------------------- one brain, and a history it can use
#
# Scott, 2026-09-26, bottling, ADMIN: thirty-odd turns, five model calls. A
# message typed over an open proposal left the conversation in a shape the
# Messages API refuses, every later message in it failed the same way, and the
# panel quietly handed the rest of the afternoon to a model with no tools. The
# four tests below are the three mechanisms and the record that would have
# shown them.

def _shapes_from(monkeypatch):
    """Records the history shape the model was handed, call by call."""
    seen: list[str] = []
    scripted_model = agent._call_model

    def watching(sess):
        seen.append(agent.history_shape(sess))
        return scripted_model(sess)

    monkeypatch.setattr(agent, "_call_model", watching)
    return seen


def test_a_message_over_an_open_proposal_is_a_history_the_api_will_take(scripted, monkeypatch):
    """The invariant, pinned where it broke: a `tool_use` is followed by its
    `tool_result`, never by the person's next sentence.

    Reproduced by the steward at 15:40 on 2026-09-26 — "Change nc_code_prefix
    to CR" got a proposal, "set it to 4" came back *The cloud brain did not
    answer (BadRequestError)*, and so did every message after it, in 0.3 s,
    with no model call and nothing in any log.
    """
    script, _calls = scripted
    script += [
        response(block_text("I will set it."),
                 block_tool("t1", "write_plant_setting", domain="quality",
                            key="nc_code_prefix", value="CR"),
                 stop="tool_use"),
        response(block_text("Four it is — say the word and I will propose that instead.")),
    ]
    shapes = _shapes_from(monkeypatch)
    sess = agent.open_session("ADMIN", "bottling", {"plant.read", "quality.define"})
    assert agent.message(sess, "Change nc_code_prefix to CR")["kind"] == "proposals"

    out = agent.message(sess, "set it to 4")
    assert out["kind"] == "reply", out
    # The second call happened at all, and what it was handed is well-formed.
    assert len(shapes) == 2, "the second message never reached the model"
    assert "assistant[text,tool_use] user[tool_result] user[text]" in shapes[1], shapes[1]
    # The decline is what the model was told, in the person's own words.
    answer = json.loads(sess.history[2]["content"][0]["content"])
    assert answer["declined"] == "the person moved on without confirming"


def test_a_conversation_already_broken_repairs_itself_on_the_next_message(scripted, monkeypatch):
    """The sessions that were live when this shipped, and any that get there
    another way. One unanswered `tool_use` is answered before the next call,
    so the next thing the person says works instead of failing forever."""
    script, _calls = scripted
    script += [response(block_text("It is CR."))]
    shapes = _shapes_from(monkeypatch)
    sess = agent.open_session("ADMIN", "bottling", {"plant.read", "quality.define"})
    # Exactly the shape `message()` used to leave behind.
    sess.history += [
        {"role": "user", "content": "Change nc_code_prefix to CR"},
        {"role": "assistant",
         "content": [block_tool("t9", "write_plant_setting", domain="quality",
                                key="nc_code_prefix", value="CR")]},
        {"role": "user", "content": "set it to 4"},
    ]

    out = agent.message(sess, "what is nc_code_prefix set to?")
    assert out["kind"] == "reply" and out["say"] == "It is CR."
    assert "assistant[tool_use] user[tool_result] user[text]" in shapes[0], shapes[0]


def test_a_model_error_is_said_plainly_logged_and_gone_by_the_next_message(scripted, caplog):
    """A failed turn is a failed turn. It says what happened in words a person
    can act on, it leaves a line somebody can find afterwards, and the session
    is still callable — `except Exception` returning the class name and
    dropping the rest is how this went unexplained for an afternoon."""
    script, _calls = scripted

    class Refused(Exception):
        status_code = 400

    def once_broken(sess):
        if script and script[0] == "boom":
            script.pop(0)
            raise Refused("messages.3: unexpected `tool_use` without `tool_result`")
        return script.pop(0)

    script += ["boom", response(block_text("Nc code prefix is CR."))]
    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as patch:
        patch.setattr(agent, "_call_model", once_broken)
        sess = agent.open_session("ADMIN", "bottling", {"plant.read"})
        with caplog.at_level("WARNING", logger="fsmes.services.agent"):
            bad = agent.message(sess, "what is nc_code_prefix set to?")
        assert bad["kind"] == "error" and bad["reason"] == "error"
        assert bad["say"] == ("The assistant hit an error on that one. Say it again "
                              "and I will try afresh.")
        # What was logged: the class, the API's own message, the session, and
        # the shape — never the key, the prompt, or a thing about the plant.
        line = caplog.text
        assert "Refused" in line and "unexpected `tool_use`" in line and sess.id in line
        assert "user[text]" in line and "nc_code_prefix" not in line

        # And it does not stick.
        good = agent.message(sess, "ask again")
        assert good["kind"] == "reply" and good["say"] == "Nc code prefix is CR."


def test_a_dropped_connection_is_tried_once_more_and_a_bad_request_is_not(scripted):
    """Retry what the line did to us, never what we asked for: a 400 retried
    is the same wrong question and the same bill, twice."""
    _script, _calls = scripted

    class Dropped(Exception):
        pass
    Dropped.__name__ = "APIConnectionError"

    class Refused(Exception):
        status_code = 400

    tries: list[str] = []

    def flaky(sess):
        tries.append("call")
        if len(tries) == 1:
            raise Dropped("the connection dropped")
        return response(block_text("Cpk capable is 1.33."))

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as patch:
        patch.setattr(agent, "_call_model", flaky)
        sess = agent.open_session("ADMIN", "bottling", {"plant.read"})
        out = agent.message(sess, "what is the capable cpk?")
        assert out["kind"] == "reply" and len(tries) == 2

        tries.clear()
        patch.setattr(agent, "_call_model",
                      lambda sess: (_ for _ in ()).throw(Refused("bad request")))
        out = agent.message(sess, "again")
        assert out["kind"] == "error" and len(tries) == 0


def test_being_switched_off_is_an_unavailable_that_says_off(scripted, monkeypatch):
    """The panel reads `reason`. Only the cases `available()` names — no key,
    budget spent, shadow mode — hand it over to the local model; an error is
    not one of them, and treating it as one is what put a model with no tools
    in front of fifteen requests to change things."""
    _script, _calls = scripted
    sess = agent.open_session("ADMIN", "bottling", {"plant.read"})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = agent.message(sess, "hello")
    assert out["kind"] == "unavailable" and out["reason"] == "off"
    assert "ANTHROPIC_API_KEY" in out["why"]


# ------------------------------------------------- "show me" is the model's call

def walks():
    """Two of this plant's own built-in walkthroughs, as the router reads them."""
    return assistant.visible_guides({"plant.read", "quality.record"})


def test_the_walk_me_tools_are_offered_beside_the_plants_own(scripted):
    sess = agent.open_session("ADMIN", "bottling", {"plant.read", "quality.record"},
                              guides=walks())
    by_name = sess.tool_by_name
    assert "guides" in by_name and "show_guide" in by_name
    assert by_name["show_guide"]["write"] is False
    # A plant with no walkthroughs is not offered a tool that can only fail.
    bare = agent.open_session("ADMIN", "bottling", {"plant.read"}, guides=[])
    assert "show_guide" not in bare.tool_by_name


def test_the_model_reads_the_catalogue_and_puts_a_walk_on_the_screen(scripted):
    """What Scott asked for three times. The model has the conversation in
    view, so "show me how to do it" is answered by the walk it picks — not by
    whichever guide a regex and a word count landed on."""
    script, _calls = scripted
    script += [
        response(block_tool("t1", "guides"), stop="tool_use"),
        response(block_text("Here is how to do it yourself."),
                 block_tool("t2", "show_guide", id="record-check"), stop="tool_use"),
    ]
    sess = agent.open_session("ADMIN", "bottling", {"plant.read", "quality.record"},
                              guides=walks())
    out = agent.message(sess, "show me how to record an inspection")

    assert out["kind"] == "guide" and out["guide"]["id"] == "record-check"
    assert out["say"] == "Here is how to do it yourself."
    catalogue = json.loads(sess.history[2]["content"][0]["content"])
    assert catalogue["total"] == len(walks()) and catalogue["total"] > 0
    assert {"record-check"} <= {g["id"] for g in catalogue["guides"]}
    # The walk's own tool_result is in the history: the conversation carries on.
    shown = json.loads(sess.history[-1]["content"][0]["content"])
    assert shown["shown"] is True and shown["guide"] == "record-check"


def test_a_walk_the_person_may_not_follow_is_a_refusal_the_model_can_read(scripted):
    script, _calls = scripted
    script += [
        response(block_tool("t1", "show_guide", id="no-such-walk"), stop="tool_use"),
        response(block_text("I do not have a walkthrough for that one.")),
    ]
    sess = agent.open_session("ADMIN", "bottling", {"plant.read", "quality.record"},
                              guides=walks())
    out = agent.message(sess, "show me how to fly the plant")
    assert out["kind"] == "reply"
    refusal = json.loads(sess.history[2]["content"][0]["content"])
    assert "no walkthrough" in refusal["error"] and refusal["available"]


def test_a_proposal_and_a_walk_in_one_round_shows_the_card_and_says_so(scripted):
    """The card is what the person is looking at, so the walk did not happen —
    and the model is told that rather than left believing it did."""
    script, _calls = scripted
    script += [
        response(block_tool("t1", "write_plant_setting", domain="quality",
                            key="nc_code_prefix", value="CR"),
                 block_tool("t2", "show_guide", id="record-check"), stop="tool_use"),
    ]
    sess = agent.open_session("ADMIN", "bottling", {"plant.read", "quality.define",
                                                   "quality.record"}, guides=walks())
    out = agent.message(sess, "change it and show me")
    assert out["kind"] == "proposals"
    assert json.loads(sess.results["t2"]["content"])["shown"] is False


# ------------------------------------------------------------- the turn record

def test_every_turn_is_written_down_the_way_the_bill_is(scripted, tmp_path, monkeypatch):
    """One line per turn: who, what kind of answer, which tools, which
    proposals and how they ended, the tokens and the dollars. The question
    nobody could answer on 2026-09-26 was *how many of those turns reached the
    model* — this is the file that answers it."""
    monkeypatch.setattr(agent, "TURN_FILE", tmp_path / "turns.jsonl")
    script, _calls = scripted
    script += [
        response(block_text("I will record it."),
                 block_tool("t1", "record_check", material="COLA-500",
                            characteristic="fill_weight", value=495.0),
                 stop="tool_use"),
        response(block_text("Recorded.")),
    ]
    sess = agent.open_session("ADMIN", "bottling", {"plant.read", "quality.record"})
    out = agent.message(sess, "record fill weight 495")
    agent.confirm(sess, out["proposals"][0]["id"])

    rows = agent.turn_rows(tmp_path / "turns.jsonl")
    assert len(rows) == 2, rows
    offered, done = rows
    assert offered["kind"] == "proposals" and offered["session"] == sess.id
    assert offered["user"] == "ADMIN" and offered["plant"] == "bottling"
    assert offered["tools"] == ["record_check"]
    assert offered["proposals"][0]["outcome"] == "open"
    assert offered["input"] == 1000 and offered["usd"] > 0
    assert done["kind"] == "reply"
    assert done["proposals"] == [{"id": out["proposals"][0]["id"],
                                  "tool": "record_check", "outcome": "confirmed"}]
    assert "error" not in offered


def test_a_failed_turn_names_its_error_in_the_record(scripted, tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "TURN_FILE", tmp_path / "turns.jsonl")

    class Refused(Exception):
        status_code = 400

    monkeypatch.setattr(agent, "_call_model",
                        lambda sess: (_ for _ in ()).throw(Refused("no")))
    sess = agent.open_session("ADMIN", "bottling", {"plant.read"})
    agent.message(sess, "anything")
    row = agent.turn_rows(tmp_path / "turns.jsonl")[-1]
    assert row["kind"] == "error" and row["error"] == "Refused"
    assert row["usd"] == 0 and row["input"] == 0


# ------------------------------------------------------------------ the api

def test_status_and_a_message_without_a_key(admin, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("MES_AGENT_BRAIN", "auto")
    status = admin.get("/assist/agent/status").json()
    assert status["available"] is False and status["cap_usd"] == 10
    out = admin.post("/assist/agent", json={"message": "what is running?"}).json()
    assert out["kind"] == "unavailable"


def test_how_do_i_still_gets_a_guide(admin, monkeypatch):
    monkeypatch.setattr(assistant, "_ask_model", lambda prompt, timeout=None, model=None: "record-check")
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


# ----------------------------------- the three sentences that never reached it

#: Scott's own words, 2026-09-26 ~14:55 CDT, mid-conversation about
#: `nc_code_prefix`, signed in as ADMIN at bottling. Each one matched
#: `SHOW_ME`, so each one was answered by `assistant.route()` before the agent
#: was asked: a walkthrough for "find the work instruction for a job", then
#: "record a quality inspection" twice. The model that held the proposal, the
#: surface and the two-step walk onto that very field never saw them.
SHOW_ME_SENTENCES = [
    "could you show me where?",
    "no I want to change the it to CR but I don't want you to do it, "
    "I want you to show me how to do it.",
    "no there should be a tool for you to show me how to do it on the "
    "quality configuration tool.",
]


@pytest.fixture()
def agent_on(monkeypatch):
    """The cloud brain, switched on and scripted, behind the real endpoint."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MES_AGENT_BRAIN", "claude")
    monkeypatch.setattr(agent, "sdk_installed", lambda: True)
    script: list = []
    asked: list[str] = []

    def fake_model(sess):
        asked.append(agent.history_shape(sess))
        return script.pop(0)

    def fake_execute(name, args, *, plant, on_behalf_of=None, dry_run=None, client_ref=None):
        would = f"set {args.get('key')} to {args.get('value')} in the quality configuration"
        return {"dry_run": True, "would": would,
                "request": {"method": "PATCH",
                            "path": f"/dashboard/config/{args.get('domain')}/settings/"
                                    f"{args.get('key')}",
                            "body": {"value": args.get("value")}}}

    monkeypatch.setattr(agent, "_call_model", fake_model)
    monkeypatch.setattr(agent, "execute", fake_execute)
    return script, asked


def test_every_show_me_reaches_the_agent_while_it_is_the_brain(admin, agent_on, monkeypatch):
    """The whole handoff in one test. The guide router does not run while the
    agent is on, so all three sentences reach the model — and the last one is
    answered by the walk the model chose, onto the field under discussion."""
    script, asked = agent_on
    # The router would have intercepted every one of these on its own.
    assert all(assistant.wants_showing(sentence) for sentence in SHOW_ME_SENTENCES)

    script += [
        # "Change nc_code_prefix to CR" -> the proposal Scott was looking at.
        response(block_text("I will change it."),
                 block_tool("p1", "write_plant_setting", domain="quality",
                            key="nc_code_prefix", value="CR"), stop="tool_use"),
        response(block_text("The card is on screen; press Show me and I will point at the box.")),
        response(block_text("Of course — it is yours to type.")),
        response(block_text("Here it is on your own screen."),
                 block_tool("g1", "show_guide", id="record-check"), stop="tool_use"),
    ]

    opened = admin.post("/assist/agent", json={"message": "Change nc_code_prefix to CR"}).json()
    assert opened["kind"] == "proposals", opened
    session = opened["session"]

    replies = [admin.post("/assist/agent",
                          json={"message": sentence, "session": session}).json()
               for sentence in SHOW_ME_SENTENCES]

    # Not one of them was intercepted: four messages, four model calls.
    assert len(asked) == 4, asked
    assert [r["kind"] for r in replies] == ["reply", "reply", "guide"]
    assert replies[-1]["guide"]["id"] == "record-check"
    assert replies[-1]["guide"]["steps"], "the walk came back without its steps"
    assert all(r["session"] == session for r in replies)


def test_with_the_agent_off_the_guide_router_answers_exactly_as_it_did(admin, monkeypatch):
    """The other brain's path is untouched: a plant with no key gets the
    regex, the local model and the lexical fallback, as it always has."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("MES_AGENT_BRAIN", "auto")
    monkeypatch.setattr(assistant, "_ask_model",
                        lambda prompt, timeout=None, model=None: "record-check")
    out = admin.post("/assist/agent", json={"message": SHOW_ME_SENTENCES[0]}).json()
    assert out["kind"] == "guide" and out["guide"]["id"] == "record-check"
