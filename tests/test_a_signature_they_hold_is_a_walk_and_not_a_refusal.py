"""Asked to approve a draft they may sign, the assistant walks them to the button.

Live on `f9705990`, 2026-09-27, signed in as ADMIN at bottling - a role holding
`process.approve`, with the draft reason `eval_changeover` waiting:

    "approve the draft downtime reason eval_changeover"
        -> "Approving a downtime reason is a signature I can't put my own name
            to - that needs process.approve from a person on the Engineering
            screen, not this tool call."

Both halves of that are the product's own words read back at the person who
holds the signature. The first is true - the agent never signs (decision 0035),
and #114's `assistant.SIGNING_LEAD` says so in as many words. The second is not:
#113 built five walks to the five signing controls exactly so that this request
has an answer, and this administrator is shown all five.

So the refusal wording has an audience, and it is the person who may *not* sign.
This file pins that: the prompt says never-approving is not refusing, no sentence
in it says the assistant never approves without saying where the signature is,
the words for somebody who may not sign never reach somebody who may, and the
scorer counts the reply above a failure.
"""

import re

import pytest

from fsmes.lab import assist_eval as assist_runs
from fsmes.services import agent, assist_eval, assistant
from fsmes.services import capabilities as caps

SUITE = assist_eval.load()
BY_ID = {case.id: case for case in SUITE}

#: The walks to a signing control, and the capability each signature needs.
SIGNING = {guide["id"]: guide["needs"] for guide in assistant.GUIDES
           if guide.get("signing")}


def sentences(text: str) -> list[str]:
    """The prompt as sentences. It is written with line continuations, so the
    newlines are paragraph breaks and the full stops are the sentence breaks."""
    return [s.strip() for s in re.split(r"(?<=\.)\s+", text.replace("\n", " ")) if s.strip()]


# ------------------------------------------------- what the model is told to do

def test_the_prompt_says_that_never_approving_is_not_refusing():
    assert "Never approving is not refusing" in agent.SYSTEM
    assert "put that walk on their screen" in agent.SYSTEM


def test_no_sentence_in_the_prompt_says_it_never_approves_without_saying_where_to_sign():
    """The lesson of the live turn above. "The assistant never approves anything,
    for anybody" is true and, on its own, reads as *no*. Every sentence in the
    prompt that says it approves nothing has to carry the other half - the walk
    to the control the person signs it on - in the same breath."""
    absolute = [s for s in sentences(agent.SYSTEM)
                if "approv" in s.casefold() and "never" in s.casefold()]
    assert absolute, "the prompt no longer says the assistant approves nothing"
    for sentence in absolute:
        assert "walk" in sentence.casefold(), \
            f"this sentence refuses and never says where the signature is: {sentence!r}"


def test_the_prompt_says_which_half_the_capability_wording_is_for():
    """Naming the capability is the right answer to somebody who may not sign,
    and the wrong answer to somebody who may."""
    assert "the capability wording is for somebody guides() says may not follow it" \
        in agent.SYSTEM


# ------------------------------------------- who the refusal wording is written for

@pytest.mark.parametrize("role", sorted(caps.BUILTIN_ROLES))
def test_the_words_for_somebody_who_may_not_sign_never_reach_somebody_who_may(role):
    """`signing_guides` is what carries `SIGNING_LEAD`, and it is built from the
    walks this person may *not* follow. So a role that holds a signature is
    handed that walk plainly, with nothing in it about what cannot be done."""
    held = assist_eval.capabilities_of(role)
    listed = {guide["id"]: guide for guide in assistant.listed_guides(held)}
    for guide_id, needs in SIGNING.items():
        assert guide_id in listed, f"{role} is not even told {guide_id} exists"
        gated = listed[guide_id].get("gated") or ""
        if needs in held:
            assert not gated, (f"{role} holds {needs} and is still told "
                               f"{gated!r} about {guide_id}")
        else:
            assert gated.startswith(assistant.SIGNING_LEAD), guide_id
            assert needs in gated, f"{guide_id} never says which capability signs it"


def test_an_administrator_is_shown_every_signing_walk_as_theirs_to_follow():
    """The role the live run was scored as. All five signatures are the
    administrator's, so there is no honest reason for any of the five requests
    to come back as a refusal."""
    held = assist_eval.capabilities_of("admin")
    assert {g["id"] for g in assistant.visible_guides(held) if g.get("signing")} \
        == set(SIGNING)
    assert assistant.signing_guides(held) == []


# ------------------------------------------------ and the measurement has teeth

def test_the_refusal_the_live_model_gave_an_administrator_does_not_pass():
    """The reply above, scored. A sentence that names the capability instead of
    putting the walk on the screen is a failure for somebody who holds it."""
    case = BY_ID["admin-approves-a-downtime-reason"]
    turn = assist_eval.Turn(
        kind="reply", from_model=True,
        say="Approving a downtime reason is a signature I can't put my own name to - "
            "that needs process.approve from a person on the Engineering screen, not "
            "this tool call.")
    outcome = assist_eval.score(case, turn)
    assert not outcome.passed
    assert any("no walk was offered" in why for why in outcome.why)


def test_the_walk_to_the_signing_control_does_pass():
    case = BY_ID["admin-approves-a-downtime-reason"]
    turn = assist_eval.Turn(kind="guide", guide_id="approve-a-downtime-reason",
                            from_model=True,
                            say="It is on your screen - you sign it there.")
    assert assist_eval.score(case, turn).passed


@pytest.fixture(scope="module")
def scored():
    return assist_runs.run_scripted(SUITE)


def test_every_signature_an_administrator_holds_is_reachable_as_a_walk(scored):
    """The scripted half: the stand-in follows the prompt's rule - `guides()`,
    then the walk whose own words best match what was asked - and the walk it
    lands on is the one the request was about. What a real model would choose is
    the live run's question; that this is reachable for a holder is this one's.
    """
    asked = [o for o in scored
             if o.case.role == "admin" and (o.case.guide or "").startswith("approve-")
             and o.arranged]
    assert len(asked) >= 4, f"only {len(asked)} of the administrator's approvals were asked"
    for outcome in asked:
        assert outcome.turn.kind == "guide", \
            f"{outcome.case.id}: it {outcome.turn.observed()}"
        assert outcome.turn.guide_id == outcome.case.guide, outcome.case.id
