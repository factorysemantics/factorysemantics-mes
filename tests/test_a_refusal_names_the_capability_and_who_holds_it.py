"""A refusal that says who can do the thing instead.

The tool catalogue is filtered per capability, so the model is never shown a
tool the person may not use. That is right, and it is also what made a refusal
useless: asked to close a non-conformance, an operator's assistant reached for
a tool that was not there and got back *"no tool named 'close_nonconformance'
is available to this person"* - a true fact about the catalogue, and no use at
all to somebody standing in front of the non-conformance.

Six cases in the request suite were marked `not_yet` for exactly this. The
answer is one sentence, from one lookup, in `capabilities.py`: the capability,
its plain description, and the roles that hold it - the same sentence the
signing walks already say their half of the question with.

Everything here is keyed on `CAPABILITIES` and `BUILTIN_ROLES` rather than on
counts or on a hand-written list of tools, so a capability added tomorrow is
covered or these go red.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from fsmes.services import agent, assistant
from fsmes.services import capabilities as caps

ROLES = {code: set(spec["capabilities"]) for code, spec in caps.BUILTIN_ROLES.items()}


def test_who_holds_a_capability_names_every_role_that_does_and_none_that_does_not():
    """The lookup, against `capabilities.py` itself. Not a list written out
    again here: a role is a bundle of capabilities and this is that bundle read
    backwards, so a capability granted to a new role is named by it at once."""
    for capability in caps.CAPABILITIES:
        named = caps.holders(capability)
        expected = [spec["name"] for spec in caps.BUILTIN_ROLES.values()
                    if capability in spec["capabilities"]]
        assert named == expected, capability


def test_every_capability_can_be_refused_in_one_sentence_that_survives_the_record():
    """A tool result is summarised to 160 characters in the turn record and in
    the transcript the panel shows. The three facts that must survive that cut
    are the capability, that it is not held, and who holds it - which is why
    the plain description comes last in the sentence and not in the middle.
    """
    for capability in caps.CAPABILITIES:
        sentence = caps.not_yours(capability)
        kept = sentence[:160]
        assert capability in kept, sentence
        assert "you do not hold it" in kept, sentence
        assert "can" in kept, sentence
        # And the whole sentence carries the product's own words for it.
        assert caps.CAPABILITIES[capability].rstrip(".") in sentence


def test_a_refusal_never_says_somebody_who_does():
    """It said that until 2026-09-26. Somebody who does not know who that is
    cannot go and find them, which is the whole of this change."""
    for capability in caps.CAPABILITIES:
        assert "somebody who does" not in caps.not_yours(capability).casefold()


def test_the_roles_named_are_this_plants_own_after_an_admin_redefines_one():
    """Capability bundles are data, and an admin may rename and redefine them
    without a deployment. A refusal that named the roles the product ships
    would be a true sentence about some other plant."""
    theirs = {"Shift Lead": ["plant.read", "quality.close_nc"],
              "Plant Owner": ["plant.read", "users.manage"]}
    sentence = caps.not_yours("quality.close_nc", roles=theirs)
    assert "Shift Lead can" in sentence
    assert "Supervisor" not in sentence and "Administrator" not in sentence
    assert caps.holders("users.manage", theirs) == ["Plant Owner"]


def test_a_capability_no_role_holds_is_said_to_be_nobodys_rather_than_guessed():
    """Unknown is not zero, and neither is it "ask a supervisor". A plant that
    has taken a capability off every role has nobody to send the person to, and
    the honest answer says so and says what would fix it."""
    nobody = caps.not_yours("quality.close_nc", roles={"Viewer": ["plant.read"]})
    assert "No role at this plant holds it" in nobody
    assert "grant it" in nobody


def test_more_holders_than_a_sentence_can_carry_are_counted_not_trailed_off():
    """House rule two on a sentence read at a machine: the roles it does not
    name are still counted out loud."""
    many = {f"Role {i}": ["quality.close_nc"] for i in range(7)}
    line = caps.who_holds("quality.close_nc", many)
    assert line.startswith("Role 0, Role 1 and Role 2 can")
    assert f"{caps.NAMED} of the 7 roles that hold it" in line


# ------------------------------------------ the same answer from the agent


@pytest.mark.parametrize("tool,capability", sorted(agent.NEEDS.items()))
def test_every_write_tool_withheld_for_a_capability_says_which_and_whose(tool, capability):
    """One case per write action, from `NEEDS` - so a new tool arrives with
    this answer or it arrives red."""
    lacking = {"plant.read"}
    assert capability not in lacking
    out = agent.withheld(tool, lacking)
    assert out is not None, f"{tool} refused without naming {capability}"
    assert out["capability"] == capability
    assert out["held_by"] == caps.holders(capability)
    assert capability in out["error"]
    assert caps.CAPABILITIES[capability].rstrip(".") in out["error"]
    for role in out["held_by"][:caps.NAMED]:
        assert role in out["error"]


@pytest.mark.parametrize("tool,capability", sorted(agent.NEEDS.items()))
def test_a_tool_the_person_holds_is_not_refused_at_all(tool, capability):
    assert agent.withheld(tool, {"plant.read", capability}) is None


def test_a_name_that_is_not_a_tool_keeps_the_sentence_it_always_had():
    """There is nothing else true to say about it. The catalogue sentence is
    only wrong when the tool exists and the person may not use it."""
    assert agent.withheld("close_the_plant", set(ROLES["admin"])) is None


def test_a_setting_refusal_names_the_capability_of_the_section_the_key_is_under():
    """`write_plant_setting` is the one tool whose capability is an argument
    rather than a property of the tool (`PER_CALL_NEEDS`), so the refusal reads
    the owning section out of the same registry the API reads it from - every
    live section, not one worked example."""
    from fsmes.services import plant_settings

    checked = 0
    for section in plant_settings.live_sections():
        if not section.define:
            continue
        for written in section.pack_keys:
            # The name the tool takes is the pack key's, which is not always
            # the section's own - `[oee] coverage_floor` on a Process page.
            _table, key = plant_settings.split(written)
            out = agent.withheld("write_plant_setting", {"plant.read"},
                                 {"domain": section.domain, "key": key})
            assert out is not None, written
            assert out["capability"] == section.define, written
            assert section.define in out["error"]
            assert out["held_by"] == caps.holders(section.define)
            checked += 1
    assert checked, "no live section gates a setting, so this proves nothing"


def test_a_setting_refusal_with_no_key_counts_the_capabilities_rather_than_picking_one():
    """No key, no one capability - a call the API would refuse too. Naming one
    of them would be naming the wrong one for every other domain."""
    could = agent.needs_any("write_plant_setting")
    out = agent.withheld("write_plant_setting", {"plant.read"}, {})
    assert out is not None
    assert out["capability"] is None
    assert f"none of the {len(could)} that gate one" in out["error"]
    for capability in could:
        assert capability in out["error"]


def test_somebody_who_may_write_one_setting_is_not_refused_the_tool_at_all():
    """They are offered it and refused key by key at the API, which is what
    `PER_CALL_NEEDS` says and what the catalogue already does."""
    one = next(iter(agent.needs_any("write_plant_setting")))
    assert agent.withheld("write_plant_setting", {"plant.read", one}, {}) is None


# --------------------------------------- what the model is told before it asks


def test_the_conversation_is_told_every_action_it_cannot_take_and_who_can():
    """The other half. A model only calls a tool it thinks of calling, and the
    moment it needs this is the moment it has decided there is nothing to call -
    so the facts are in the cached prefix before the question arrives.
    """
    operator = set(ROLES["operator"])
    note = agent.withheld_note(operator)
    offered = {t["name"] for t in agent.catalogue(operator)}
    every = set(agent.NEEDS) | set(agent.PER_CALL_NEEDS)
    for tool in every - offered:
        assert f"- {tool}: needs " in note, tool
        capability = agent.NEEDS.get(tool)
        if capability:
            assert f"needs {capability} ({caps.CAPABILITIES[capability].rstrip('.')})" in note
            assert caps.who_holds(capability) in note
    for tool in every & offered:
        assert f"- {tool}:" not in note, f"{tool} is theirs and does not belong in the list"
    assert f"{len(every - offered)} of the {len(every)} actions" in note


def test_an_administrator_is_told_nothing_because_nothing_is_withheld():
    assert agent.withheld_note(set(ROLES["admin"])) == ""


def test_the_note_reaches_the_model_in_the_system_prompt_it_caches():
    sess = agent.open_session("JO", "bottling", set(ROLES["operator"]))
    assert "close_nonconformance" in sess.withheld
    sent = agent.SYSTEM.format(plant=sess.plant) + sess.withheld
    assert sent.startswith("You are the assistant")
    assert "never say there is no tool for it" in sent


def test_a_conversation_opened_with_this_plants_roles_names_this_plants_roles():
    sess = agent.open_session("JO", "bottling", {"plant.read"},
                              roles={"Shift Lead": ["plant.read", "users.manage"]})
    out = agent.withheld("create_user", sess.capabilities, {}, sess.roles)
    assert "Shift Lead can" in out["error"]
    assert "Shift Lead can" in sess.withheld


# ------------------------------------------------ and through a whole turn


@pytest.fixture()
def a_scripted_model(monkeypatch, tmp_path):
    """The model, following a script. No plant is reached: the tool the script
    asks for is one this person was never given, so nothing runs."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MES_AGENT_BRAIN", "claude")
    monkeypatch.setattr(agent, "sdk_installed", lambda: True)
    monkeypatch.setattr(agent, "USAGE_FILE", tmp_path / "usage.jsonl")
    monkeypatch.setattr(agent, "TURN_FILE", tmp_path / "turns.jsonl")
    script: list = []
    monkeypatch.setattr(agent, "_call_model", lambda sess: script.pop(0))
    return script


def _usage():
    return SimpleNamespace(input_tokens=1000, output_tokens=50,
                           cache_read_input_tokens=800, cache_creation_input_tokens=0)


def _said(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=_usage())


def test_the_refusal_the_person_reads_back_names_the_capability_and_who(a_scripted_model):
    """End to end, the way the request suite scores it: the model reaches for a
    tool it was not given, and the transcript line the panel shows - and the
    turn record keeps - is the sentence, not the catalogue's own words."""
    a_scripted_model += [
        _said(SimpleNamespace(type="tool_use", id="t1", name="close_nonconformance",
                              input={"code": "NC-00001"}), stop="tool_use"),
        _said(SimpleNamespace(type="text",
                              text="Closing one needs quality.close_nc; a supervisor can.")),
    ]
    sess = agent.open_session("JO", "bottling", set(ROLES["operator"]))
    agent.message(sess, "close NC-00001")

    refusal = json.loads(sess.history[2]["content"][0]["content"])
    assert refusal["capability"] == "quality.close_nc"
    assert "Supervisor" in refusal["held_by"]
    summary = [e["summary"] for e in sess.transcript if not e["ok"]]
    assert summary and "quality.close_nc" in summary[0]
    assert "can" in summary[0]
    assert "no tool named" not in summary[0]


# ----------------------------------- the signing walks say it the same way


def test_a_signing_walk_still_leads_with_the_thing_only_it_can_say():
    """The assistant approves nothing, for anybody (decision 0035). That lead
    sentence is the half that is only true of a signature; the rest is the
    sentence every refusal shares, so there is one of them and not two."""
    operator = set(ROLES["operator"])
    gated = {g["id"]: g for g in assistant.signing_guides(operator)}
    note = gated["approve-a-severity"]["gated"]
    assert note.startswith(assistant.SIGNING_LEAD)
    assert note.endswith(caps.not_yours("quality.approve", gates="signs this"))
    assert "Administrator can" in note


def test_a_signing_walk_names_this_plants_own_signer():
    theirs = {"Quality Manager": ["plant.read", "quality.approve"]}
    gated = {g["id"]: g for g in
             assistant.signing_guides({"plant.read"}, theirs)}
    assert "Quality Manager can" in gated["approve-a-severity"]["gated"]


# ------------------------------------ and through the endpoint, off a real plant


def test_through_the_endpoint_the_roles_named_are_the_ones_in_this_plants_database(
        client, session, monkeypatch):
    """The wire, end to end: `/assist/agent` reads this plant's roles out of the
    short session it already has open and hands them to the conversation.

    Proved by renaming one. `Supervisor` is what the product ships and what a
    lookup with no plant to ask would say, so a plant that calls the role
    something else is the only way to tell the two apart.
    """
    from fsmes.domain import Role

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MES_AGENT_BRAIN", "claude")
    monkeypatch.setattr(agent, "sdk_installed", lambda: True)
    script = [
        _said(SimpleNamespace(type="tool_use", id="t1", name="close_nonconformance",
                              input={"code": "NC-00001"}), stop="tool_use"),
        _said(SimpleNamespace(type="text", text="That one is not yours to close.")),
    ]
    monkeypatch.setattr(agent, "_call_model", lambda sess: script.pop(0))

    role = session.scalar(select(Role).where(Role.code == "supervisor"))
    assert role is not None, "the built-in roles are seeded on start"
    role.name = "Shift Lead"
    session.flush()

    out = client.post("/assist/agent", json={"message": "close NC-00001"}).json()
    assert out["kind"] == "reply", out
    refusal = [line for line in out["transcript"] if not line["ok"]]
    assert refusal, out["transcript"]
    said = refusal[0]["summary"]
    assert "quality.close_nc" in said
    assert "Shift Lead" in said, said
    assert "Supervisor" not in said, said
