"""Asked for a change, the assistant proposes it; and no tool name reaches the
person.

Three sentences went into `agent.SYSTEM` for three things that happened live on
`2c5281e0` on 2026-09-26 between 21:05 and 21:30, signed in as ADMIN at
bottling:

    "I want a non-conformance to have a prefix CR instead of NC. Could you
     make that change?"
        -> "I can change it to 'CR' - want me to go ahead?"     (no card)

    "...which spc rules raise a hold to 1,2 only"
        -> "I'll change it to '1,2' - meaning only Western Electric rules 1
            and 2 will trigger..."                              (no card)

    "could you show me where?"        (nothing open)
        -> "I can propose the change now, and then show_guide(\"proposal\")
            will put the real form with 'CR' already filled in..."

The same requests had produced cards four hours earlier. The model varied, and
the prompt let it: #109 already said *a proposal is not a change*, and nothing
said *a request for a change is a proposal, not a question*.

The hosted model cannot be run here, so what this file pins is everything that
can be: the sentences are in the prompt; every reply the scripted suite produces
passes the two rules a person can see from the outside - a change is proposed
rather than asked about, and no tool's name appears in the words they read - and
the scorer marks each of the three live replies above a failure, so the suite
that runs live has teeth against exactly what happened.
"""

import re

import pytest

from fsmes.lab import assist_eval as assist_runs
from fsmes.services import agent, assist_eval
from live_replies import NOTHING_DEAD_TO_RETRY

SUITE = assist_eval.load()
BY_ID = {case.id: case for case in SUITE}

#: Every tool this product serves, plus the conversation's own two. The
#: conversation's two are the ones that leaked: `show_guide("proposal")` was
#: typed at Scott verbatim.
TOOL_NAMES = frozenset({tool.name for tool in agent.registry_tools()}) | set(agent.GUIDE_TOOLS)

#: `name(` - a call, written out where a sentence should have been.
_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def tool_names_in(text: str) -> list[str]:
    """The tool names a person would read in `text`.

    Two shapes, because a tool name reaches prose two ways. A name with an
    opening bracket after it is a call however it is spelled - `guides()`,
    `show_guide("proposal")`, `orders(limit=50)`. A name with an underscore in
    it is a tool name wherever it appears, bracket or none, because
    `write_plant_setting` and `setting_changes` are not words anybody says out
    loud. A bare one-word name is *not* counted: `orders`, `quality` and
    `machines` are all tools and all ordinary English, and a suite that failed
    on "your open orders" would be measuring the wrong thing.
    """
    found = {name for name in _CALL.findall(text) if name in TOOL_NAMES}
    for name in TOOL_NAMES:
        if "_" in name and re.search(rf"\b{re.escape(name)}\b", text):
            found.add(name)
    return sorted(found)


#: Asking to be allowed to make the change, rather than proposing it. Each of
#: these is a way of ending a turn that leaves the person with nothing to press.
_ASKING = (
    "want me to go ahead",
    "do you want me to",
    "would you like me to",
    "shall i",
    "should i go ahead",
    "let me know if you want",
    "just say the word",
    "confirm and i will",
)


def asks_permission(text: str) -> list[str]:
    """The ways `text` asks to be allowed, instead of proposing."""
    low = text.casefold()
    return [phrase for phrase in _ASKING if phrase in low]


# ------------------------------------------------- what the model is told to do

def test_the_prompt_says_a_request_for_a_change_is_a_proposal_and_not_a_question():
    assert "A request for a change is a proposal, not a question" in agent.SYSTEM
    assert 'never reply "want me to go ahead?"' in agent.SYSTEM


def test_the_prompt_says_show_me_with_nothing_open_is_answered_with_a_card():
    """The card's own "Show me" is the walk being asked for. It cannot be put on
    the screen in the same turn as the card - the card is what the person is then
    looking at - so the answer is to propose and leave the walk one press away."""
    assert "Asked to be shown a change nothing has proposed yet" in agent.SYSTEM
    assert 'the card\'s own "Show me" is the walk they are asking for' in agent.SYSTEM


def test_the_prompt_says_a_tool_name_never_reaches_the_person():
    assert "Never put a tool's name in what they read" in agent.SYSTEM


# ---------------------------------- the two rules, over every reply the suite makes

@pytest.fixture(scope="module")
def scored():
    return assist_runs.run_scripted(SUITE)


def test_no_reply_the_suite_produces_names_a_tool_in_the_words_the_person_reads(scored):
    """Every case, every role, every turn: the sentence beside the card, the
    sentence above a walk, and the sentence that answers a question."""
    leaked = [(o.case.id, o.turn.say, tool_names_in(o.turn.say))
              for o in scored if tool_names_in(o.turn.say)]
    assert not leaked, "\n".join(f"{cid}: {names} in {say!r}" for cid, say, names in leaked)


def test_every_request_for_a_change_ends_in_a_proposal_and_not_a_question(scored):
    """A `propose` case is a person asking for a change. What they must end the
    turn holding is a card - not a sentence about the change, and not a question
    about whether to make it."""
    wrong = []
    for outcome in scored:
        if outcome.case.expect != "propose":
            continue
        turn = outcome.turn
        if turn.kind != "proposals" or not turn.proposals:
            wrong.append(f"{outcome.case.id}: no card - it {turn.observed()}")
        elif asks_permission(turn.say):
            wrong.append(f"{outcome.case.id}: asked permission ({asks_permission(turn.say)}) "
                         f"in {turn.say!r}")
    assert not wrong, "\n".join(wrong)


def test_the_two_requests_that_lost_their_card_on_the_evening_of_2026_09_26_are_both_propose_cases():
    """Named, so that neither can quietly stop being scored this way."""
    for case_id in ("scott-wants-the-nonconformance-prefix-to-be-cr",
                    "scott-changes-which-spc-rules-raise-a-hold"):
        case = BY_ID[case_id]
        assert case.expect == "propose" and case.tool == "write_plant_setting"


# ------------------------------------------- asked to be shown, with nothing open

def test_asked_to_be_shown_a_change_nobody_has_proposed_the_card_and_its_walk_arrive_together(scored):
    """Both halves in the one turn: the card, and the card's own "Show me"
    standing on the field the change is made in. The walk is *offered*, not put
    on the screen - a walkthrough and a card in one turn is a card, because that
    is what the person is looking at."""
    case = BY_ID["scott-asks-to-be-shown-with-nothing-open"]
    assert "show me where" in case.request.casefold()
    assert not case.over_proposal, "the point of this case is that nothing is open"

    outcome = next(o for o in scored if o.case.id == case.id)
    assert outcome.passed, outcome.why
    card = next(p for p in outcome.turn.proposals if p["tool"] == "write_plant_setting")
    assert card["args"]["value"] == "CR"
    anchors = [step["anchor"] for step in card["surface"]["steps"]]
    assert "setting-in-focus" in anchors, anchors


def test_a_narrated_walk_with_no_card_does_not_pass(scored):
    """Live, 21:2x: *"I can propose the change now, and then
    show_guide(\"proposal\") will put the real form with 'CR' already filled
    in..."* - the walk described, the tool named, and nothing on his screen."""
    case = BY_ID["scott-asks-to-be-shown-with-nothing-open"]
    said = ('I can propose the change now, and then show_guide("proposal") will put the '
            "real form with 'CR' already filled in on your screen.")
    outcome = assist_eval.score(case, assist_eval.Turn(kind="reply", say=said,
                                                       from_model=True))
    assert not outcome.passed
    assert any("nothing was proposed" in why for why in outcome.why)
    assert tool_names_in(said) == ["show_guide"]


# ------------------------------------- the predicates, against what was actually said

@pytest.mark.parametrize(("said", "names"), [
    ('I can propose the change now, and then show_guide("proposal") will put the real form '
     "on your screen.", ["show_guide"]),
    ("Call guides() to see what this plant has.", ["guides"]),
    ("I would set write_plant_setting for you.", ["write_plant_setting"]),
    ("Nothing is recorded in setting_changes.", ["setting_changes"]),
    # What the model actually said, live on `09c8ce2c` on 2026-09-27, to an
    # operator asking for a dead ERP message to be requeued. The scripted
    # stand-in never breaks this rule, so until this line the rule was only ever
    # scored against sentences written here.
    (NOTHING_DEAD_TO_RETRY, ["erp_retry"]),
    # Ordinary English that happens to be a tool name is not a leak.
    ("You have three open orders and one quality hold.", []),
    ("Here is what I would change - nothing has changed yet.", []),
    ("It is on your screen now (two steps).", []),
])
def test_a_tool_name_is_recognised_in_the_words_a_person_would_read(said, names):
    assert tool_names_in(said) == names


@pytest.mark.parametrize("said", [
    "I can change it to 'CR' - want me to go ahead?",
    "I found the setting. Do you want me to change it?",
    "Shall I set it to 1,2?",
    "Let me know if you want that changed.",
])
def test_asking_to_be_allowed_is_recognised_as_asking(said):
    assert asks_permission(said), said


@pytest.mark.parametrize("said", [
    "Here is what I would change - nothing has changed yet.",
    "Rules 1,2,3,4 raise a hold; that is the default.",
    "This one is not yours to do here - a supervisor can.",
])
def test_proposing_and_answering_are_not_read_as_asking(said):
    assert not asks_permission(said), said


# ------------------------------------ the rule, against prose a model really wrote


def test_the_live_reply_that_named_a_tool_is_scored_by_this_rule_and_not_only_by_a_stand_in():
    """The scripted suite cannot catch this one. Its stand-in writes the
    sentences it writes, so "no tool name reaches the person" is a rule about
    prose that, run scripted, is only ever scored against prose this repository
    wrote itself.

    Live on `09c8ce2c`, 2026-09-27, as SCOTT at bottling, the model read the ERP
    outbox, found nothing dead, said so - and then told an operator that
    requeuing is "done via erp_retry". That is the miss, and it is the reply it
    is scored against from here on; `tests/live_replies.py` is where such a reply
    is kept.
    """
    assert tool_names_in(NOTHING_DEAD_TO_RETRY) == ["erp_retry"]
    # And not a leak the predicate found by accident: the tool it named is the
    # one the request was about.
    assert BY_ID["operator-refused-retrying-a-dead-erp-message"].tool == "erp_retry"
