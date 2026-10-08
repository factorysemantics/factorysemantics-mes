"""The plant's own record of what its AI was asked, did, and cost.

Scott, 2026-09-26, having had to paste a screenshot because nothing in the
plant remembered the conversation: *"These conversations should be traced
within the MES as well."* The turn log beside the bill answered "how many of
those thirty turns reached the model"; this table answers "what did it say,
what did it call, what did it propose, and what became of each" - on a screen,
to somebody who will never have a shell on that box.

Four claims, one test each where one will do.

1. A turn through the real endpoint lands in `ai_turns`, with the words on
   both sides and the tool calls the panel showed.
2. A confirmed proposal names the audit row it wrote, so the trace and the
   audit trail can be read side by side.
3. The trace never carries the system prompt or the key. This is the one that
   would make the table a liability, and it is checked against the real
   `SYSTEM` string rather than a paraphrase of it.
4. A turn past the plant's horizon is deleted as new ones are written, and
   `ai_trace_days = 0` means keep everything rather than keep nothing.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from fsmes.db import utcnow
from fsmes.domain import AiTurn, AuditLog
from fsmes.services import agent, ai_trace


def block_text(text):
    return SimpleNamespace(type="text", text=text)


def block_tool(id_, tool, **args):
    return SimpleNamespace(type="tool_use", id=id_, name=tool, input=args)


def response(*blocks, stop="end_turn"):
    usage = SimpleNamespace(input_tokens=1000, output_tokens=50,
                            cache_read_input_tokens=800, cache_creation_input_tokens=0)
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=usage)


@pytest.fixture()
def agent_on(monkeypatch):
    """The cloud brain, switched on and scripted, behind the real endpoint.

    `write_plant_setting` is the tool Scott's own two reports were about, and
    it is the one write in this product whose confirmed form goes through the
    plant's own API and lands in the audit trail without a fixture pretending
    it did.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MES_AGENT_BRAIN", "claude")
    monkeypatch.setattr(agent, "sdk_installed", lambda: True)
    script: list = []

    def fake_model(sess):
        return script.pop(0)

    monkeypatch.setattr(agent, "_call_model", fake_model)
    return script


@pytest.fixture()
def settings_agent(agent_on, monkeypatch):
    """The same, with the settings write faked either side of the decision."""
    def fake_execute(name, args, *, plant, on_behalf_of=None, dry_run=None, client_ref=None):
        would = f"set {args.get('key')} to {args.get('value')} in the quality configuration"
        if dry_run:
            return {"dry_run": True, "would": would,
                    "request": {"method": "PATCH", "path": "/x", "body": args}}
        return {"done": would, "response": {"value": args.get("value")}, "audited_as": "AGENT"}

    monkeypatch.setattr(agent, "execute", fake_execute)
    return agent_on


# --------------------------------------------------- 1. the turn is recorded

def test_a_turn_through_the_panel_is_written_into_the_plants_own_trace(
        admin, session, settings_agent):
    script = settings_agent
    script += [
        response(block_text("Let me look."),
                 block_tool("r1", "plant_settings", domain="quality"), stop="tool_use"),
        response(block_text("I will change it."),
                 block_tool("p1", "write_plant_setting", domain="quality",
                            key="nc_code_prefix", value="CR"), stop="tool_use"),
    ]
    out = admin.post("/assist/agent",
                     json={"message": "make the non-conformance prefix CR"}).json()
    assert out["kind"] == "proposals", out

    rows = session.scalars(select(AiTurn).order_by(AiTurn.id)).all()
    assert len(rows) == 1
    turn = rows[0]
    assert turn.session == out["session"]
    assert turn.brain == "floor"
    assert turn.person == "ADMIN"
    assert turn.kind == "proposals"
    assert turn.asked == "make the non-conformance prefix CR"
    # Both text blocks, in the order the model wrote them. The trace is the
    # plant's record of what its AI said, so it holds all of what it said:
    # before 2026-10-08 the first round's words were thrown away with the
    # round, here and on the screen alike.
    assert turn.said == "Let me look.\n\nI will change it."
    # The tool call the panel showed, with the sentence it showed beside it.
    assert [call["tool"] for call in turn.tools] == ["plant_settings"]
    assert turn.tools[0]["ok"] is True and turn.tools[0]["summary"]
    # The proposal, with the arguments the card put in front of the person.
    assert turn.proposals == [{"id": out["proposals"][0]["id"],
                               "tool": "write_plant_setting",
                               "args": {"domain": "quality", "key": "nc_code_prefix",
                                        "value": "CR"},
                               "outcome": "open"}]
    assert turn.input_tokens == 2000 and turn.usd > 0
    assert turn.error is None


def test_a_declined_proposal_and_a_failed_turn_are_both_in_the_record(
        admin, session, settings_agent):
    """Two of the three things Scott hit. A decline is an outcome, not an
    absence; a model error is a turn, not a silence - it was logged nowhere at
    all until PR #109 and had no screen until this one."""
    class Refused(Exception):
        status_code = 400

    script = settings_agent
    script += [
        response(block_text("I will change it."),
                 block_tool("p1", "write_plant_setting", domain="quality",
                            key="nc_code_prefix", value="CR"), stop="tool_use"),
        response(block_text("Left as it was.")),
    ]
    opened = admin.post("/assist/agent", json={"message": "prefix CR"}).json()
    admin.post("/assist/agent/decline",
               json={"session": opened["session"], "proposal": opened["proposals"][0]["id"]})

    def boom(sess):
        raise Refused("no")

    import fsmes.services.agent as agent_module
    agent_module._call_model = boom
    admin.post("/assist/agent",
               json={"message": "and again", "session": opened["session"]})

    rows = session.scalars(select(AiTurn).order_by(AiTurn.id)).all()
    kinds = [row.kind for row in rows]
    assert kinds == ["proposals", "reply", "error"], kinds
    assert rows[1].proposals[0]["outcome"] == "declined"
    assert rows[2].error == "Refused"
    # The class, never the message: the plant's own log has the detail.
    assert rows[2].said != "no" and "Refused" not in rows[2].said


# ------------------------------------- 2. the trace and the audit trail agree

def test_a_proposal_done_through_do_it_names_the_audit_row_it_wrote(
        admin, session, agent_on, monkeypatch):
    """Definition of done, item 3. The trace says what was proposed, the audit
    trail says what changed, and the page can put them side by side because
    the proposal carries the entity the audit row named.

    The confirmed write is stood in for, because the real one leaves this
    process over HTTP and there is no plant on a port in a unit test - but the
    audit row it leaves behind is written exactly as the endpoint writes one,
    by `audit.record` as AGENT on behalf of the person. That is the whole of
    what the link reads, so standing in for the HTTP hop costs the test
    nothing. The end-to-end path is walked in the browser test.
    """
    from fsmes.api.deps import Actor
    from fsmes.services import audit as audit_service

    def fake_execute(name, args, *, plant, on_behalf_of=None, dry_run=None, client_ref=None):
        would = f"set {args.get('key')} to {args.get('value')}"
        if dry_run:
            return {"dry_run": True, "would": would,
                    "request": {"method": "PATCH", "path": "/x", "body": args}}
        actor = Actor("AGENT")
        actor.on_behalf_of = on_behalf_of
        audit_service.record(session, actor=actor, action="plant_setting.set",
                             entity_type="plant_setting",
                             entity_id=f"[quality] {args.get('key')}",
                             before={"value": "NC"}, after={"value": args.get("value")})
        session.flush()
        return {"done": would, "response": {"value": args.get("value")}, "audited_as": "AGENT"}

    monkeypatch.setattr(agent, "execute", fake_execute)
    script = agent_on
    script += [
        response(block_text("I will set it."),
                 block_tool("p1", "write_plant_setting", domain="quality",
                            key="nc_code_prefix", value="CR"), stop="tool_use"),
        response(block_text("Done.")),
    ]
    opened = admin.post("/assist/agent", json={"message": "prefix CR"}).json()
    assert opened["kind"] == "proposals", opened
    done = admin.post("/assist/agent/confirm",
                      json={"session": opened["session"],
                            "proposal": opened["proposals"][0]["id"]})
    assert done.status_code == 200, done.text

    entry = session.scalars(
        select(AuditLog).where(AuditLog.entity_type == "plant_setting")
        .order_by(AuditLog.id.desc())).first()
    assert entry is not None, "the confirmed write left no audit row to link to"

    turn = session.scalars(
        select(AiTurn).where(AiTurn.kind != "proposals").order_by(AiTurn.id.desc())).first()
    proposal = turn.proposals[0]
    assert proposal["outcome"] == "confirmed"
    assert proposal["entity_type"] == entry.entity_type
    assert proposal["entity_id"] == entry.entity_id

    # A confirm is its own turn: the reads of the turn before it belong to
    # that turn's row and are not repeated here.
    assert [call["tool"] for call in turn.tools] == ["write_plant_setting"]


# ------------------------------------------------- 3. what is never recorded

def test_a_recorded_turn_carries_neither_the_system_prompt_nor_the_key(
        admin, session, settings_agent):
    """The rule that keeps this table from becoming a liability, checked
    against the real `SYSTEM` string rather than a paraphrase of it."""
    script = settings_agent
    script += [response(block_text("Nothing to change."))]
    admin.post("/assist/agent", json={"message": "how are we doing?"})

    turn = session.scalars(select(AiTurn).order_by(AiTurn.id.desc())).first()
    written = " ".join(str(v) for v in (
        turn.asked, turn.said, turn.tools, turn.proposals, turn.model, turn.kind))
    for forbidden in ai_trace.NEVER_STORED:
        assert forbidden not in written
    assert agent.SYSTEM.split("{")[0].strip() not in written
    assert "test-key" not in written


# ---------------------------------------------------------- 4. the horizon

def _turn(session, *, days_ago: float, sid: str = "s1") -> AiTurn:
    row = ai_trace.record(session, {"session": sid, "user": "ADMIN", "kind": "reply",
                                    "asked": "q", "said": "a"})
    row.ts = utcnow() - timedelta(days=days_ago)
    session.flush()
    return row


def test_a_turn_past_the_plants_horizon_is_deleted_as_new_ones_are_written(session):
    _turn(session, days_ago=100)
    _turn(session, days_ago=10)
    ai_trace.record(session, {"session": "s1", "user": "ADMIN", "kind": "reply"},
                    keep_days=90)
    kept = session.scalars(select(AiTurn).order_by(AiTurn.id)).all()
    assert len(kept) == 2, "the hundred-day-old turn should have gone with the write"


def test_keeping_nothing_is_not_what_zero_means(session):
    """Zero is the plant that keeps its whole AI history. A horizon of zero
    days that deleted everything would be a setting whose only effect is to
    make the screen lie about what it has."""
    _turn(session, days_ago=1000)
    ai_trace.record(session, {"session": "s1", "user": "ADMIN", "kind": "reply"},
                    keep_days=0)
    assert len(session.scalars(select(AiTurn)).all()) == 2


# ------------------------------------------------------------ the two reads

def test_the_conversation_list_says_its_total_and_what_became_of_each_proposal(session):
    for index in range(3):
        ai_trace.record(session, {
            "session": f"s{index}", "user": "ADMIN", "kind": "proposals",
            "asked": f"ask {index}", "said": "here",
            "proposals": [{"id": "p", "tool": "write_plant_setting",
                           "outcome": "confirmed" if index else "declined"}],
            "usd": 0.01})
    page = ai_trace.conversations(session, limit=2)
    assert page["total"] == 3 and page["showing"] == 2
    assert page["conversations"][0]["session"] == "s2"
    assert page["conversations"][0]["proposals"] == {"confirmed": 1}
    assert page["conversations"][0]["opened_with"] == "ask 2"


def test_one_conversation_reads_in_the_order_it_happened(session):
    for index in range(3):
        ai_trace.record(session, {"session": "one", "user": "ADMIN", "kind": "reply",
                                  "asked": f"ask {index}", "said": "here"})
    page = ai_trace.turns(session, conversation="one")
    assert [t["asked"] for t in page["turns"]] == ["ask 0", "ask 1", "ask 2"]
    assert page["total"] == 3


def test_both_endpoints_need_audit_read(client, admin):
    """The same gate the audit trail is behind, for the same reason. An
    operator sees neither; the supervisor and above see both."""
    for path in ("/ai/conversations", "/ai/turns"):
        assert client.get(path).status_code == 403
        assert admin.get(path).status_code == 200


# ------------------------------------------------------- without a browser

def test_the_cli_prints_the_same_conversations_and_one_of_them_in_full(tmp_path):
    """`fsmes ai conversations` and `fsmes ai show`, against a plant of their
    own on disk. A plant's server over SSH at two in the morning is where
    somebody usually is when they want to know what the assistant told the
    night shift, and that person has no browser."""
    from typer.testing import CliRunner

    from fsmes import config, db
    from fsmes.cli import app
    from fsmes.db import Base, make_engine

    url = f"sqlite:///{tmp_path / 'plant.db'}"
    with pytest.MonkeyPatch.context() as env:
        env.setenv("MES_DATABASE_URL", url)
        config.get_settings.cache_clear()
        db.get_engine.cache_clear()
        db.get_sessionmaker.cache_clear()

        engine = make_engine(url)
        Base.metadata.create_all(engine)
        from sqlalchemy.orm import Session as OrmSession
        with OrmSession(engine, expire_on_commit=False) as db_session:
            ai_trace.record(db_session, {
                "session": "abc123", "user": "ADMIN", "model": "claude-sonnet-5",
                "kind": "proposals", "asked": "make the prefix CR",
                "said": "I will change it.",
                "tools": [{"tool": "plant_settings", "args": {"find": "prefix"},
                           "ok": True, "summary": "total=1, showing=1"}],
                "proposals": [{"id": "p1", "tool": "write_plant_setting",
                               "args": {"key": "nc_code_prefix", "value": "CR"},
                               "outcome": "confirmed",
                               "entity_type": "plant_setting",
                               "entity_id": "[quality] nc_code_prefix"}],
                "usd": 0.005})
            db_session.commit()
        engine.dispose()

        runner = CliRunner()
        listed = runner.invoke(app, ["ai", "conversations"])
        assert listed.exit_code == 0, listed.output
        assert "1 of 1 conversation" in listed.output
        assert "abc123" in listed.output and "ADMIN" in listed.output
        assert "make the prefix CR" in listed.output

        shown = runner.invoke(app, ["ai", "show", "abc123"])
        assert shown.exit_code == 0, shown.output
        assert "make the prefix CR" in shown.output
        assert "plant_settings find=prefix" in shown.output
        assert "confirmed" in shown.output
        # The audit row a confirmed proposal wrote, printed beside it.
        assert "[quality] nc_code_prefix" in shown.output

        missing = runner.invoke(app, ["ai", "show", "nope"])
        assert missing.exit_code == 1
        assert "No conversation" in missing.output

    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()
