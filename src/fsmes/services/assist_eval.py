"""Does the floor assistant do what people actually ask?

`tests/test_agent.py` pins the loop: reads run free, writes pause, a confirmed
write is idempotent. It says nothing about whether the assistant, handed a
sentence a person typed at a machine, reaches the right tool. Twice in two days
it did not, and both times Scott found it rather than the build.

So this is a written suite of requests - in the words operators, supervisors and
administrators use, Scott's own two conversations among them - each with one
expectation:

    propose(tool, args)   the request is a change: offer exactly that tool,
                          with the arguments the sentence named
    walk(guide)           the request is "show me": walk them to the control,
                          and to *that* control
    answer(contains)      the request is a question: answer it from the plant
    read(tool)            the request claims something changed, or asks what is
                          recorded: look before answering
    refuse(mentions)      the request is not this person's to make: say so, and
                          say who can

Two modes, and they measure different things.

**Scripted** (`--scripted`, and `tests/test_assist_suite_scripted.py`) asks: *is
this expectation reachable, through the real plumbing, for this role?* It runs
the real router, the real tool catalogue, the real tools against a real seeded
plant, the real surfaces, and the real conversation loop - with the model
replaced by a stand-in that plays the expectation. So it cannot tell you whether
a model would choose right, and it is not pretending to. What it catches is
everything around that choice: a request the guide router answers before the
agent is asked, a tool this role is not offered, arguments the tool will not
take, a walk with no surface behind it, a proposal reported as done, a
conversation the API will refuse from here on. Every one of those was a real
failure this week. It is deterministic, runs in CI, and costs nothing.

**Live** (`--live`) asks the other half: *does the model choose right?* The real
model, on a running plant, over its own HTTP API, scored by the same scorer and
written up in a dated file under `docs/ai/assist-eval/` the way a calibration
run is. It costs money, so it stops at a budget, and it is the steward's to run.

The scripted stand-in validates the message history the way the hosted API does
- every `tool_use` answered by a `tool_result` before the next thing the person
says. That is not decoration: a message typed over an open proposal leaves the
history malformed, and on 2026-09-26 that turned into `BadRequestError` for the
rest of Scott's page. A double that accepted what the real API refuses would
have scored that conversation as fine.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

#: Where the suite lives. One file per role, because a role is how a plant
#: thinks about who is asking, and a file per role is the thing a person can
#: read on a phone and add a case to.
SUITE = Path(__file__).resolve().parents[3] / "tests" / "assist_suite"

#: Where a run is written up, dated, the way `docs/ai/calibration/` is.
RESULTS = Path(__file__).resolve().parents[3] / "docs" / "ai" / "assist-eval"

#: The name the scripted plant answers to. Nothing outside the process running
#: it can reach it: an in-memory database and an in-process app.
SCRIPTED_PLANT = "assist-eval"

KINDS = ("propose", "walk", "answer", "read", "refuse")

#: A case the current code cannot pass yet names the handoff that will make it
#: pass. The ratchet works both ways: a `not_yet` case that starts passing
#: fails the test too, because a fix that is in and unrecorded is a fix nobody
#: knows they can rely on.
EXPECTED = ("pass", "not_yet")

#: "Show me" for a proposal that is already open, rather than for a catalogued
#: guide. The person is pointing at the thing on their screen. It is the id
#: the conversation itself serves that walk under - `agent.PROPOSAL_WALK`.
OWN_WALK = "proposal"

#: The conversation's own two walk-me tools, named here so the stand-in can
#: play the rule the prompt gives the model rather than a copy of it.
GUIDES_TOOL = "guides"
SHOW_GUIDE_TOOL = "show_guide"
#: And the exploration's own chart tool - `agent.DRAW_TOOL`. Written out here for
#: the same reason as the two above; a test pins the three against the loop's own
#: names, so a rename cannot leave this file quietly scoring nothing.
DRAW_TOOL = "draw"

#: The conversation's own tools. None of them reads the plant, so none of them is
#: one of a turn's `reads`: the walk goes on the person's screen and the chart is
#: scored as a chart.
_OURS = frozenset({GUIDES_TOOL, SHOW_GUIDE_TOOL, DRAW_TOOL})


class Invalid(Exception):
    """The suite itself is wrong. Raised with every problem, not the first."""


# --------------------------------------------------------------------- cases

@dataclass(frozen=True)
class Case:
    """One request as it was typed, and what the assistant owes in reply."""

    id: str
    role: str
    request: str
    expect: str
    #: Which agent this request is put to - `floor` (the assistant in the panel)
    #: or `analysis`. Declared once per file beside the role, because a file is
    #: one person asking one agent; a case does not override it. It keeps a
    #: default so that the four files written before there were two agents say
    #: nothing new.
    kind: str = "floor"
    screen: str = "/dashboard"
    #: propose / refuse: the tool that does it.
    tool: str | None = None
    #: The arguments the request itself names. Scored strictly by key, and by
    #: value except where `loose` says the sentence did not pin the value.
    args: dict = field(default_factory=dict)
    loose: tuple[str, ...] = ()
    #: The rest of a legal call - what the request implies but does not name, so
    #: the tool has everything it needs. Never scored: a case scores what the
    #: sentence said, and a name and a password are not in "create an account
    #: for Jo Patel as an operator".
    fills: dict = field(default_factory=dict)
    #: What a read would have to be narrowed by. Offered to every derived read
    #: and filtered to the arguments that tool actually takes, so one line
    #: serves `plant_settings(domain, find)`, `setting_changes(key)` and
    #: `order_detail(code)`.
    lookup: dict = field(default_factory=dict)
    #: walk: a guide id, or `proposal` for the open proposal's own walk.
    guide: str | None = None
    #: The step the walk must land on. `setting-in-focus` is the one Scott's
    #: phone never reached on 2026-09-25.
    anchor: str | None = None
    #: Guides this request must never be answered with. The three "show me"
    #: turns of 2026-09-26 were answered with two of these.
    not_guide: tuple[str, ...] = ()
    #: Walks that answer this question as well as prose would. "take me to
    #: scrap" has no tool that lists scrap; the form where scrap is booked is a
    #: right answer, and a case that scored it as "offered a walk instead of
    #: answering" was scoring the shape of the reply rather than its use.
    or_walk: tuple[str, ...] = ()
    #: Tools that must be read in this turn, before the answer.
    reads: tuple[str, ...] = ()
    #: Reads whose answer has to be *drawn* as well as read - the exploration's
    #: own `draw` tool, named by the tool whose payload the picture carries. Not
    #: a synonym for `reads`: a graph the agent read and described in prose is a
    #: shape the reader has to take on trust, and on 2026-09-29 the first live
    #: exploration did exactly that until it was asked for a picture outright.
    draws: tuple[str, ...] = ()
    #: ... or any one of these, when several reads would be honest answers.
    reads_any: tuple[str, ...] = ()
    #: Loose on prose: substrings, matched case-insensitively.
    contains: tuple[str, ...] = ()
    #: The same, for a fact a person may write down several honest ways. Each
    #: group is a list, and one of its renderings has to appear: the hold rules
    #: are "1,2,3,4" on the settings page and "1, 2, 3 and 4" in a sentence, and
    #: a suite that demanded the first was scoring the typography.
    contains_any: tuple[tuple[str, ...], ...] = ()
    #: Substrings that would make the answer wrong however it is phrased.
    never: tuple[str, ...] = ()
    #: refuse: what the refusal must say, so it names who can.
    mentions: tuple[str, ...] = ()
    #: The turn before this one, in the person's own words. `set it to4` only
    #: means anything over an open proposal.
    before_request: str | None = None
    before: tuple[dict, ...] = ()
    plan: tuple[dict, ...] = ()
    expected: str = "pass"
    handoff: str | None = None
    source: str | None = None
    note: str | None = None
    loose_about: str | None = None
    #: What has to be on the plant before this sentence means anything, as
    #: `"kind:code"` - or `"no kind:code"` where the sentence only makes sense
    #: on a plant that has *not* got one. A run arranges what it may and reports
    #: the rest as *not arranged*: counted apart from pass and fail, because a
    #: model that says "there is no MIX01 here" is right.
    #: `fsmes.lab.assist_fixtures` holds the kinds and what each one takes.
    requires: tuple[str, ...] = ()
    file: str = ""

    @property
    def over_proposal(self) -> bool:
        return bool(self.before_request or self.before)


_LISTS = ("loose", "not_guide", "or_walk", "reads", "reads_any", "draws", "contains",
          "never", "mentions", "requires")
#: Lists of lists: each group is a set of renderings, one of which must appear.
_GROUPS = ("contains_any",)
_FIELDS = {f for f in Case.__dataclass_fields__} | {"before", "plan"}


def _case(raw: dict, role: str, source_file: str, problems: list[str],
          kind: str = "floor") -> Case | None:
    where = f"{source_file}: case {raw.get('id', '(no id)')!r}"
    unknown = sorted(set(raw) - _FIELDS)
    if unknown:
        problems.append(f"{where}: fields the suite does not know: {', '.join(unknown)}")
    kwargs: dict[str, Any] = {"role": role, "kind": kind, "file": source_file}
    for key, value in raw.items():
        if key not in _FIELDS:
            continue
        if key in _LISTS:
            value = tuple(str(v) for v in value)
        elif key in _GROUPS:
            value = tuple(tuple(str(v) for v in group) for group in value)
        elif key in ("before", "plan"):
            value = tuple(dict(v) for v in value)
        kwargs[key] = value
    try:
        case = Case(**kwargs)
    except TypeError as exc:
        problems.append(f"{where}: {exc}")
        return None
    if case.expect not in KINDS:
        problems.append(f"{where}: expect {case.expect!r} is not one of {', '.join(KINDS)}")
    if case.expected not in EXPECTED:
        problems.append(f"{where}: expected {case.expected!r} is not one of {', '.join(EXPECTED)}")
    if case.expected == "not_yet" and not case.handoff:
        problems.append(f"{where}: a not_yet case must name the handoff that will make it pass")
    if case.expect == "propose" and not case.tool:
        problems.append(f"{where}: a propose case must name a tool")
    if case.expect == "refuse" and not (case.tool or case.guide):
        # An approval is refused without a tool ever being involved: there is
        # no approve tool and there never will be (decision 0035). Such a case
        # names the signing walk instead - the thing that was asked for and
        # said no to.
        problems.append(f"{where}: a refuse case must name the tool it was refused, "
                        f"or the signing walk it was refused")
    if case.expect == "walk" and not case.guide:
        problems.append(f"{where}: a walk case must name a guide, or {OWN_WALK!r}")
    if case.expect == "read" and not (case.reads or case.reads_any):
        problems.append(f"{where}: a read case must name the tool that must be read")
    if case.expect == "refuse" and not case.mentions:
        problems.append(f"{where}: a refuse case must say what the refusal has to name")
    if case.before_request and not case.before:
        problems.append(f"{where}: a case with a turn before it must say what that turn "
                        f"did, as a [[case.before]] step - otherwise the proposal this "
                        f"request is typed over is not actually open")
    for group in case.contains_any:
        if not group:
            problems.append(f"{where}: a contains_any group with nothing in it says nothing")
    for requirement in case.requires:
        bare = requirement[3:] if requirement.startswith("no ") else requirement
        kind, _, code = bare.partition(":")
        if not kind.strip() or not code.strip():
            problems.append(f"{where}: requires {requirement!r} is not \"kind:code\" "
                            f"(or \"no kind:code\")")
    for step in (*case.plan, *case.before):
        if not (step.get("read") or step.get("propose") or step.get("draw")
                or step.get("say")):
            problems.append(f"{where}: a step must read, propose, draw or say "
                            f"something: {step}")
        if step.get("draw") and step["draw"] not in (*case.reads, *case.reads_any):
            problems.append(f"{where}: a step draws {step['draw']!r}, which this "
                            f"case never reads")
    for tool in case.draws:
        # A picture is drawn from a read, so a case that demands one has to
        # demand the read behind it - otherwise the requirement is a chart of
        # nothing, which is the refusal `draw` already gives.
        if tool not in (*case.reads, *case.reads_any):
            problems.append(f"{where}: draws {tool!r}, which this case never reads")
    if case.draws and case.expect not in ("read", "answer"):
        # `draws` is scored where reads are scored, which is on a question. A
        # walk or a refusal that carried one would be an expectation nothing
        # ever checks, which is worse than not having it.
        problems.append(f"{where}: draws is scored on a question, and this case "
                        f"expects {case.expect!r}")
    if case.draws and case.kind != "analysis":
        # Only one kind holds the tool at all: the floor assistant answers beside
        # somebody standing at a machine, where a picture is a dashboard nobody
        # asked for (`docs/design/deep-analysis.md` §2).
        problems.append(f"{where}: only an analysis case can require a drawing - "
                        f"the {case.kind} assistant is not offered {DRAW_TOOL!r}")
    return case


def load(directory: Path | None = None) -> tuple[Case, ...]:
    """Every case in the suite, in role then file order. Raises `Invalid` with
    every problem it found, because fixing them one exception at a time is how
    a suite stops being edited."""
    directory = SUITE if directory is None else directory
    if not directory.is_dir():
        raise Invalid(
            f"no suite at {directory}. The suite is part of this repository rather "
            f"than the installed package - it is a set of test cases, and a plant has "
            f"no use for it. Run this from a checkout, or point --suite at one.")
    problems: list[str] = []
    cases: list[Case] = []
    for path in sorted(directory.glob("*.toml")):
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            problems.append(f"{path.name}: not readable as TOML: {exc}")
            continue
        role = raw.get("role")
        if not role:
            problems.append(f"{path.name}: no role = \"...\" line")
            continue
        # Which agent this file's requests are put to. Absent is the floor
        # assistant, because that is what every file written before there were
        # two of them was asking.
        kind = str(raw.get("kind") or "floor")
        from fsmes.services import agent as _agent

        if kind not in _agent.KINDS:
            problems.append(f"{path.name}: kind {kind!r} is not an agent this product "
                            f"has: {', '.join(sorted(_agent.KINDS))}")
            continue
        for entry in raw.get("case", []):
            case = _case(entry, role, path.name, problems, kind)
            if case is not None:
                cases.append(case)
    seen: dict[str, str] = {}
    for case in cases:
        if case.id in seen:
            problems.append(f"{case.file}: case id {case.id!r} is already used in {seen[case.id]}")
        seen[case.id] = case.file
    if problems:
        raise Invalid("\n".join(problems))
    return tuple(cases)


# ------------------------------------------------------- what the turn did

@dataclass
class Turn:
    """What the assistant did with one request, in the one shape both modes
    produce and the one shape the scorer reads."""

    kind: str        # guide | proposals | reply | unavailable | not_asked
    say: str = ""
    guide_id: str | None = None
    #: The walk that was put on the screen, when one was - so a case can say
    #: which control it has to land on, for a walk as well as for a card.
    walk: dict = field(default_factory=dict)
    proposals: tuple[dict, ...] = ()
    reads: tuple[str, ...] = ()
    #: What the reads actually returned. Scripted mode scores `contains`
    #: against this, because the prose is the stand-in's and scoring it would
    #: be scoring the suite against itself.
    facts: str = ""
    #: The charts drawn beside the answer, as the loop's own specs - so a case
    #: can ask for a picture and not only for a sentence. The tool a chart was
    #: drawn from is `spec["tool"]`.
    charts: tuple[dict, ...] = ()
    #: Tool names this role was offered at all.
    offered: frozenset[str] = frozenset()
    #: Words the loop handed back when a tool could not be used.
    refusals: tuple[str, ...] = ()
    #: True when a real model wrote `say`.
    from_model: bool = False
    usd: float = 0.0
    tokens: dict = field(default_factory=dict)
    note: str | None = None

    def observed(self) -> str:
        """One line for the report: what it did instead."""
        if self.kind == "guide":
            return f"walked them through the {self.guide_id!r} guide"
        if self.kind == "proposals":
            offered = "; ".join(f"{p['tool']}({_short_args(p.get('args') or {})})"
                                for p in self.proposals)
            return f"proposed {offered}"
        if self.kind == "unavailable":
            return f"the conversation went nowhere: {self.say}"
        if self.kind == "not_asked":
            # Never asked, so never paid for. A question a plant cannot be
            # asked is not a question the model got wrong.
            return self.say or "was not asked"
        read = ", ".join(self.reads) or "no tool"
        return f"read {read} and said: {self.say[:160]!r}"


def _short_args(args: dict) -> str:
    return ", ".join(f"{k}={str(v)[:40]}" for k, v in sorted(args.items()))


# --------------------------------------------------------------- the scorer

_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


def same_value(want: Any, got: Any) -> bool:
    """Strict on identity, forgiving about how a value is written.

    `1.33` and `"1.33"` are the same number; `"1,2"` and `"2, 1"` are the same
    pair of rule numbers, because a plant's rule list is a set and the sentence
    that named it did not pick an order; `"CR"` and `"cr"` are the same prefix.
    Anything else is compared as it was written.
    """
    if want is None or got is None:
        return want == got
    if isinstance(want, bool) or isinstance(got, bool):
        return bool(want) == bool(got)
    w, g = str(want).strip(), str(got).strip()
    if _NUMBER.match(w) and _NUMBER.match(g):
        return abs(float(w) - float(g)) < 1e-9
    if "," in w or "," in g:
        return {p.strip() for p in w.split(",") if p.strip()} == \
               {p.strip() for p in g.split(",") if p.strip()}
    return w.casefold() == g.casefold()


@dataclass
class Outcome:
    case: Case
    passed: bool
    why: tuple[str, ...]
    turn: Turn
    #: What the plant did not have. A case whose fixtures are not on the plant
    #: was never asked a fair question, and is reported apart from pass and
    #: fail rather than scored.
    missing: tuple[str, ...] = ()
    #: No account on this plant holds this case's role, so there was nobody to
    #: ask as. Reported apart from pass and fail for the same reason: a live run
    #: that asked an operator's questions as the administrator measured nothing
    #: about an operator - its refusal cases cannot refuse - and a number that
    #: counted them would be a number about nobody.
    no_account: bool = False

    @property
    def role(self) -> str:
        return self.case.role

    @property
    def arranged(self) -> bool:
        return not self.missing

    @property
    def counted(self) -> bool:
        """Whether this case is in the required score. A `not_yet` case is
        counted on its own, so the required number never drifts because
        somebody added a case for a thing that does not exist yet; a case the
        plant was not arranged for, or had nobody to ask as, is counted on its
        own for the same reason."""
        return self.case.expected == "pass" and self.arranged and not self.no_account


def _haystack(case: Case, turn: Turn) -> str:
    """Where prose is looked for. A real model's own sentence when there is
    one; in scripted mode, the facts the sentence would have had to come
    from - saying the stand-in said the right thing would be saying nothing.
    """
    if turn.from_model:
        return f"{turn.say}\n{' '.join(turn.refusals)}"
    if case.expect == "refuse":
        return " ".join(turn.refusals)
    return turn.facts


def score(case: Case, turn: Turn, missing: tuple[str, ...] = (), *,
          no_account: bool = False) -> Outcome:
    """Did this turn do what the case asked. Strict on identity, loose on prose.

    `missing` is what the plant did not have. The turn is still scored - the
    report says what it did either way - but the outcome is marked *not
    arranged*, and `counted` keeps it out of the required number. `no_account`
    says the same thing about the other half of a fair question: nobody on this
    plant holds the role, so nobody was asked.
    """
    why: list[str] = []
    hay = _haystack(case, turn).casefold()

    if turn.kind == "unavailable":
        why.append(f"the conversation could not continue: {turn.say}")
    if turn.kind == "not_asked":
        return Outcome(case=case, passed=False, turn=turn, missing=tuple(missing),
                       no_account=no_account,
                       why=(turn.say or "this plant has not got what the request names",))

    if case.expect == "propose":
        why += _score_propose(case, turn)
    elif case.expect == "walk":
        why += _score_walk(case, turn)
    elif case.expect == "refuse":
        why += _score_refuse(case, turn)
    else:                                       # answer, read
        why += _score_answer(case, turn)

    for want in case.contains:
        if want.casefold() not in hay:
            why.append(f"nothing in the reply carried {want!r}")
    for group in case.contains_any:
        if not any(want.casefold() in hay for want in group):
            why.append("nothing in the reply carried any of "
                       + ", ".join(repr(want) for want in group))
    for never in case.never:
        if never.casefold() in hay:
            why.append(f"the reply fell back on {never!r}")
    if case.not_guide and turn.guide_id in case.not_guide:
        why.append(f"it walked them through {turn.guide_id!r}, which this request is not about")
    return Outcome(case=case, passed=not why, why=tuple(why), turn=turn,
                   missing=tuple(missing), no_account=no_account)


def _score_propose(case: Case, turn: Turn) -> list[str]:
    why: list[str] = []
    if turn.kind != "proposals":
        return [f"nothing was proposed: it {turn.observed()}"]
    match = next((p for p in turn.proposals if p.get("tool") == case.tool), None)
    if match is None:
        return [f"{case.tool} was not proposed: it {turn.observed()}"]
    got = match.get("args") or {}
    for key, want in case.args.items():
        if key not in got:
            why.append(f"the proposal left out {key}, which the request named")
        elif key not in case.loose and not same_value(want, got[key]):
            why.append(f"{key} was {got[key]!r}, and the request said {want!r}")
    why += _score_reads(case, turn)
    why += _score_anchor(case, match)
    return why


def _score_anchor(case: Case, proposal: dict) -> list[str]:
    if not case.anchor:
        return []
    surface = proposal.get("surface")
    if not surface:
        return [f"there is no walk behind {proposal.get('tool')}, so "
                f"\"show me\" has nowhere to go"]
    anchors = [step.get("anchor") for step in surface.get("steps") or []]
    if case.anchor not in anchors:
        return [f"the walk's steps land on {anchors or 'nothing'}, not on {case.anchor!r}"]
    return []


def _score_walk(case: Case, turn: Turn) -> list[str]:
    if case.guide == OWN_WALK:
        # The card's own walk, asked for by name. Written first as "a fresh
        # proposal whose card carries the right surface", because that was
        # the only shape there was: the walk behind "Show me" could be
        # pressed and not handed over. It can be handed over now, and that
        # is the better answer to somebody who has just said they would
        # rather do it themselves - a second card would re-offer "Do it" and
        # cost them another press. So the walk has to be on their screen.
        if turn.kind != "guide":
            return [f"the walk they asked for was not put on their screen: "
                    f"it {turn.observed()}"]
        if turn.guide_id != OWN_WALK:
            return [f"a catalogued guide ({turn.guide_id!r}) answered instead of the "
                    f"proposal that was already open"]
        return _score_anchor(case, {"tool": OWN_WALK, "surface": turn.walk})
    if turn.kind != "guide":
        return [f"no walk was offered: it {turn.observed()}"]
    if turn.guide_id != case.guide:
        return [f"the walk was {turn.guide_id!r}, not {case.guide!r}"]
    return []


def _score_refuse(case: Case, turn: Turn) -> list[str]:
    why: list[str] = []
    proposed = [p.get("tool") for p in turn.proposals]
    if case.tool and case.tool in proposed:
        why.append(f"{case.tool} was proposed to somebody who may not do it")
    if case.tool and turn.offered and case.tool in turn.offered:
        why.append(f"{case.tool} is offered to this role, so nothing would refuse it")
    if case.guide and turn.kind == "guide" and turn.guide_id == case.guide:
        why.append(f"it walked them to the {case.guide!r} control, which their role is "
                   f"not shown - the answer is who may press it, not where it is")
    hay = _haystack(case, turn).casefold()
    for want in case.mentions:
        if want.casefold() not in hay:
            why.append(f"the refusal never says {want!r}")
    return why


def _score_answer(case: Case, turn: Turn) -> list[str]:
    why: list[str] = []
    if turn.kind == "guide":
        if turn.guide_id in case.or_walk:
            # A walk the case itself says is a right answer. There are no reads
            # behind a walk and there is nothing missing: the person asked to be
            # taken somewhere and was taken there.
            return []
        why.append(f"it offered the {turn.guide_id!r} walk instead of answering")
    why += _score_reads(case, turn)
    return why


def _score_reads(case: Case, turn: Turn) -> list[str]:
    why: list[str] = []
    for tool in case.reads:
        if tool not in turn.reads:
            why.append(f"it answered without reading {tool}")
    if case.reads_any and not set(case.reads_any) & set(turn.reads):
        why.append(f"it read none of {', '.join(case.reads_any)} - and said nothing about "
                   f"having no tool for it")
    why += _score_draws(case, turn)
    return why


def _score_draws(case: Case, turn: Turn) -> list[str]:
    """Did it draw what it read, without being asked to.

    The picture is half of the answer, so a case may require one by naming the
    tool whose payload it has to carry. The request itself never says "draw": a
    reader who has to ask for the chart is a reader who was handed a shape in
    prose, which is what the first live exploration did on 2026-09-29.
    """
    drawn = {str(chart.get("tool")) for chart in turn.charts}
    return [f"it read {tool} and described it instead of drawing it"
            for tool in case.draws if tool not in drawn]


# ----------------------------------------------------- the scripted stand-in

class HistoryRefused(Exception):
    """What the hosted API does to a malformed conversation, done here.

    Every `tool_use` block must be answered by a `tool_result` before the next
    thing the person says. On 2026-09-26 a message typed over an open proposal
    left one unanswered, and every later call in that conversation came back
    `BadRequestError` - which the panel reported as the brain not answering.
    """


def _kind_of(block: Any) -> str:
    return str(block.get("type") if isinstance(block, dict) else getattr(block, "type", ""))


def _id_of(block: Any, key: str) -> str | None:
    return block.get(key) if isinstance(block, dict) else getattr(block, key, None)


def check_history(history: list[dict]) -> None:
    """Refuse a conversation the hosted API would refuse."""
    awaiting: set[str] = set()
    for message in history:
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        if message.get("role") == "assistant":
            awaiting = {_id_of(b, "id") for b in blocks if _kind_of(b) == "tool_use"}
            awaiting.discard(None)
            continue
        answered = {_id_of(b, "tool_use_id") for b in blocks
                    if _kind_of(b) == "tool_result"}
        missing = awaiting - answered
        if missing:
            raise HistoryRefused(
                f"tool_use block(s) {sorted(missing)} were never answered with a "
                f"tool_result before the person spoke again")
        awaiting = set()


_USAGE = SimpleNamespace(input_tokens=0, output_tokens=0,
                         cache_read_input_tokens=0, cache_creation_input_tokens=0)


def _walk_plan(case: Case) -> tuple[dict, ...]:
    """What the stand-in plays when the person asked to be shown.

    The same rule the system prompt gives the model, and no more of one: call
    `guides()`, then `show_guide` the walk whose own words best match what was
    asked - except over an open proposal, where the answer is that proposal's
    own walk, `show_guide("proposal")`.

    The prompt's third arm - asked to be shown a change nothing has proposed
    yet, propose it, because the card's own "Show me" *is* the walk - is not
    here, and cannot be: it ends in a card, so such a case is a `propose` case
    and `plan_for` sends it to the proposing branch below.

    Deliberately not `case.guide`. A stand-in handed the answer would score
    nothing: the question scripted mode asks is whether the walk this person
    asked for is *reachable* - listed for their role, and served by an id the
    conversation will take. Picking it is the model's job and the live run's
    question.
    """
    from fsmes.services import assistant

    if case.over_proposal:
        # "could you show me where?" typed under a card. The walk being asked
        # for is the card's own; nothing in the catalogue is about it.
        return ({"read": GUIDES_TOOL, "args": {}},
                {"read": SHOW_GUIDE_TOOL, "args": {"id": OWN_WALK},
                 "say": "Here it is on the real screen."})
    offered = assistant.listed_guides(capabilities_of(case.role))
    chosen = assistant.lexical_match(case.request, offered)
    steps: list[dict] = [{"read": GUIDES_TOOL, "args": {}}]
    if chosen is None:
        # Nothing this person may be shown answers it. Say so, and let the
        # scorer report that the walk was never offered rather than the
        # stand-in inventing an id.
        steps.append({"say": "I have no walkthrough for that one."})
        return tuple(steps)
    steps.append({"read": SHOW_GUIDE_TOOL, "args": {"id": chosen["id"]},
                  "say": f"I can walk you through it - {chosen['title'].lower()}."})
    return tuple(steps)


def plan_for(case: Case) -> tuple[dict, ...]:
    """The steps the stand-in plays for this case.

    Authored when the case authors them, and otherwise derived from the
    expectation itself. Derived is the honest default: scripted mode is not
    asking whether a model would get here, it is asking whether getting here
    works - whether this role is offered the tool, whether the tool takes
    these arguments against a real plant, whether there is a walk behind it.
    """
    if case.plan:
        return case.plan
    call = {**case.args, **case.fills}
    if case.expect == "walk" and not case.tool:
        return _walk_plan(case)
    if case.expect == "refuse" and not case.tool:
        # An approval. There is no tool for it and there never will be
        # (decision 0035), so the walk to the signing control is what is
        # asked for - and refused, by name, when it is not theirs to press.
        return _walk_plan(case)
    if case.expect in ("propose", "walk") and case.tool:
        # A request for a change is a proposal, not a question: the plan ends in
        # the card, and the sentence beside it neither asks for permission
        # ("want me to go ahead?" - live, 2026-09-26 21:05) nor names the tool
        # it is about to call. Those are two of the three rules the prompt gives
        # the model, and `tests/test_the_assistant_proposes_rather_than_asking.py`
        # holds every reply this stand-in produces to them.
        #
        # And an optional argument is not a question, which is the same rule met
        # from the other side. `call` is what the sentence named plus what it
        # implied, and nothing else: an optional argument the person did not give
        # - `set_machine_state(reason=None)`, live 2026-09-27 11:2x - is absent
        # from the card and absent from the sentence, rather than asked for in
        # front of it. `tests/test_the_assistant_reads_before_it_says_no.py`
        # holds every reply to that one, and holds every `propose` case to
        # naming what its tool requires, which is what makes the rule fair.
        return (
            *(_read_step(tool, case) for tool in case.reads),
            {"propose": case.tool, "args": call,
             "say": "Here is what I would change - nothing has changed yet."},
        )
    if case.expect == "refuse" and case.tool:
        return (
            {"propose": case.tool, "args": call},
            {"say": "That one is not yours to do here."},
        )
    # Read before you deny, and before you answer at all: an `answer` or `read`
    # case reads first and speaks afterwards, and the sentence it speaks reports
    # what came back rather than what the stand-in believes. That is the third
    # rule mirrored - live on 2026-09-27 the assistant said `nc_code_prefix`
    # "isn't configurable" and then that it "isn't a real control", with no tool
    # call behind either, and both were about a Quality setting this product
    # ships. The same test file holds every reply this stand-in produces to it.
    reads = case.reads or case.reads_any[:1]
    return (*(_read_step(tool, case) for tool in reads),
            {"say": "Answered from what I read."})


def _read_step(tool: str, case: Case) -> dict:
    return {"read": tool, "args": narrow(tool, case.lookup)}


def narrow(tool: str, lookup: dict) -> dict:
    """`lookup` cut down to the arguments this tool takes.

    One line in a case serves every read it names, because "which setting, which
    order" is one question however many tools are asked it.
    """
    if not lookup:
        return {}
    from fsmes.services import agent

    for listed in agent.registry_tools():
        if listed.name == tool:
            props = (listed.input_schema or {}).get("properties") or {}
            return {k: v for k, v in lookup.items() if k in props}
    return {}


def _blocks(step: dict, index: int) -> tuple[list[Any], str]:
    out: list[Any] = []
    if step.get("say"):
        out.append(SimpleNamespace(type="text", text=str(step["say"])))
    if step.get("draw"):
        # `draw = "trace_graph"` and `shape = "graph"`: the picture, named by the
        # tool whose answer it carries. The stand-in says it the way a plan can
        # say it at all - by tool name - because the `tool_use` ids in a scripted
        # conversation are this function's own and a plan cannot know them. That
        # is the same form a live model gets to use since 2026-09-29, so the two
        # modes exercise one code path.
        args = {"from": str(step["draw"]), "shape": str(step.get("shape") or "graph")}
        if step.get("title"):
            args["title"] = str(step["title"])
        out.append(SimpleNamespace(type="tool_use", id=f"s{index}", name=DRAW_TOOL,
                                   input=args))
        return out, "tool_use"
    tool = step.get("read") or step.get("propose")
    if tool:
        out.append(SimpleNamespace(type="tool_use", id=f"s{index}", name=tool,
                                   input=dict(step.get("args") or {})))
        return out, "tool_use"
    return out, "end_turn"


def scripted_model(plan: tuple[dict, ...]):
    """A model that plays a plan and refuses a malformed conversation."""
    steps = list(plan)

    def call(session) -> Any:
        check_history(session.history)
        if not steps:
            return SimpleNamespace(content=[SimpleNamespace(type="text",
                                                            text="Nothing further.")],
                                   stop_reason="end_turn", usage=_USAGE)
        blocks, stop = _blocks(steps.pop(0), len(steps))
        return SimpleNamespace(content=blocks, stop_reason=stop, usage=_USAGE)

    return call


def capabilities_of(role: str) -> set[str]:
    """What this role may do, as the product ships it.

    The built-in bundles, not a plant's redefinition of them: a suite whose
    answers moved when somebody edited a role would be measuring the plant
    rather than the assistant.
    """
    from fsmes.services import capabilities as caps

    spec = caps.BUILTIN_ROLES.get(role)
    if spec is None:
        raise Invalid(f"no role {role!r} in this product: "
                      f"{', '.join(sorted(caps.BUILTIN_ROLES))}")
    return set(spec["capabilities"])


# ------------------------------------------------------------ reading a turn

def _facts_and_reads(session, writes=frozenset()) -> tuple[tuple[str, ...], str]:
    """Which tools were read in this turn, and what came back - out of the
    conversation's own history, which is where the model read it.

    A write tool whose preview came back an error leaves a transcript line with
    no `write` marker on it, so the catalogue's own answer to "is this a write"
    is what decides, not the shape of the line.

    The conversation's own tools are not reads of the plant. `draw` is scored as
    a chart (`Turn.charts`) and never counted here: a case that named its reads
    exactly would otherwise start failing the day the agent drew one of them.
    """
    reads = tuple(entry["tool"] for entry in session.transcript
                  if not entry.get("write") and entry["tool"] not in writes
                  and entry["tool"] not in _OURS)
    facts: list[str] = []
    for message in session.history:
        content = message.get("content")
        if isinstance(content, list):
            facts += [str(b.get("content")) for b in content
                      if isinstance(b, dict) and b.get("type") == "tool_result"]
    return reads, "\n".join(facts)


def _refusals(session) -> tuple[str, ...]:
    return tuple(str(e.get("summary")) for e in session.transcript if not e.get("ok"))


def turn_from_reply(reply: dict, session=None, *, offered=frozenset(),
                    writes=frozenset(), from_model: bool = False) -> Turn:
    """One reply from the agent - scripted or live - in the scorer's shape."""
    reads: tuple[str, ...] = tuple(e["tool"] for e in reply.get("transcript") or []
                                   if not e.get("write") and e["tool"] not in writes
                                   and e["tool"] not in _OURS)
    facts = ""
    refusals = tuple(str(e.get("summary")) for e in reply.get("transcript") or []
                     if e.get("ok") is False)
    if session is not None:
        reads, facts = _facts_and_reads(session, writes)
        refusals = _refusals(session)
    guide = reply.get("guide") or {}
    return Turn(kind=reply.get("kind", "reply"), say=reply.get("say") or "",
                guide_id=guide.get("id"), walk=guide,
                proposals=tuple(reply.get("proposals") or ()),
                reads=reads, facts=facts, charts=tuple(reply.get("charts") or ()),
                offered=frozenset(offered),
                refusals=refusals, from_model=from_model)


# ----------------------------------------------------------------- the report

def tally(outcomes: tuple[Outcome, ...]) -> dict:
    """Pass rate per role. Four buckets, and every case is in exactly one of
    them: required, `not_yet`, *not arranged* - the plant did not have what the
    sentence names - and *no account*, where this plant has nobody holding the
    role. Nothing about the model was measured in either of the last two."""
    roles: dict[str, dict] = {}
    for outcome in outcomes:
        row = roles.setdefault(outcome.role, {"required": 0, "passed": 0, "not_yet": 0,
                                              "not_yet_passing": 0, "not_arranged": 0,
                                              "no_account": 0})
        if outcome.no_account:
            row["no_account"] += 1
        elif not outcome.arranged:
            row["not_arranged"] += 1
        elif outcome.counted:
            row["required"] += 1
            row["passed"] += int(outcome.passed)
        else:
            row["not_yet"] += 1
            row["not_yet_passing"] += int(outcome.passed)
    required = sum(r["required"] for r in roles.values())
    passed = sum(r["passed"] for r in roles.values())
    return {"roles": roles, "required": required, "passed": passed,
            "not_yet": sum(r["not_yet"] for r in roles.values()),
            "not_yet_passing": sum(r["not_yet_passing"] for r in roles.values()),
            "not_arranged": sum(r["not_arranged"] for r in roles.values()),
            "no_account": sum(r["no_account"] for r in roles.values()),
            "total": len(outcomes)}


def suite_commit() -> str:
    """The commit this suite was read from, when there is a repository to ask."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=Path(__file__).resolve().parents[3],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def report(outcomes: tuple[Outcome, ...], *, mode: str, model: str | None = None,
           plant: str | None = None, plant_commit: str | None = None,
           run: dict | None = None, when: datetime | None = None) -> str:
    """One run, written the way a calibration run is written: what was asked,
    what it cost, and every case that did not pass with what happened instead.
    """
    when = when or datetime.now(UTC)
    counts = tally(outcomes)
    run = run or {}
    apart = counts["not_arranged"]
    unarranged = "" if not apart else (
        f" {apart} {'case was' if apart == 1 else 'cases were'} not arranged — this "
        f"plant has not got what the request names, so "
        f"{'it is' if apart == 1 else 'they are'} not scored.")
    nobody = counts.get("no_account") or 0
    unasked = "" if not nobody else (
        f" {nobody} {'case was' if nobody == 1 else 'cases were'} not asked at all — "
        f"this plant has no account holding the role, and asking somebody else's "
        f"questions as the wrong person measures nobody.")
    lines = [f"# Assistant faithfulness — {when.date().isoformat()}", "",
             f"*{mode} run, {when.isoformat(timespec='seconds')}. "
             f"{counts['passed']} of {counts['required']} required cases pass; "
             f"{counts['not_yet']} are marked `not_yet`.{unarranged}{unasked}*", ""]
    lines += ["| | |", "|---|---|",
              f"| Mode | {mode} |",
              f"| Model | {model or 'none — the model is scripted'} |",
              f"| Plant | {plant or SCRIPTED_PLANT} |",
              f"| Plant commit | {plant_commit or _no_plant_commit(mode)} |",
              f"| Suite commit | {suite_commit()} |"]
    if run.get("usd") is not None and mode == "live":
        tokens = run.get("tokens") or {}
        lines += [f"| Cost | ${run['usd']:.4f} of a ${run.get('max_usd', 0):.2f} budget "
                  f"(${run.get('month_usd', 0):.2f} of ${run.get('cap_usd', 0)} this month) |",
                  f"| Tokens | {', '.join(f'{k} {v:,}' for k, v in sorted(tokens.items())) or 'not reported'} |"]
    asked_as = (run.get("asked_as") or {}) if mode == "live" else {}
    lines += ["", "## Per role", "",
              "| Role | Asked as | Required | Pass | Rate | not_yet | of those, passing "
              "| not arranged | no account |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for role in sorted(counts["roles"]):
        row = counts["roles"][role]
        rate = f"{100 * row['passed'] / row['required']:.0f}%" if row["required"] else "—"
        # Which account answered for this role. A run that signed in as one
        # person for every role is a run whose per-role numbers are about one
        # person, and that is the fact a reader needs first.
        who = asked_as.get(role) or ("—" if mode != "live" else "nobody")
        lines.append(f"| {role} | {who} | {row['required']} | {row['passed']} | {rate} | "
                     f"{row['not_yet']} | {row['not_yet_passing']} | {row['not_arranged']} "
                     f"| {row.get('no_account', 0)} |")

    if mode == "live":
        lines += ["", *arrangement(run)]

    failed = [o for o in outcomes if o.counted and not o.passed]
    lines += ["", "## Required cases that did not pass", ""]
    if not failed:
        lines += ["None.", ""]
    for outcome in failed:
        lines += [f"### `{outcome.case.id}` ({outcome.case.role})", "",
                  f"> {outcome.case.request}", "",
                  f"- Expected: {_expectation(outcome.case)}",
                  f"- It {outcome.turn.observed()}"]
        lines += [f"- {why}" for why in outcome.why]
        lines.append("")

    unmade = [o for o in outcomes if not o.arranged and not o.no_account]
    if unmade:
        # Why a fixture could not be arranged is the harness's knowledge, not
        # this module's: it comes in on the run, so the scorer stays a scorer.
        reasons = run.get("why_not") or {}
        fixes = run.get("what_would") or {}
        lines += ["## Not arranged", "",
                  "These were not scored. The plant has not got what the request names, "
                  "so nothing about the model's choice was measured either way. The last "
                  "column is what would make each one arrangeable - so that a reader can "
                  "tell a plant that is short of a line from a suite asking for something "
                  "no plant could give it.", "",
                  "| Case | Role | The plant has not got | Why not | What would make it "
                  "arrangeable |", "|---|---|---|---|---|"]
        for outcome in unmade:
            for requirement in outcome.missing:
                lines.append(f"| `{outcome.case.id}` | {outcome.case.role} | "
                             f"`{requirement}` | {reasons.get(requirement, '')} "
                             f"| {fixes.get(requirement, '')} |")
        lines.append("")

    unasked_cases = [o for o in outcomes if o.no_account]
    if unasked_cases:
        lines += ["## Not asked — no account for the role", "",
                  "This plant has no account holding these roles, so these cases were "
                  "not asked and nothing was spent on them. They were not asked as "
                  "somebody else either: an operator's refusal cases cannot refuse when "
                  "the administrator is typing.", "",
                  "| Case | Role |", "|---|---|"]
        lines += [f"| `{o.case.id}` | {o.case.role} |" for o in unasked_cases]
        lines.append("")

    waiting = [o for o in outcomes if o.arranged and not o.no_account and not o.counted]
    lines += ["## Marked `not_yet`", ""]
    if not waiting:
        lines.append("None.")
    else:
        lines += ["| Case | Role | Waiting on | Passing already |", "|---|---|---|---|"]
        for outcome in waiting:
            lines.append(f"| `{outcome.case.id}` | {outcome.case.role} | "
                         f"`{outcome.case.handoff}` | {'yes' if outcome.passed else 'no'} |")
    if run.get("not_run"):
        lines += ["", "## Not run (the budget stopped the run)", ""]
        lines += [f"- `{case.id}` ({case.role})" for case in run["not_run"]]
    lines += ["", "---", "",
              "Written by `fsmes assist eval`. A bug found in the assistant becomes a "
              "case in `tests/assist_suite/` before it becomes a fix — see "
              "[ASSIST-EVAL.md](../ASSIST-EVAL.md).", ""]
    return "\n".join(lines)


def _no_plant_commit(mode: str) -> str:
    """A result nobody can date to a build cannot be compared to the next one."""
    if mode == "scripted":
        return "the same checkout as the suite — the plant is built in this process"
    return "not reported by the plant; pass --plant-commit"


def arrangement(run: dict) -> list[str]:
    """What a run put on the plant, as lines for the result file.

    Two halves, kept apart because two different accounts wrote them and the
    operator of a scored plant is entitled to know which. Written even when
    nothing was put there, because "nothing was arranged" is the fact a reader
    most needs when every case comes back not arranged.
    """
    lines = ["## What this run put on the plant", "",
             "### As the agent, through the assistant's own tools", ""]
    lines += _put_there(run.get("made") or [], run.get("already") or [],
                        run.get("refused") or {},
                        nothing="Nothing. This run was given `--no-arrange`.",
                        verb="arranged",
                        closing="Nothing here is removed afterwards: an MES does not "
                                "delete an audited record, and neither does this.")
    lines += ["### The master data, as the person signed in", ""]
    if not run.get("seeding"):
        lines += ["Nothing. `--seed-masterdata` was not given, so this run left the "
                  "plant's own master data alone — materials, equipment, routings, "
                  "lots and specifications are the plant's, not a suite's.", ""]
        return lines
    seeded = run.get("seeded") or {}
    lines += ["Put there over the plant's own API by the account this run signed in "
              "as — not by the AGENT account, which may not define master data "
              "(decision 0035).", ""]
    lines += _put_there(seeded.get("made") or [], seeded.get("already") or [],
                        seeded.get("refused") or {},
                        nothing="Nothing: every code was already there.",
                        verb="put there",
                        closing="This API has no way to remove any of it — there is no "
                                "`DELETE` for equipment, materials, routings, "
                                "specifications or lots — so a seeded plant keeps it "
                                "until it is rebuilt or restored from a backup taken "
                                "first.")
    return lines


def _put_there(made, already, refused: dict, *, nothing: str, verb: str,
               closing: str) -> list[str]:
    if not (made or already or refused):
        return [nothing, ""]
    lines = [f"- Put there by this run: {_codes(made) or 'nothing'}",
             f"- Already there, and left untouched: {_codes(already) or 'nothing'}"]
    for requirement, why in sorted(refused.items()):
        lines.append(f"- Could not be {verb} — `{requirement}`: {why}")
    lines += ["", f"{closing} See [ASSIST-EVAL.md](../ASSIST-EVAL.md) for what a person "
                  f"does about each one.", ""]
    return lines


def _codes(requirements) -> str:
    return ", ".join(f"`{r}`" for r in requirements)


def _expectation(case: Case) -> str:
    if case.expect == "propose":
        return f"`propose {case.tool}` with {_short_args(case.args) or 'no arguments'}"
    if case.expect == "walk":
        return (f"the open proposal's own walk, landing on `{case.anchor}`"
                if case.guide == OWN_WALK else f"the `{case.guide}` walk")
    if case.expect == "refuse":
        return f"a refusal naming {', '.join(repr(m) for m in case.mentions)}"
    if case.expect == "read":
        return f"a read of {', '.join(case.reads or case.reads_any)} before answering"
    wants = ", ".join(repr(c) for c in case.contains)
    return f"an answer carrying {wants}" if wants else "an answer from the plant"


def write_report(text: str, *, when: datetime | None = None,
                 directory: Path | None = None) -> Path:
    when = when or datetime.now(UTC)
    directory = RESULTS if directory is None else directory
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{when.date().isoformat()}.md"
    path.write_text(text, encoding="utf-8")
    return path


def as_json(outcomes: tuple[Outcome, ...]) -> str:
    """The same run as data, for a next month that wants to compare."""
    return json.dumps(
        {"tally": tally(outcomes),
         "cases": [{"id": o.case.id, "role": o.case.role, "expect": o.case.expect,
                    "expected": o.case.expected, "handoff": o.case.handoff,
                    "arranged": o.arranged, "missing": list(o.missing),
                    "no_account": o.no_account,
                    "passed": o.passed, "why": list(o.why),
                    "observed": o.turn.observed()} for o in outcomes]},
        indent=1, default=str)
