"""The analysis agent: a second kind beside the floor assistant.

Decision 0038 says an agent is an account with a role, a budget and a cadence.
This file holds that claim to the code for the first kind that is not the floor
assistant: its own account and role, a catalogue built out of what a tool is
*not*, a prompt that explores rather than proposes, a budget of its own,
`brain: analysis` on its trace rows, and off in shadow mode saying which brain
and why (answer 9 of `docs/design/agentic-harness.md`).

Every assertion here is keyed on identity rather than on a count that would have
to be edited: the catalogue is checked against the tool registry, the role
against `capabilities.NEEDS`, and the account against the list the seeders read.
A test that said "53 tools" would go red on the day somebody adds a read, which
is the day it should stay green.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from fsmes import mcp_server
from fsmes import plant as plants
from fsmes.domain import BRAINS
from fsmes.services import agent
from fsmes.services import capabilities as caps

#: What the `analyst` role grants, written out rather than read from the role, so
#: that widening the role fails a test instead of widening this file with it.
ANALYST = {"plant.read", "audit.read"}


def _turn(*blocks, stop="end_turn"):
    usage = SimpleNamespace(input_tokens=1000, output_tokens=50,
                            cache_read_input_tokens=800, cache_creation_input_tokens=0)
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=usage)


def _said(text):
    return _turn(SimpleNamespace(type="text", text=text))


def _wants(id_, tool, **args):
    return SimpleNamespace(type="tool_use", id=id_, name=tool, input=args)


@pytest.fixture()
def scripted(monkeypatch, tmp_path):
    """A model that follows a script, and tools that record what ran.

    The same arrangement `test_agent.py` uses, with the plant's own reads stood
    in for: what is under test here is which tools the loop will call at all.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MES_AGENT_BRAIN", "claude")
    monkeypatch.setenv("MES_ANALYSIS_BRAIN", "claude")
    monkeypatch.setattr(agent, "sdk_installed", lambda: True)
    monkeypatch.setattr(agent, "USAGE_FILE", tmp_path / "usage.jsonl")
    script: list = []
    calls: list[dict] = []

    def fake_model(sess):
        assert sess.history[-1]["role"] == "user"
        return script.pop(0)

    def fake_execute(name, args, *, plant, on_behalf_of=None, dry_run=None, client_ref=None):
        calls.append({"name": name, "args": args, "plant": plant,
                      "on_behalf_of": on_behalf_of, "dry_run": dry_run,
                      "client_ref": client_ref})
        return {"plant": plant, "line": args.get("line"), "coverage": 0.61,
                "unknown_seconds": 900, "total": 4, "showing": 4}

    monkeypatch.setattr(agent, "_call_model", fake_model)
    monkeypatch.setattr(agent, "execute", fake_execute)
    return script, calls


def props(tool) -> dict:
    return (tool.input_schema or {}).get("properties") or {}


def registry() -> dict:
    return {tool.name: tool for tool in agent.registry_tools()}


# ------------------------------------------------- the catalogue, by exclusion

def test_the_analysis_catalogue_holds_no_tool_that_could_change_the_plant():
    """The one claim the whole kind rests on, checked against the registry and
    not against the function that built the catalogue.

    Three independent marks of a write, and none of them may appear: the
    `dry_run` parameter that makes a tool a write in this product, the
    `on_behalf_of` that says a person is being acted for, and a name in `NEEDS`
    or `PER_CALL_NEEDS` - the list of what each write is gated on, which
    `assist_coverage.py` derives a second way from the helper each tool's body
    calls.
    """
    offered = agent.catalogue(ANALYST, for_kind=agent.ANALYSIS)
    assert offered, "the analysis kind was offered no tools at all"
    known = registry()
    for tool in offered:
        raw = props(known[tool["name"]])
        assert tool["write"] is False, tool["name"]
        assert "dry_run" not in raw, tool["name"]
        assert "on_behalf_of" not in raw, tool["name"]
        assert "client_ref" not in raw, tool["name"]
        assert tool["name"] not in agent.NEEDS, tool["name"]
        assert tool["name"] not in agent.PER_CALL_NEEDS, tool["name"]


def test_nothing_in_the_catalogue_sends_a_write_to_any_route_of_this_product():
    """The second, independent definition of a write, and the reason the claim is
    checkable rather than asserted.

    `assist_coverage.tool_writes()` does not look at a schema at all: it parses
    the tool modules and reads which helper each tool's body calls and with what
    verb, so it would catch a tool that changed the plant while somehow carrying
    no `dry_run`. The two derivations agree on all 47 names today; what this
    pins is that neither of them ever meets the analysis catalogue.
    """
    from fsmes import assist_coverage

    by_source = {write.tool for write in assist_coverage.tool_writes()}
    assert by_source, "the source scan found no writes at all, so it proves nothing"
    offered = {t["name"] for t in agent.catalogue(ANALYST, for_kind=agent.ANALYSIS)}
    assert not by_source & offered
    # And the two ways of naming a write agree, which is what makes either of
    # them worth trusting.
    by_schema = {t.name for t in agent.registry_tools() if "dry_run" in props(t)}
    assert by_source == by_schema


def test_the_analysis_catalogue_is_every_read_tool_and_says_how_many_of_how_many():
    """"Full MCP" means every read tool, so a read must not be missing either -
    a catalogue narrowed by hand would be the thing this kind exists not to be.

    The two it does not hold are the two `catalogue()` has always dropped for
    every kind: a tool that takes no `plant` is not about a plant, and
    `list_plants` is the fleet's business rather than one plant's.
    """
    every = [t for t in agent.registry_tools() if t.name not in agent.HIDDEN]
    reads = {t.name for t in every if "dry_run" not in props(t) and "plant" in props(t)}
    offered = {t["name"] for t in agent.catalogue(ANALYST, for_kind=agent.ANALYSIS)}
    assert offered == reads
    # And the note the model is handed accounts for all of them, rather than
    # naming its own and leaving the rest unsaid (house rule 2).
    note = agent.no_write_note(agent.kind_named(agent.ANALYSIS))
    assert f"{len(offered)} of this plant's {len(every)} tools" in note
    writes = [t for t in every if "dry_run" in props(t)]
    assert f"The {len(writes)} that change anything are not in your catalogue" in note


def test_the_floor_assistant_still_holds_the_write_tools_its_person_may_use():
    """The exclusion is the analysis kind's and nobody else's."""
    floor = {t["name"]: t for t in agent.catalogue({"plant.read", "quality.record"})}
    assert floor["record_check"]["write"] is True
    analysis = {t["name"] for t in agent.catalogue({"plant.read", "quality.record"},
                                                   for_kind=agent.ANALYSIS)}
    assert "record_check" not in analysis


def test_the_analysis_agent_is_offered_no_walkthrough_because_a_walk_ends_in_a_change():
    """A walk puts somebody on the real form with the values in it and asks them
    to press the button. That is a change made by another route, so a kind that
    may not propose one is not offered the two walk-me tools either; its answer
    to "show me how" is the assistant in the panel."""
    walks = [{"id": "record-a-check", "title": "Record a check", "steps": [{"anchor": "a"}]}]
    floor = agent.open_session("JO", "bottling", ANALYST, guides=walks)
    assert {t["name"] for t in floor.tools} >= set(agent.GUIDE_TOOLS)

    sess = agent.open_session("JO", "bottling", ANALYST, kind=agent.ANALYSIS, guides=walks)
    assert not {t["name"] for t in sess.tools} & set(agent.GUIDE_TOOLS)
    assert sess.guides == []


# --------------------------------------------------- the account and the role

def test_the_analysis_kind_names_an_account_and_a_role_the_product_ships():
    kind = agent.kind_named(agent.ANALYSIS)
    assert kind.account == "ANALYST" and kind.role == "analyst"
    seeded = {code: role for code, _name, _password, role in plants.LAB_USERS}
    assert seeded[kind.account] == kind.role
    assert kind.account in mcp_server.ACCOUNTS
    assert set(caps.BUILTIN_ROLES[kind.role]["capabilities"]) == ANALYST


def test_the_analysts_role_holds_nothing_that_gates_a_write_anywhere():
    """Keyed on the product's own list of what each write tool needs, so a
    capability that starts gating a write tomorrow makes this fail rather than
    quietly widening what the analyst can be asked to do."""
    granted = set(caps.BUILTIN_ROLES["analyst"]["capabilities"])
    assert not granted & set(agent.NEEDS.values())
    assert not granted & (agent.needs_any("write_plant_setting") or set())
    assert not [c for c in granted if c.endswith((".approve", ".define", ".write"))]


def test_the_analysts_password_comes_from_its_own_key_and_the_lab_default_is_flagged():
    """The same shape the agent's has: an environment key, a lab default so a
    laptop demo needs no setup, and a line in the log when an installation is
    still running on it."""
    from fsmes.api import app as api_app

    assert mcp_server.ACCOUNTS["ANALYST"] == "FSMES_ANALYST_PASSWORD"
    source = api_app.__file__
    with open(source, encoding="utf-8") as fh:
        said = fh.read()
    assert "FSMES_ANALYST_PASSWORD (lab default)" in said


def test_an_account_can_hold_a_role_this_release_added_on_a_plant_built_before_it(
        session):
    """`fsmes plant <name> migrate` creates any lab account a plant has not got,
    by shelling out to `fsmes add-user` - and that is the one path that never
    goes through the app's start-up, where the roles the product ships are topped
    up. On a plant whose database predates this release, creating ANALYST would
    have been refused for want of a role row it was about to be given, and
    `ensure_lab_users` only reports the ones that worked: the account would have
    appeared on the *second* migrate and nowhere in between.
    """
    from fsmes.domain import Role
    from fsmes.services import auth

    session.execute(Role.__table__.delete().where(Role.code == "analyst"))
    session.flush()
    person = auth.create_user(session, code="ANALYST", name="Plant Analyst",
                              password="a-long-enough-password", role="analyst")
    assert person.role == "analyst"
    assert session.scalar(select(Role).where(Role.code == "analyst")) is not None


# ------------------------------------------------ what its reads reach as

def test_its_reads_reach_the_plant_as_its_own_account(make_client, session, monkeypatch):
    """The second gate, and the one that does not depend on the catalogue being
    right: the tools reach a plant over its own HTTP API, and an analysis
    conversation signs in as ANALYST. A read goes through; the write the
    catalogue never offered is refused by the plant itself.

    One client per account, each with its own cookies, because `_call` retries a
    401 by signing in - so a shared client would have sent this read out as
    whichever account signed in last, which on a real plant is AGENT.
    """
    from fsmes.services import auth

    auth.create_user(session, code=mcp_server.AGENT_USER, name="Plant Agent",
                     password=mcp_server.AGENT_PASSWORD, role="agent")
    auth.create_user(session, code=mcp_server.ANALYST_USER, name="Plant Analyst",
                     password=mcp_server.ANALYST_PASSWORD, role="analyst")
    session.flush()
    monkeypatch.setattr(mcp_server, "_clients",
                        mcp_server.wire_clients("testplant", make_client))
    monkeypatch.setattr(mcp_server, "_registry",
                        lambda: {"testplant": {"api_port": 0, "label": "Test"}})

    with mcp_server.acting_as("ANALYST"):
        read = mcp_server.machines("testplant")
        assert "error" not in read, read
        refused = mcp_server.set_machine_state("testplant", equipment="MIX01",
                                               state="running", dry_run=False)
    assert "error" in refused and "403" in refused["error"], refused

    # The same write, as the account the floor assistant uses, is allowed - so
    # what refused it was the analyst's role and not a broken plant.
    with mcp_server.acting_as(mcp_server.AGENT_USER):
        allowed = mcp_server.set_machine_state("testplant", equipment="MIX01",
                                               state="running", dry_run=False)
    assert "error" not in allowed, allowed


def test_an_account_the_tools_do_not_know_is_refused_rather_than_defaulted():
    """Falling back to AGENT is the one outcome two accounts exist to prevent."""
    with pytest.raises(KeyError, match="no account"), mcp_server.acting_as("PRESIDENT"):
        pass


def test_each_account_keeps_its_own_client_for_a_plant():
    assert (mcp_server.client_key("bottling", "AGENT")
            != mcp_server.client_key("bottling", "ANALYST"))
    made = mcp_server.wire_clients("bottling", lambda: SimpleNamespace())
    assert set(made) == {mcp_server.client_key("bottling", code)
                         for code in mcp_server.ACCOUNTS}


# ----------------------------------------------------------------- the prompt

def test_its_prompt_says_it_explores_explains_and_changes_nothing():
    said = agent.system_for(agent.ANALYSIS)
    assert said.startswith("You are the analysis agent inside FactorySemantics MES")
    assert "You explore and you explain." in said
    assert "You change nothing and you recommend nothing." in said
    assert "a recommendation is a proposal" in said
    # Asked for a change, it names who can rather than saying there is no tool.
    assert "the assistant in the panel on any screen can propose that change" in said
    # The three honesty rules this product would otherwise have to hope for.
    assert "Every figure carries its coverage." in said
    assert "Unknown is an answer; zero is not." in said
    assert "never work an OEE" in said
    assert "A list is only what it says it is." in said
    # And it is not the floor assistant's prompt with a word changed.
    assert said != agent.SYSTEM
    assert "Do it" not in said


def test_the_charts_are_left_to_the_tab_in_one_named_place():
    """`analysis-in-the-ai-tab` writes the two sentences about what an
    exploration draws. Until it does, the prompt promises words and numbers
    rather than a picture - and the place to replace has a name, so nobody has
    to read the prompt looking for it."""
    assert agent.ANALYSIS_CHARTS in agent.system_for(agent.ANALYSIS)
    assert "not built yet" in agent.ANALYSIS_CHARTS
    assert "a picture is not yours to promise" in agent.ANALYSIS_CHARTS


# ------------------------------------------------- a conversation of its own

def test_a_conversation_belongs_to_one_agent_for_its_whole_life():
    """The tools, the prompt and the budget in a conversation are the kind's, so
    a message naming the other kind opens a new conversation rather than
    inheriting this one's."""
    sess = agent.open_session("JO", "bottling", ANALYST, kind=agent.ANALYSIS)
    assert agent.get_session(sess.id, "JO") is sess
    assert agent.get_session(sess.id, "JO", agent.ANALYSIS) is sess
    assert agent.get_session(sess.id, "JO", agent.FLOOR) is None
    assert agent.get_session(sess.id, "SOMEBODY-ELSE", agent.ANALYSIS) is None


def test_an_agent_kind_this_release_does_not_have_is_refused_not_defaulted():
    assert agent.kind_named(None).name == agent.FLOOR
    assert agent.kind_named("").name == agent.FLOOR
    with pytest.raises(KeyError, match="no agent kind"):
        agent.kind_named("continuous-improvement")


def test_its_turns_are_traced_under_its_own_brain(scripted):
    """So the AI tab can list one kind's conversations apart from another's."""
    script, _calls = scripted
    script += [_said("Nothing was running on LINE1 for 20 minutes.")]
    sess = agent.open_session("JO", "bottling", ANALYST, kind=agent.ANALYSIS)
    out = agent.message(sess, "what happened on LINE1 this shift?")
    assert out["kind"] == "reply"
    assert sess.last_turn["brain"] == agent.ANALYSIS
    assert agent.ANALYSIS in BRAINS


def test_it_proposes_nothing_even_when_the_model_reaches_for_a_write(scripted):
    """The model asks to set a machine's state. The tool is not in its
    catalogue, so nothing is previewed, nothing is executed, no card reaches the
    person, and the model is told in a sentence it can use."""
    script, calls = scripted
    script += [
        _turn(_wants("t1", "set_machine_state", equipment="MIX01", state="running"),
              stop="tool_use"),
        _said("I only read - the assistant in the panel can set that for you."),
    ]
    sess = agent.open_session("JO", "bottling", ANALYST, kind=agent.ANALYSIS)
    out = agent.message(sess, "put MIX01 into running")

    assert out["kind"] == "reply"
    assert out.get("proposals", []) == []
    assert not sess.pending
    assert calls == []                                    # nothing reached the plant
    assert out["transcript"][0]["ok"] is False
    # And the sentence it is refused with says the agent is the reason, not the
    # person's role: "you do not hold equipment.state" would be true of an
    # analyst conversation and would read as though a supervisor could ask for it.
    said = out["transcript"][0]["summary"]
    # Both facts are inside the first 160 characters, which is what a tool
    # result is summarised to elsewhere in this loop and about as much as a
    # person reads of a line in the panel. The clause order is what puts them
    # there: what the agent cannot do, and who can, before the wording advice.
    assert "the analysis agent holds no tool that does" in said[:160]
    assert "the assistant in the panel can propose it" in said[:160]


# ------------------------------------------------------------- what it costs

def test_one_conversation_stops_at_its_own_budget_and_says_the_month_is_separate(
        scripted, monkeypatch):
    """A per-conversation cap is the bump and the month's cap is the wall. What
    it stops is one exploration that keeps calling - the failure mode a kind
    with every read tool and nobody watching each round has."""
    script, _calls = scripted
    monkeypatch.setenv("MES_ANALYSIS_CONVERSATION_USD", "0.000001")
    script += [
        _said("Watched 61% of the window; the rest is unknown."),
        _said("This one never gets asked."),
    ]
    sess = agent.open_session("JO", "bottling", ANALYST, kind=agent.ANALYSIS)
    assert sess.cap_usd == 0.000001
    first = agent.message(sess, "how did LINE1 run?")
    assert first["kind"] == "reply" and sess.spent_usd > 0

    second = agent.message(sess, "and the shift before it?")
    assert second["kind"] == "unavailable" and second["reason"] == "spent"
    assert "this conversation has spent" in second["why"]
    assert "the month's" in second["why"]
    assert len(script) == 1, "the model was called after the budget was spent"

    # A new conversation starts at nothing - that is what a per-conversation cap
    # is, and the month's cap is the one nothing gets around.
    fresh = agent.open_session("JO", "bottling", ANALYST, kind=agent.ANALYSIS)
    assert fresh.spent_usd == 0.0


def test_the_floor_assistant_keeps_the_budget_it_has_always_had(monkeypatch):
    """Uncapped per conversation, bounded by `[admin] agent_max_rounds` and by
    somebody standing at a machine waiting for it. Changing that was not this
    handoff's to do."""
    monkeypatch.delenv("MES_AGENT_CONVERSATION_USD", raising=False)
    assert agent.conversation_cap_usd(agent.FLOOR) == 0.0
    assert agent.conversation_cap_usd(agent.ANALYSIS) == 0.25
    monkeypatch.setenv("MES_ANALYSIS_CONVERSATION_USD", "not-a-number")
    assert agent.conversation_cap_usd(agent.ANALYSIS) == 0.25


def test_the_month_is_one_bill_whichever_kind_spent_it(monkeypatch, tmp_path):
    usage = tmp_path / "usage.jsonl"
    monkeypatch.setattr(agent, "USAGE_FILE", usage)
    monkeypatch.setenv("MES_AGENT_MONTHLY_USD", "0.01")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(agent, "sdk_installed", lambda: True)
    assert agent.available(agent.ANALYSIS)[0] is True
    agent.log_usage("bottling", "JO", "claude-sonnet-5",
                    {"input": 100_000, "output": 100_000})
    for kind in (agent.FLOOR, agent.ANALYSIS):
        on, why = agent.available(kind)
        assert on is False and "this month's budget is spent" in why


# ------------------------------------------------------- off, and saying so

def test_the_analysis_agent_is_off_in_shadow_mode_and_says_which_brain(monkeypatch):
    """Answer 9. There is no local analysis agent and answering worse was the
    option turned down, so a shadow plant's analysis agent is off and the
    sentence names it, the setting, and what to do about it."""
    from fsmes import config, shadow

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(agent, "sdk_installed", lambda: True)
    monkeypatch.setattr(shadow, "enabled", lambda settings=None: True)
    config.get_settings.cache_clear()

    on, why = agent.available(agent.ANALYSIS)
    assert on is False
    assert "shadow mode" in why and "the analysis agent is not used" in why
    assert shadow.SETTING in why
    # And the floor assistant says its own name in its own sentence.
    assert "the floor assistant is not used" in agent.available(agent.FLOOR)[1]
    config.get_settings.cache_clear()


def test_either_kind_can_be_switched_off_on_its_own(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(agent, "sdk_installed", lambda: True)
    monkeypatch.setenv("MES_ANALYSIS_BRAIN", "off")
    monkeypatch.setenv("MES_AGENT_BRAIN", "claude")

    on, why = agent.available(agent.ANALYSIS)
    assert on is False
    assert why == "the analysis agent is switched off (MES_ANALYSIS_BRAIN=off)"
    assert agent.available(agent.FLOOR)[0] is True


def test_a_conversation_with_an_agent_that_is_off_is_told_which_one(scripted, monkeypatch):
    _script, _calls = scripted
    monkeypatch.setenv("MES_ANALYSIS_BRAIN", "off")
    sess = agent.open_session("JO", "bottling", ANALYST, kind=agent.ANALYSIS)
    out = agent.message(sess, "how did LINE1 run?")
    assert out["kind"] == "unavailable" and out["reason"] == "off"
    assert "the analysis agent is switched off" in out["why"]
    # Still a row in the trace, so a plant with it switched off can see that
    # somebody asked - the absence is visible rather than silent.
    assert sess.last_turn["brain"] == agent.ANALYSIS
    assert sess.last_turn["kind"] == "unavailable"


# ------------------------------------------------------------ what it reports

def test_ai_status_over_ssh_names_both_agents_and_what_each_may_do(monkeypatch, tmp_path):
    """`fsmes ai-status` is what somebody on a plant's server reaches for, and
    until now it said "cloud brain" as though there were one agent. A kind can be
    off on its own - shadow mode turns the analysis agent off and leaves the
    floor assistant on the local model - and this is the only place to see that
    without a browser."""
    from typer.testing import CliRunner

    from fsmes.cli import app

    monkeypatch.setattr(agent, "USAGE_FILE", tmp_path / "usage.jsonl")
    monkeypatch.setenv("MES_LOCAL_AI", "0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MES_ANALYSIS_BRAIN", "off")
    monkeypatch.setattr(agent, "sdk_installed", lambda: True)

    said = CliRunner().invoke(app, ["ai-status"]).output
    assert f"{len(agent.KINDS)} kinds" in said
    assert "floor" in said and "reads and proposes" in said
    assert "analysis" in said and "reads only" in said
    assert "ANALYST/analyst" in said
    assert "$0.25 a conversation" in said
    assert "off because the analysis agent is switched off" in said


def test_status_says_the_same_of_every_kind_and_counts_them(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(agent, "sdk_installed", lambda: True)
    out = agent.status()
    assert out["kind"] == agent.FLOOR                     # the fields the panel has always read
    assert set(out["kinds"]) == set(agent.KINDS)
    assert out["total_kinds"] == len(out["kinds"])
    analysis = out["kinds"][agent.ANALYSIS]
    assert analysis["account"] == "ANALYST" and analysis["role"] == "analyst"
    assert analysis["writes"] is False
    assert analysis["conversation_cap_usd"] == 0.25
    assert agent.status(agent.ANALYSIS)["kind"] == agent.ANALYSIS
