"""The assistant looks before it denies, and an optional argument is not a
question.

Two sentences went into `agent.SYSTEM` for two things that happened live on
`5eda7495` on 2026-09-27 between 11:15 and 11:25 - the third live run, admin
37/40 and operator 28/30, reports under `docs/ai/assist-eval/`.

    "I want a non-conformance to have a prefix CR instead of NC."
        -> no tool call, then: "That's not something I can do here - NC codes
           aren't configurable in this system; they're assigned automatically
           with a fixed 'NC' prefix."
    "could you show me where?"
        -> "there's no walkthrough for this because it isn't a real control."

`nc_code_prefix` is a live Quality setting. The same request four hours earlier
found it in one `plant_settings(find=...)`. Nothing was read either time, so both
sentences were said from belief, and one unread assertion cost two cases: having
decided the control was not real, the assistant then had nothing to walk him to.
That is #108's rule - *read before saying what is recorded* - asked about what
**exists** rather than about what changed.

    "MIX01 is down, mark it down"        (operator)
        -> "I can set MIX01 to down, but I need a reason so it's tracked
            properly... What's causing it?"                        (no card)

`set_machine_state` requires the machine and the state; the sentence gave both.
`reason` is optional, the station screen puts its picker beside the state
control, and the card's own "Show me" lands there. So asking for it first is #116's
miss in a new coat: the card *is* how the assistant asks, and a question in front
of it leaves somebody at a machine with nothing to press.

The hosted model cannot be run here, so what this file pins is everything that
can be: the sentences are in the prompt; over every reply the scripted suite
produces, a denial never comes with nothing read behind it and a request that
named everything the tool requires always ends in a card; and the scorer marks
each of the three live replies above a failure, so the suite that runs live has
teeth against exactly what happened.
"""

import re

import pytest

from fsmes.lab import assist_eval as assist_runs
from fsmes.services import agent, assist_eval

SUITE = assist_eval.load()
BY_ID = {case.id: case for case in SUITE}

#: The conversation's own two walk-me tools. Neither is a read of the plant:
#: `guides()` lists walkthroughs and `show_guide` puts one on a screen, and
#: neither can tell you whether a setting exists. They are named here so that
#: *"there's no walkthrough for this because it isn't a real control"* - which is
#: what came after a `guides()` call and nothing else - counts as a denial with
#: nothing read behind it, because it is one.
NOT_PLANT_READS = frozenset(agent.GUIDE_TOOLS)

#: The ways a reply says *the plant has not got this*, and the ways it says *and
#: it never could*. Each is either the wording that was used live on 2026-09-27
#: or the same claim with the contraction the other way round: a model writes
#: "aren't configurable" and "is not configurable" on different days and means
#: the one thing.
_DENIALS = (
    r"(?:not|n't)\s+(?:something\s+)?configurable",
    r"(?:not|n't)\s+a\s+(?:real|configurable)\b",
    r"(?:does|do|did)(?:\s+not|n't)\s+exist",
    r"no\s+such\b",
    r"there\s+(?:is|are)\s+no\b",
    r"generated\s+(?:by|in)\s+the\s+code",
    r"assigned\s+automatically",
    r"hard-?coded",
)
_DENIAL = re.compile("|".join(f"({pattern})" for pattern in _DENIALS), re.IGNORECASE)


def denials_in(text: str) -> list[str]:
    """The denials a person would read in `text`, as the words that carried them."""
    return [match.group(0) for match in _DENIAL.finditer(text or "")]


def plant_reads(turn) -> tuple[str, ...]:
    """The tools this turn read the plant with. `guides` and `show_guide` are
    not among them - see `NOT_PLANT_READS`."""
    return tuple(tool for tool in turn.reads if tool not in NOT_PLANT_READS)


#: Asking for an argument the tool does not require. The reason picker is beside
#: the state control on the station screen and the card walks onto it, so every
#: one of these is a turn that ended in a question where a card belonged.
_ASKS_FOR_MORE = (
    "what's causing it",
    "what is causing it",
    "i need a reason",
    "i'll need a reason",
    "what reason",
    "which reason",
    "can you tell me why",
    "tell me the reason",
    "what should i put as the reason",
)


def asks_for_an_argument(text: str) -> list[str]:
    """The ways `text` asks for something the tool would have taken without."""
    low = (text or "").casefold()
    return [phrase for phrase in _ASKS_FOR_MORE if phrase in low]


def required_beyond_the_plant(tool: str) -> set[str]:
    """What this tool will not run without, other than which plant it is.

    The plant is the conversation's, never the person's to say, so it is not part
    of what a request has to name.
    """
    for listed in agent.registry_tools():
        if listed.name == tool:
            return set((listed.input_schema or {}).get("required") or ()) - {"plant"}
    raise AssertionError(f"no tool named {tool!r} in this product's registry")


# ------------------------------------------------- what the model is told to do

def test_the_prompt_says_to_read_before_denying():
    assert "Read before you deny." in agent.SYSTEM
    assert ("Never say a thing is not configurable, does not exist, is generated by "
            "the code or is not a real control until you have looked for it in this turn"
            in agent.SYSTEM)


def test_the_prompt_says_which_read_finds_a_setting():
    """The denial that cost two cases was about a setting, and there is one read
    that finds any setting by the words a person used for it."""
    assert "plant_settings(find=...)" in agent.SYSTEM
    assert "anything else is that domain's own read" in agent.SYSTEM


def test_the_prompt_says_an_optional_argument_is_not_a_question():
    assert "An optional argument is not a question" in agent.SYSTEM
    assert ("leave anything optional they did not give - a downtime reason, a note - "
            "for the card and the walk beside it rather than asking for it first"
            in agent.SYSTEM)


# --------------------------------- the two rules, over every reply the suite makes

@pytest.fixture(scope="module")
def scored():
    return assist_runs.run_scripted(SUITE)


def test_no_denial_the_suite_produces_has_nothing_read_behind_it(scored):
    """Every case, every role: a sentence that tells somebody the plant has not
    got a thing was said after looking for it."""
    unread = [(o.case.id, o.turn.say, denials_in(o.turn.say))
              for o in scored
              if denials_in(o.turn.say) and not plant_reads(o.turn)]
    assert not unread, "\n".join(f"{cid}: {words} with no read behind it, in {say!r}"
                                 for cid, say, words in unread)


def test_every_request_that_named_what_the_tool_requires_ends_in_a_card(scored):
    """A `propose` case names everything its tool requires - the suite's own test
    below holds that - so nothing in it is waiting on an answer from the person.
    What they end the turn holding is a card, never a question about an argument
    the tool would have run without."""
    wrong = []
    for outcome in scored:
        if outcome.case.expect != "propose":
            continue
        turn = outcome.turn
        if turn.kind != "proposals" or not turn.proposals:
            wrong.append(f"{outcome.case.id}: no card - it {turn.observed()}")
        elif asks_for_an_argument(turn.say):
            wrong.append(f"{outcome.case.id}: asked for an argument "
                         f"({asks_for_an_argument(turn.say)}) in {turn.say!r}")
    assert not wrong, "\n".join(wrong)


def test_every_propose_case_names_everything_its_tool_requires():
    """The other half of the rule above, and the thing that makes it fair: a case
    that left out a required argument *would* be a question, and the assistant
    asking it would be right. `fills` counts - it is the rest of a legal call, the
    part the sentence implied rather than said."""
    short = []
    for case in SUITE:
        if case.expect != "propose" or not case.tool:
            continue
        named = set(case.args) | set(case.fills)
        owed = required_beyond_the_plant(case.tool) - named
        if owed:
            short.append(f"{case.id}: {case.tool} requires {sorted(owed)}, "
                         f"which the request does not name")
    assert not short, "\n".join(short)


# ------------------------------- marking a machine down, with no reason to give

def test_marking_a_machine_down_is_proposed_with_the_reason_left_for_the_card(scored):
    """Named, so it cannot quietly stop being scored this way. The sentence gives
    the machine and the state, which is everything `set_machine_state` requires;
    `reason` is optional and is not in the card, because he did not say one."""
    case = BY_ID["operator-marks-a-machine-down"]
    assert case.expect == "propose" and case.tool == "set_machine_state"
    assert required_beyond_the_plant("set_machine_state") <= set(case.args)
    assert "reason" not in case.args and "reason" not in case.fills

    outcome = next(o for o in scored if o.case.id == case.id)
    assert outcome.passed, outcome.why
    card = next(p for p in outcome.turn.proposals if p["tool"] == "set_machine_state")
    assert not card["args"].get("reason"), card["args"]


def test_asking_for_the_reason_first_does_not_pass():
    """Live, 11:2x: *"I can set MIX01 to down, but I need a reason so it's tracked
    properly... What's causing it?"* - no card, and a machine still running in the
    plant's own record while somebody types an answer."""
    case = BY_ID["operator-marks-a-machine-down"]
    said = ("I can set MIX01 to down, but I need a reason so it's tracked properly. "
            "What's causing it?")
    outcome = assist_eval.score(case, assist_eval.Turn(kind="reply", say=said,
                                                       from_model=True))
    assert not outcome.passed
    assert any("nothing was proposed" in why for why in outcome.why)
    assert asks_for_an_argument(said)


# --------------------------------------- the prefix denial, and what it cost him

def test_saying_the_prefix_is_not_configurable_does_not_pass():
    """Live, 11:1x: *"That's not something I can do here - NC codes aren't
    configurable in this system; they're assigned automatically with a fixed 'NC'
    prefix."* Three denials in one sentence, no read behind any of them, and
    `nc_code_prefix` is a Quality setting this product ships."""
    case = BY_ID["scott-wants-the-nonconformance-prefix-to-be-cr"]
    said = ("That's not something I can do here - NC codes aren't configurable in this "
            "system; they're assigned automatically with a fixed 'NC' prefix.")
    outcome = assist_eval.score(case, assist_eval.Turn(kind="reply", say=said,
                                                       from_model=True))
    assert not outcome.passed
    assert any("nothing was proposed" in why for why in outcome.why)
    assert len(denials_in(said)) >= 2, denials_in(said)


def test_and_then_having_denied_it_there_was_nowhere_to_walk_him(scored):
    """The second failure the same denial caused. Asked to be shown, the
    assistant said *"there's no walkthrough for this because it isn't a real
    control"* - a claim about the plant, after a call that says nothing about the
    plant. The scripted run gets this case right, which is what makes the live
    miss a model miss rather than a reachability one."""
    case = BY_ID["scott-asks-to-be-shown-with-nothing-open"]
    said = "There's no walkthrough for this because it isn't a real control."
    outcome = assist_eval.score(case, assist_eval.Turn(kind="reply", say=said,
                                                       from_model=True))
    assert not outcome.passed
    assert denials_in(said)
    # And the walk was there to be handed over all along.
    reachable = next(o for o in scored if o.case.id == case.id)
    assert reachable.passed, reachable.why


def test_a_call_that_lists_walkthroughs_is_not_a_read_of_the_plant():
    """The distinction the rule turns on. `guides()` was called before *"it isn't a
    real control"*, and a suite that counted it would have passed the sentence that
    cost him the second case: a list of walkthroughs cannot tell you whether a
    setting exists."""
    turn = assist_eval.Turn(kind="reply", say="It isn't a real control.",
                            reads=("guides", "show_guide"), from_model=True)
    assert denials_in(turn.say) and not plant_reads(turn)
    looked = assist_eval.Turn(kind="reply", say="There is no such setting in Quality.",
                              reads=("guides", "plant_settings"), from_model=True)
    assert denials_in(looked.say) and plant_reads(looked) == ("plant_settings",)


# ------------------------------------- the predicates, against what was actually said

@pytest.mark.parametrize("said", [
    "NC codes aren't configurable in this system.",
    "That is not configurable here.",
    "It isn't a real control, so there is nothing to show you.",
    "That setting does not exist on this plant.",
    "There's no such setting.",
    "The prefix is generated by the code.",
    "They're assigned automatically with a fixed 'NC' prefix.",
    "The prefix is hardcoded.",
    "There is no setting for that.",
])
def test_a_denial_is_recognised_in_the_words_a_person_would_read(said):
    assert denials_in(said), said


@pytest.mark.parametrize("said", [
    # Honest reports of what a read came back with are not denials of its
    # existence - they are the answer, and they have a read behind them.
    "Rules 1, 2, 3 and 4 are all set to hold when they fire.",
    "It is on your screen now (two steps).",
    "Here is what I would change - nothing has changed yet.",
    "MIX01 is down and nothing is labelled against the stop yet.",
    "This one is not yours to do here - a supervisor can.",
])
def test_an_answer_is_not_read_as_a_denial(said):
    assert not denials_in(said), said


@pytest.mark.parametrize("said", [
    "I can set MIX01 to down, but I need a reason. What's causing it?",
    "I'll need a reason before I can record that.",
    "Which reason should go against the stop?",
])
def test_asking_for_an_optional_argument_is_recognised_as_asking(said):
    assert asks_for_an_argument(said), said


@pytest.mark.parametrize("said", [
    "MIX01 goes to down - press Do it, and the reason picker is beside it.",
    "Here is what I would change - nothing has changed yet.",
    "That is on hold with the reason you gave: waiting on flavour.",
])
def test_proposing_with_the_reason_left_open_is_not_read_as_asking(said):
    assert not asks_for_an_argument(said), said
