"""The agent behind the floor assistant.

A person types what they want done. A cloud model works the plant's own
tools - the same registry the MCP server serves - to find the facts and to
propose the change. Three rules, all deliberate:

Reads are free. The model looks up machines, specifications and orders
without asking, because an assistant that asks permission to *look* is
unusable, and reads cannot hurt a plant.

Writes are proposals. A tool that changes the plant (anything with a
`dry_run` parameter) is run as `dry_run=True` and the preview is handed to
the screen; the loop pauses until the person confirms or declines. A
confirmed write runs as AGENT on behalf of the signed-in person with the
proposal id as its idempotency key, so a double click cannot double-book.
Same discipline as the MCP tools and the write-back queue, for the same
reason: a plant is not a place for a model to act unattended.

The person's capabilities gate what may be proposed. The API enforces the
AGENT role on top, so a tool the agent account cannot use comes back as a
readable refusal rather than a surprise.

Everything is optional. No key, no `anthropic` package, or the month's
budget spent: `available()` says why, the panel says so, and the qwen
assistant carries on answering and guiding as before.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

MODEL = os.environ.get("MES_AGENT_MODEL", "claude-sonnet-5")
EFFORT = os.environ.get("MES_AGENT_EFFORT", "low")
# What one conversation may spend. These three were literals here until the
# configuration audit of 2026-09-21 named them; they are now `[admin]
# agent_max_rounds`, `agent_session_ttl_seconds` and `agent_result_limit`, and
# every default below is the number that was here. A conversation reads them
# once, when it is opened, and keeps what it opened with - a budget that moved
# under a turn already in flight would be a conversation cut off mid-sentence
# by somebody else's save.
MAX_ROUNDS = 12            # model turns per person message before it must stop
SESSION_TTL = 30 * 60      # seconds a conversation lives without a message
# Characters of a tool result the model sees. Six thousand until 2026-09-29,
# raised on two pieces of measured evidence rather than on a feeling:
# Administration's settings list pages twelve of thirty-nine rows at six
# thousand (`test_every_settings_list_fits_in_one_tool_result`), and the trace
# graph's FRAME alone - the window, the empty node kinds and their sentences,
# the refused measures - is about 2,600 characters of the 3,600 a list may use,
# which left three of thirteen nodes in the answer. An exploration that reads a
# graph and then follows the thread cannot do either on a page that small. The
# paging stays exactly as it was: every list still states its total and names
# the call that reaches the rest, and this raises the budget rather than
# removing the honesty.
RESULT_LIMIT = 12000

# List prices per million tokens: input, output, cache read, cache write.
# Estimates only - the Console is the bill. Updated 2026-09-03.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-5": (2.0, 10.0, 0.20, 2.50),
    "claude-opus-5": (5.0, 25.0, 0.50, 6.25),
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25),
}

USAGE_FILE = Path(os.environ.get(
    "MES_AGENT_USAGE_FILE", Path.home() / ".local" / "share" / "fsmes" / "agent-usage.jsonl"))

#: One line per turn of every conversation - what the person got back, what it
#: cost, and what went wrong when something did. `USAGE_FILE` beside it is the
#: bill and nothing else; this is the record of what the assistant actually
#: did, which is the only way to find out afterwards that thirty turns reached
#: the model five times. Nothing here that is not already in the transcript the
#: panel shows the person.
TURN_FILE = Path(os.environ.get(
    "MES_AGENT_TURN_FILE", Path.home() / ".local" / "share" / "fsmes" / "agent-turns.jsonl"))

# Tools the plant's own process has no use for.
HIDDEN = {"list_plants"}

# What a person must be allowed to do for a write tool to be proposed on
# their behalf, read from the `require(...)` of the route the tool sends to.
#
# Every write tool is here or in `PER_CALL_NEEDS`, and
# `test_every_write_tool_names_the_capability_its_route_demands` fails on one
# that is not. That was not true until 2026-09-27: nine write tools were in
# neither, and a tool in neither is offered to anybody holding `plant.read`.
# Nothing was written - the route refused the call when it arrived - but the
# model was shown a tool it would be refused on, and the person got a refusal
# for a thing their assistant had just offered them.
#
# A capability named here must exist in capabilities.CAPABILITIES (a test
# checks) and must be one the tool's own routes demand (the ratchet checks).
NEEDS: dict[str, str] = {
    "record_check": "quality.record",
    "close_nonconformance": "quality.close_nc",
    "review_nonconformance": "quality.close_nc",
    "disposition_nonconformance": "quality.close_nc",
    # A supervisor's dispositions, each gated on the same capability closing a
    # non-conformance is: recording what a gauge did when it was checked,
    # putting a certificate in force, and quarantining, releasing or scrapping
    # an identified unit.
    "calibrate_gauge": "quality.close_nc",
    "issue_certificate": "quality.close_nc",
    "issue_pallet_certificate": "quality.close_nc",
    "set_unit_status": "quality.close_nc",
    "produce_units": "production.book",
    "book_output": "production.book",
    # Starting and completing a step of an order: the operator's most basic
    # act, and gated by the route on the same capability booking output is.
    "start_operation": "production.book",
    "complete_operation": "production.book",
    # Identified units: what a marker or a palletizer sends, and what an
    # operator does by hand at the packing station. The same capability
    # booking output is gated on, because it is the same act counted a
    # serial at a time.
    "produce_batch": "production.book",
    "pack_unit": "production.book",
    "issue_material": "production.consume",
    "create_lot": "production.consume",
    "set_machine_state": "equipment.state",
    "create_order": "orders.create",
    "order_action": "orders.release",
    "perform_maintenance": "maintenance.perform",
    "raise_corrective_maintenance": "maintenance.perform",
    "raise_due_maintenance": "maintenance.perform",
    "create_maintenance_plan": "maintenance.plan",
    "create_material": "masterdata.write",
    "create_equipment": "masterdata.write",
    "register_gauge": "masterdata.write",
    "create_routing": "masterdata.write",
    "create_spec": "masterdata.write",
    "add_bom_component": "masterdata.write",
    # A person on the personnel register without a sign-in. The register is
    # who the plant may name as having done something, so it is gated where
    # accounts are.
    "add_person": "users.manage",
    "set_home_equipment": "users.manage",
    "create_user": "users.manage",
    "create_role": "users.manage",
    "update_role": "users.manage",
    "assign_role": "users.manage",
    "create_document": "documents.write",
    "draft_instruction": "documents.write",
    "revise_document": "documents.write",
    "draft_trigger": "triggers.write",
    "draft_downtime_reason": "process.define",
    "draft_nc_severity": "quality.define",
    "propose_adjustment": "adjustments.propose",
    # Putting a dead ERP message back in the queue. Gated where closing an
    # order is: the message is usually the confirmation of one.
    "erp_retry": "orders.close",
    "plan_order": "scheduling.plan",
    "plan_all_orders": "scheduling.plan",
    "add_shift": "scheduling.plan",
    "add_calendar_exception": "scheduling.plan",
}

#: Write tools whose capability is not one word, and why each is not in
#: `NEEDS`. `NEEDS` is one capability per tool, read when the catalogue is
#: built and before any argument exists; `write_plant_setting` is gated by the
#: `define` of the `ConfigSection` the key belongs to, which is a property of
#: an argument rather than of the tool. Naming any one capability here would
#: be naming the wrong one for every other domain, so the tool is deliberately
#: absent from `NEEDS` and listed here with the reason, and it is gated twice
#: instead: offered only to somebody who holds at least one capability that
#: could gate a setting (`needs_any` below), and refused key by key at the
#: API, which reads the owning section exactly the same way.
PER_CALL_NEEDS: dict[str, str] = {
    "write_plant_setting":
        "the capability is the `define` of the ConfigSection the key belongs "
        "to - one tool serves every domain's live settings and no single "
        "capability gates them all, so it is read per call from the registry",
}


def needs_any(tool: str) -> set[str] | None:
    """For a tool in `PER_CALL_NEEDS`, the capabilities any one of which could
    gate a call to it. `None` for every other tool, which is what tells the
    catalogue that `NEEDS` is the whole answer.

    Read from the registry the API reads, not written out again here, so the
    next domain whose section becomes live is covered without a code change.
    """
    if tool not in PER_CALL_NEEDS:
        return None
    from fsmes.services import plant_settings

    return {section.define for section in plant_settings.live_sections()
            if section.define}

# ------------------------------------------- what is not this person's to do


def withheld(tool: str, capabilities: set[str], args: dict | None = None,
             roles: dict[str, list[str]] | None = None) -> dict | None:
    """Why one of the plant's write tools is not this person's to use, in words
    that name who it belongs to. `None` when want of a capability is not the
    reason - a tool they do hold, or a name that is not a tool at all.

    The catalogue is filtered per capability, so the model is never shown a
    tool the person cannot use. That is right, and it is also why a refusal
    used to be useless: asked to close a non-conformance, an operator's
    assistant reached for a tool that was not there and got back *"no tool
    named 'close_nonconformance' is available to this person"* - a fact about
    the catalogue, and no use to somebody standing in front of the
    non-conformance. This is the same lookup the signing walks answer their
    half of the question with (`assistant.signing_guides`), one sentence written
    once in `capabilities.not_yours`.
    """
    from fsmes.services import capabilities as caps

    need = NEEDS.get(tool)
    if need is not None:
        if need in capabilities:
            return None
        return {"error": caps.not_yours(need, roles=roles),
                "capability": need, "held_by": caps.holders(need, roles)}
    if tool in PER_CALL_NEEDS:
        return _withheld_setting(tool, capabilities, args or {}, roles)
    return None


def _withheld_setting(tool: str, capabilities: set[str], args: dict,
                      roles: dict[str, list[str]] | None) -> dict | None:
    """The same answer for the one tool whose capability is an argument.

    `write_plant_setting` is gated by the `define` of the `ConfigSection` the
    key is listed under (`PER_CALL_NEEDS`), so the capability to name is read
    per call from the same registry the API reads it from. A call that names no
    key, or a key this version does not have, has no one capability behind it -
    and then the honest answer counts the ones that gate a setting rather than
    picking one of them.
    """
    from fsmes.services import capabilities as caps
    from fsmes.services import plant_settings

    try:
        section, _table, _key = plant_settings.owner(args.get("domain") or "",
                                                     args.get("key") or "")
    except plant_settings.Unknown:
        section = None
    if section is not None and section.define:
        if section.define in capabilities:
            return None
        return {"error": caps.not_yours(section.define, roles=roles),
                "capability": section.define,
                "held_by": caps.holders(section.define, roles)}
    could = sorted(needs_any(tool) or ())
    if [c for c in could if c in capabilities]:
        return None
    return {"error": (f"Changing a plant setting needs the capability of the section "
                      f"its key is listed under, and you hold none of the {len(could)} "
                      f"that gate one ({', '.join(could)}). Name the setting and I can "
                      f"say who changes it."),
            "capability": None, "held_by": []}


def no_write_note(kind: Kind) -> str:
    """What a kind that holds no write tool is told about its own catalogue.

    The floor assistant's note below is a list of the actions *this person* may
    not take and who may. For a kind with no write tools at all that list would
    be every write in the product, and it would be the wrong sentence anyway:
    nothing is being withheld from an analyst for want of a capability - the
    kind is a reader, and the change belongs to the assistant in the panel.

    Every number in it is counted from the registry, and they add up to the
    whole of it, because a sentence that says "you hold 53 tools" and leaves
    the reader to wonder about the rest is the kind of arithmetic this product
    does not leave lying around (house rule 2).
    """
    def props(tool):
        return (tool.input_schema or {}).get("properties") or {}

    every = [t for t in registry_tools() if t.name not in HIDDEN]
    writes = [t.name for t in every if "dry_run" in props(t)]
    plantless = [t.name for t in every if "plant" not in props(t)]
    mine = [t.name for t in every if t.name not in writes and t.name not in plantless]
    return (f"\n\nYou hold {len(mine)} of this plant's {len(every)} tools, and every one of them "
            f"is a read. The {len(writes)} that change anything are not in your catalogue and "
            f"cannot be put there; what is left ({', '.join(sorted(plantless))}) takes no plant "
            f"argument and is not about a plant at all. So asked to change something, say that "
            f"you only read, and that the assistant in the panel can propose it to whoever signs "
            f"it - never that a tool is missing, never that it is broken, and never that you will "
            f"do it later.")


def not_this_kinds(tool: str, kind: Kind) -> dict | None:
    """Why a tool this kind reached for is not in its catalogue, when the reason
    is the kind rather than the person.

    `withheld` answers the other question - *"this person does not hold the
    capability, and here is who does"* - and for a kind with no write tools at
    all that answer would be true and misleading in the same sentence: it would
    read as though a supervisor could ask the analyst to do it. The reason is
    the agent, not the person asking, so the sentence says the agent.
    """
    if kind.writes:
        return None
    if tool in NEEDS or tool in PER_CALL_NEEDS:
        # The order of the clauses is load-bearing: a tool result is summarised
        # to 160 characters in the transcript the panel shows and in the turn
        # record, so what a reader can least afford to lose comes first - the
        # agent holds none of these, and who can.
        return {"error": (f"{tool} changes the plant, and {kind.title} holds no tool that "
                          f"does - the assistant in the panel can propose it to whoever "
                          f"signs it, and that is the answer. Say that you only read; never "
                          f"that there is no way to do it, and never that the person's own "
                          f"role is what stopped it, because it is not."),
                "reads_only": True, "agent": kind.name}
    return None


def withheld_note(capabilities: set[str],
                  roles: dict[str, list[str]] | None = None) -> str:
    """Every write action this person is not offered, one line each, with the
    capability it needs and who holds it.

    This is the half a tool could not answer. A `who_can(action)` tool was the
    other way to do it, and this is cheaper and cannot be forgotten: the model
    only calls a tool it thinks of calling, and the moment it needs this is the
    moment it has decided there is nothing to call. A few hundred tokens in the
    cached prefix - which is already per-capability-set, because the catalogue
    after it is - say it up front instead, and `withheld` above says it again at
    call time for a model that reaches for the tool anyway.
    """
    if "plant.read" not in capabilities:
        return ""
    from fsmes.services import capabilities as caps

    offered = {t["name"] for t in catalogue(capabilities)}
    every = sorted(set(NEEDS) | set(PER_CALL_NEEDS))
    lines = []
    for tool in every:
        if tool in offered:
            continue
        need = NEEDS.get(tool)
        if need:
            lines.append(f"- {tool}: needs {need} ({caps.describe(need)}) - "
                         f"{caps.who_holds(need, roles)}")
            continue
        could = sorted(needs_any(tool) or ())
        lines.append(f"- {tool}: needs the capability of the section a setting is "
                     f"listed under, and they hold none of the {len(could)} that "
                     f"gate one")
    if not lines:
        return ""
    return ("\n\nNot theirs to do here, with who it belongs to - "
            f"{len(lines)} of the {len(every)} actions the assistant can take for "
            "somebody. Refuse from this list, by name; never say there is no tool "
            "for it.\n" + "\n".join(lines))


SYSTEM = """You are the assistant inside FactorySemantics MES, a manufacturing execution system, \
helping the person signed in at plant "{plant}".

You have the plant's own tools. Reads are free: use them to find machines, materials, \
characteristics, orders and specifications before you act - never invent a code. When a tool \
changes the plant, the person sees a preview and decides; the tool result tells you whether it \
was confirmed or declined, so never claim something was done until the result says so.

A proposal is not a change. When you offer one, say what you are about to change and that you \
are waiting for them to press "Do it" - never "updating it now", never any words that describe \
the write as already happening or under way. Nothing has changed until a tool result says it has.

A request for a change is a proposal, not a question: asked to change something, propose it in \
that same turn - the card is how you ask them - so never reply "want me to go ahead?" and never \
say what you would change without putting the card on their screen.

An optional argument is not a question: when the request names everything the tool requires, \
propose it in that turn, and leave anything optional they did not give - a downtime reason, a \
note - for the card and the walk beside it rather than asking for it first.

A list is only what it says it is. If a result carries "total", "showing", "more" or \
"truncated", it is part of a longer list: say so, and call again - with a narrower search or \
the offset it names - rather than treating what you were shown as everything there is. \
Never answer that the plant has no such thing when all you have seen is part of a list.

When the person says they have changed something, or asks what is recorded, read the plant \
again before you answer. Never say "no change is recorded" without having just read the record \
in this turn - settings have setting_changes, which reads the audit trail.

Read before you deny. Never say a thing is not configurable, does not exist, is generated by \
the code or is not a real control until you have looked for it in this turn - anything that \
sounds like a setting is plant_settings(find=...), anything else is that domain's own read - \
because a denial with nothing read behind it tells somebody a control they have is not real.

When someone asks to be shown - "show me how", "where do I click", "I want to do it myself" - \
the answer is a proposal's "Show me" button or the show_guide tool, never a description of the \
screen. Call guides() to see what walks this plant has; show_guide(id) puts one on their screen.

Every change you propose comes with "Show me" beside "Do it", which walks them onto the real \
form with your values already in it - so if they ask to be shown after you proposed something, \
put that same walk on their screen with show_guide("proposal") rather than saying there is none.

Asked to be shown a change nothing has proposed yet, propose it in that turn anyway: the card's \
own "Show me" is the walk they are asking for, standing on the very field, and the catalogue \
holds no walk about it to find.

You never approve anything: signing a draft reason, severity, document, trigger or adjustment \
belongs to a person, so asked to approve one, show them the walk to the control they sign it on, \
and if guides() says a signing walk is not theirs to follow, say which capability it needs.

Never approving is not refusing: when guides() lists a draft's approve walk as theirs to follow \
they hold the signature, so put that walk on their screen rather than a sentence about what you \
cannot sign - the capability wording is for somebody guides() says may not follow it.

Asked for something that is not theirs to do, name the capability it needs and who holds \
it, from the list below - never that no tool for it is available.

Never put a tool's name in what they read: they are at a machine, not reading code, so call \
the tool and say "it is on your screen now" rather than naming the call you are about to make.

Speak plainly, in at most four sentences, to someone standing at a machine. State the numbers \
you found. If you cannot do what was asked, say what you can do instead."""


# --------------------------------------------------------------- the kinds

#: The two agent kinds this product has. A kind is not a mode of one agent: it
#: is an account, a role, a tool set, a prompt and a budget, and decision 0038
#: says that is the whole of what an agent is. `floor` is the assistant in the
#: panel, which proposes changes for the person signed in; `analysis` explores
#: and explains and holds nothing that writes.
#:
#: They are rows here rather than branches everywhere because every place that
#: used to mean "the agent" now has to say *which* - the catalogue, the prompt,
#: the availability sentence, the budget, the account its reads sign in as, and
#: the `brain` on the trace row - and a branch per place is how one of them
#: quietly keeps the other's answer.
FLOOR = "floor"
ANALYSIS = "analysis"


@dataclass(frozen=True)
class Kind:
    """One agent kind, and what it is allowed to be."""

    #: What it is called: in the trace's `brain` column, and in the panel.
    name: str
    title: str
    #: The plant account its tool calls sign in as, and the built-in role that
    #: account holds. Both exist on every plant `fsmes plant init` built
    #: (`plant.LAB_USERS`); a plant that predates one is told to run init again
    #: rather than quietly falling back to the other kind's account, because
    #: not falling back is the whole point of there being two.
    account: str
    role: str
    #: The middle word of its own environment keys - `MES_{env}_BRAIN` and
    #: `MES_{env}_CONVERSATION_USD`. In M2 these become `[ai]` settings with
    #: the same words in the same order (`analysis_brain`,
    #: `analysis_conversation_usd`), so that is a move and not a redesign.
    env: str
    #: Whether a tool that changes the plant may be in its catalogue at all.
    #: `False` is enforced by building the catalogue out of what a tool is
    #: *not*, and asserted by a test that reads the tool registry rather than
    #: the catalogue - see `catalogue`.
    writes: bool
    #: Whether it is offered the two walk-me tools. The analysis kind is not: a
    #: walk is how somebody is led onto a form to make a change, and a kind
    #: that may not propose a change has no business leading anybody to one
    #: either. Its answer to "show me how" is the assistant in the panel.
    walks: bool
    #: Its system prompt, with `{plant}` still in it.
    system: str
    #: What one conversation with it may spend, in dollars, before it stops and
    #: says so. Zero is uncapped, which is what the floor assistant has always
    #: been - a floor conversation is bounded by `[admin] agent_max_rounds` and
    #: by somebody standing at a machine waiting for it. The month's cap
    #: (`MES_AGENT_MONTHLY_USD`) is shared whatever this says: every kind's
    #: spend counts against `spend_this_month()`, because it is one bill.
    conversation_usd: float
    #: Whether it may ask for a chart. The floor assistant may not: the panel
    #: it answers in is a column of sentences beside somebody standing at a
    #: machine, and a picture there is a dashboard nobody asked for
    #: (`docs/design/deep-analysis.md` §2). An exploration is the other
    #: lifetime - one person, one question - so the analysis kind draws.
    draws: bool = False

    @property
    def brain_env(self) -> str:
        return f"MES_{self.env}_BRAIN"

    @property
    def cap_env(self) -> str:
        return f"MES_{self.env}_CONVERSATION_USD"


# ---------------------------------------------------------------- the chart

#: The one tool that is not the plant's and not a walk: it is this
#: conversation's own, and it is how an exploration gets a picture beside its
#: answer (`docs/design/deep-analysis.md` §1 and §2).
#:
#: It takes no numbers and it cannot be given any. The model names a tool call
#: it already made - `from` is the id of its own `tool_use` block, which is
#: beside every result it has been handed, or the plain name of the tool, which
#: means its most recent answer in this conversation - and the loop looks that up
#: in what the plant actually returned. The envelope goes to the browser; the
#: browser draws it with `FS.kit.chart`, which writes the total, the coverage
#: and the footer before any shape draws anything. So every figure in the
#: picture is a figure the plant computed, and rule 1 of the chart contract is
#: kept by there being no other way to get a chart at all.
DRAW_TOOL = "draw"

#: The shapes `kit.js` has. `pareto` is `bars` with the cumulative line on, and
#: is named here because that is the word a person asking for one uses.
CHART_SHAPES = ("line", "bars", "pareto", "states", "histogram", "graph")

#: What a spec may carry, and nothing else. A key outside this set is a spec
#: carrying its own numbers, and it is refused by name rather than ignored:
#: ignoring it would draw the plant's payload under a title the model believed
#: described its own arithmetic.
DRAW_KEYS = ("from", "shape", "title")


def chart_tools() -> list[dict]:
    """The draw tool, as an Anthropic tool definition."""
    return [
        {"name": DRAW_TOOL, "write": False,
         "description":
             "Draw one chart beside your answer, from a result you have already read. "
             "`from` is the id of your own tool_use block for that call, or just the "
             "tool's name, which draws that tool's most recent answer - the plant's "
             "own payload is what gets drawn, with its total and its coverage on it. "
             "You pass no numbers: this tool has nowhere to put them, deliberately.",
         "input_schema": {
             "type": "object",
             "additionalProperties": False,
             "properties": {
                 "from": {"type": "string",
                          "description": "the tool_use id of the call whose answer to draw, "
                                         "or the tool's name for its most recent answer"},
                 "shape": {"type": "string", "enum": list(CHART_SHAPES),
                           "description": "which shape fits what that answer is"},
                 "title": {"type": "string",
                           "description": "what this picture is of, in plain words"},
             },
             "required": ["from", "shape"]}},
    ]


#: What an exploration draws, and what a chart has to say about its own
#: coverage. The shapes landed in `kit.js` with #128 and #134; where an
#: exploration renders was answered on 2026-09-29 - beside the conversation, on
#: the AI tab's Explore panel - and this is the paragraph that tells the model
#: how to ask for one.
#:
#: The rule in the middle is the load-bearing one: the model NAMES a tool result
#: and never carries numbers. The server attaches the envelope the plant
#: computed, straight out of the recorded tool result, and the browser draws
#: that. So a chart is the plant's own arithmetic drawn twice - once as the
#: number in the answer and once as the picture beside it - and there is no
#: path by which the two could come to disagree.
ANALYSIS_CHARTS = (
    f"You can draw what you read. `{DRAW_TOOL}` puts one chart beside your answer: give it the "
    f"`from` of the tool call whose answer you want drawn - the id of your own tool_use block, "
    f"which is beside every result you have been handed, or simply that tool's name, which takes "
    f"its most recent answer - the `shape` "
    f"({', '.join(CHART_SHAPES)}), and a plain `title` saying what the picture is of. "
    "The plant's own payload is what gets drawn: you never pass numbers, you never pass a series, "
    "and a chart that carried figures of yours would be a second arithmetic reachable only "
    "through you. The picture states its own total and its own coverage, drawn from that same "
    "payload, so say the figures in words as well and let the two agree. "
    "A graph or a pareto you have read is drawn, not described: call it in the same turn you read "
    "one, without being asked, because a reader handed the sentence and not the picture has to "
    "take the shape on trust. "
    "Otherwise draw when a shape is the answer - a series over time, a spread - and not to "
    "decorate a sentence; a reader asked you a question, not for a dashboard. A trace graph is "
    "`graph`, a downtime pareto is `bars`, a tag or MTTR series is `line`, a state history is "
    "`states`.")

ANALYSIS_SYSTEM = """You are the analysis agent inside FactorySemantics MES, a manufacturing \
execution system, exploring plant "{plant}" for the person who asked.

You explore and you explain. Reads are free and they are meant to be deep: follow the question \
wherever this plant's own records take it - the four shift analyses, the state history, the tag \
history, the quality record, the audit trail - and answer in the plant's own words, with its own \
codes, never invented ones.

You draw what was measured. Every figure you give is one the plant computed and handed you; never \
work an OEE, an availability, a rate or a share out of the parts yourself, because the plant's \
arithmetic is the one its screens and its people already agree on, and a second one reachable \
only through you is how two true-looking numbers come to disagree.

Every figure carries its coverage. A payload that says what share of the window was actually \
watched is a payload whose numbers mean nothing without it, so give the share beside the figure, \
every time. Where the ledger withheld a figure, say it was withheld and give the reason the \
ledger gave - never fill the hole, never average it away, and never answer the question the \
ledger refused.

Unknown is an answer; zero is not. Time nobody was watching is unknown time and not idle time; a \
stop nobody labelled is unlabelled and not "other". Say which, in those words, and say how much.

A list is only what it says it is. If a result carries "total", "showing", "more" or "truncated", \
say so and call again - narrower, or with the offset it names - rather than reporting what you \
were shown as all there is.

Read before you deny. Never say this plant has no such machine, no such stop and no such record \
until you have looked for it in this turn.

A question about the people here starts with what they asked. When somebody asks about \
operators, about the people on this floor, about what is going wrong for them, about the \
questions being asked or about the biggest problem this plant has, begin with `trace_rollup` to \
see which questions are being asked and by how many of whom, then `trace_graph` to see what \
those questions connect to and - this is the half that matters - what they connect to nothing \
at all. Only then follow the thread into the floor's own records: the downtime pareto, the \
maintenance repair times, the shift analyses, the tag history. Say what the biggest cluster \
touches and name what it does not touch, because on a graph of whatever this plant happened to \
record, the absence is the finding. Nothing in this product links a question to a stop, so two \
facts stand side by side and never become a third: never say a question caused a stop, or that a \
stop followed from one, however close the clocks are.

You change nothing and you recommend nothing. You hold no tool that changes this plant: you \
cannot book, cannot draft, cannot propose, cannot approve and cannot put anything on anybody's \
screen to sign. Asked to change something, say plainly that you only read, and that the \
assistant in the panel on any screen can propose that change to whoever signs it - then answer \
the measuring half of the question if there is one. Never say what somebody ought to change, \
either: a recommendation is a proposal, and a proposal is another agent's tool and another \
person's signature.

{charts}

Speak plainly, to somebody who has sat down with a question. State the numbers you found and the \
share of the window behind them. If the plant cannot answer what was asked, say what it can \
answer instead."""


KINDS: dict[str, Kind] = {
    FLOOR: Kind(
        name=FLOOR, title="the floor assistant", account="AGENT", role="agent",
        env="AGENT", writes=True, walks=True, draws=False, system=SYSTEM,
        conversation_usd=0.0),
    ANALYSIS: Kind(
        name=ANALYSIS, title="the analysis agent", account="ANALYST", role="analyst",
        env="ANALYSIS", writes=False, walks=False, draws=True,
        system=ANALYSIS_SYSTEM.replace("{charts}", ANALYSIS_CHARTS),
        # A fortieth of the month's $10. One exploration that runs away is a
        # quarter, not the month - and this is a speed bump rather than a wall
        # on purpose: the wall is the month's cap, which no new conversation
        # gets around. `docs/ai/BUDGET.md` says both numbers.
        conversation_usd=0.25),
}


def kind_named(name: str | None) -> Kind:
    """One kind, by name. `None` and "" are the floor assistant, because that
    is what every caller that predates kinds meant.

    An unknown name is refused rather than defaulted: a panel asking for a kind
    this release does not have should be told so, not handed the agent that can
    change the plant.
    """
    if not name:
        return KINDS[FLOOR]
    try:
        return KINDS[str(name)]
    except KeyError:
        raise KeyError(f"no agent kind {name!r} in this product: "
                       f"{', '.join(sorted(KINDS))}") from None


def system_for(kind: str | Kind = FLOOR) -> str:
    """One kind's system prompt, with `{plant}` still in it."""
    return (kind if isinstance(kind, Kind) else kind_named(kind)).system


# ------------------------------------------------------- the walk-me tools

#: Two read-only tools that are not the plant's: they are this conversation's
#: own, and they exist so that "show me how" is answered by the model with the
#: conversation in view rather than by a regex before the model is asked.
#:
#: Until 2026-09-26 `/assist/agent` ran `assistant.route()` first, so a message
#: matching `SHOW_ME` never reached the agent at all. Scott asked to be shown
#: three times, mid-way through a proposal about `nc_code_prefix`, and was
#: handed a walkthrough about recording a quality inspection each time. The
#: gate is the agent now; these are how it opens.
GUIDES_TOOL = "guides"
SHOW_GUIDE_TOOL = "show_guide"
GUIDE_TOOLS = (GUIDES_TOOL, SHOW_GUIDE_TOOL)

#: The id of the walk behind a proposal card's own "Show me" button. It is not
#: in `GUIDES` - it is authored per tool in `assistant.SURFACES` and filled
#: with this proposal's arguments - but the model reaches it by id like any
#: other, because the question it answers arrives as a sentence and not as a
#: click.
#:
#: 2026-09-26, live, after #109: with a `write_plant_setting` proposal on his
#: screen Scott typed "could you show me where?". Typing over a card declines
#: it (that is the design), the model then called `guides()`, saw fourteen
#: walks about other tasks and correctly reported that none of them was about
#: plant settings - "there isn't a walkthrough for that; press Do it". The
#: walk he was asking for was on the card, two steps onto the very field, and
#: nothing let the model hand it over. Now it can.
PROPOSAL_WALK = "proposal"


def guide_tools() -> list[dict]:
    """The two walk-me tools, as Anthropic tool definitions."""
    return [
        {"name": GUIDES_TOOL, "write": False,
         "description": "The walkthroughs this person can be shown on their own screen - "
                        "every one they are allowed to follow, with the id show_guide takes. "
                        "Call it before show_guide unless you already know the id.",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": SHOW_GUIDE_TOOL, "write": False,
         "description": "Put a walkthrough on the person's screen: it highlights each real "
                        "control in turn and they do the task themselves. This is the answer "
                        "when somebody asks to be shown how, or says they want to do it "
                        f"rather than have it done. The id {PROPOSAL_WALK!r} is the walk for "
                        "the change you last proposed - the real form with your values in it.",
         "input_schema": {"type": "object",
                          "properties": {"id": {"type": "string",
                                                "description": "a guide id from guides()"}},
                          "required": ["id"]}},
    ]


# ------------------------------------------------------------ availability

def brain(for_kind: str | Kind = FLOOR) -> str:
    """claude | off, for one kind. (qwen joins in a later phase.)

    Each kind has its own switch - `MES_AGENT_BRAIN` for the floor assistant,
    `MES_ANALYSIS_BRAIN` for the analysis agent - so a plant can run the one it
    wants without turning the other off. Neither is read at import: a test that
    sets it and a plant that restarts with it changed behave the same way.
    """
    kind = for_kind if isinstance(for_kind, Kind) else kind_named(for_kind)
    return os.environ.get(kind.brain_env, "auto").strip().lower()


def monthly_cap_usd() -> float:
    try:
        return float(os.environ.get("MES_AGENT_MONTHLY_USD", "10"))
    except ValueError:
        return 10.0


def sdk_installed() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def conversation_cap_usd(for_kind: str | Kind = FLOOR) -> float:
    """What one conversation with this kind may spend. Zero is uncapped.

    An environment key for now, named so that M2's `[ai]` domain can hold the
    same words as a plant setting (`docs/ai/BUDGET.md` says so out loud). A
    number this plant cannot read is the kind's own default rather than a
    crash: a typo in a budget must not take the agent off the plant.
    """
    kind = for_kind if isinstance(for_kind, Kind) else kind_named(for_kind)
    try:
        return max(0.0, float(os.environ.get(kind.cap_env, kind.conversation_usd)))
    except ValueError:
        return kind.conversation_usd


def available(for_kind: str | Kind = FLOOR) -> tuple[bool, str]:
    """Can this kind be used right now, and if not, why - naming the kind.

    Shadow mode, a spent month and a missing key are the plant's and stop both
    kinds; the switch and the per-conversation budget are the kind's own. The
    sentence says which brain it is about either way, because a panel that can
    talk to two of them has to be able to say which one is off (answer 9).
    """
    from fsmes import shadow

    kind = for_kind if isinstance(for_kind, Kind) else kind_named(for_kind)
    if shadow.enabled():
        # It changes nothing in the plant, but it carries the plant's own
        # numbers off the box, and a plant lending us its data to watch did
        # not agree to that. The local model on this machine still answers -
        # for the floor assistant. There is no local analysis agent, and
        # answering worse was the option Scott turned down (answer 9), so on a
        # shadow plant the analysis agent is simply off and says so.
        return False, (f"shadow mode: this plant's data does not leave the box, so "
                       f"{kind.title} is not used. Unset {shadow.SETTING} and restart to "
                       f"allow it.")
    if brain(kind) == "off":
        return False, f"{kind.title} is switched off ({kind.brain_env}=off)"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False, "no ANTHROPIC_API_KEY in this plant's environment"
    if not sdk_installed():
        return False, "the anthropic package is not installed (pip install 'fsmes[agent]')"
    spent = spend_this_month()
    if spent >= monthly_cap_usd():
        # One bill for the box, whichever kind spent it. A month's cap that a
        # second kind could get around would not be a cap.
        return False, f"this month's budget is spent (${spent:.2f} of ${monthly_cap_usd():.2f})"
    return True, "ok"


# ----------------------------------------------------------------- usage

def _cost(model: str, usage: dict) -> float:
    p_in, p_out, p_read, p_write = PRICES.get(model, PRICES["claude-sonnet-5"])
    return (usage.get("input", 0) * p_in + usage.get("output", 0) * p_out
            + usage.get("cache_read", 0) * p_read + usage.get("cache_write", 0) * p_write) / 1_000_000


def log_usage(plant: str, user: str, model: str, usage: dict, path: Path | None = None) -> dict:
    row = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "plant": plant, "user": user,
           "model": model, **usage, "usd": round(_cost(model, usage), 6)}
    target = path or USAGE_FILE
    with contextlib.suppress(OSError):
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    return row


def log_turn(row: dict, path: Path | None = None) -> dict:
    """One line for one turn: what the person got back, and what it cost.

    Written the way `log_usage` writes the bill - append-only JSON lines, and
    a filesystem that will not take it loses the line rather than the answer.
    """
    row = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), **row}
    target = path or TURN_FILE
    with contextlib.suppress(OSError):
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    return row


def turn_rows(path: Path | None = None) -> list[dict]:
    target = path or TURN_FILE
    if not target.is_file():
        return []
    rows = []
    with target.open(encoding="utf-8") as fh:
        for line in fh:
            with contextlib.suppress(ValueError):
                rows.append(json.loads(line))
    return rows


def usage_rows(path: Path | None = None) -> list[dict]:
    target = path or USAGE_FILE
    if not target.is_file():
        return []
    rows = []
    with target.open(encoding="utf-8") as fh:
        for line in fh:
            with contextlib.suppress(ValueError):
                rows.append(json.loads(line))
    return rows


def spend_this_month(path: Path | None = None, now: datetime | None = None) -> float:
    month = (now or datetime.now(UTC)).strftime("%Y-%m")
    return round(sum(r.get("usd", 0.0) for r in usage_rows(path) if str(r.get("ts", "")).startswith(month)), 6)


#: The four numbers a usage row carries, in the order a bill reads them.
TOKEN_KINDS = ("input", "output", "cache_read", "cache_write")


def tokens_this_month(path: Path | None = None, now: datetime | None = None) -> dict[str, int]:
    """What the month's conversations have cost in tokens, not only dollars.

    Dollars are an estimate against a price list; tokens are what actually
    happened. A faithfulness run has to report both, because a month's price
    change would otherwise look like a change in how much the assistant does.
    """
    month = (now or datetime.now(UTC)).strftime("%Y-%m")
    out = dict.fromkeys(TOKEN_KINDS, 0)
    for row in usage_rows(path):
        if not str(row.get("ts", "")).startswith(month):
            continue
        for kind in TOKEN_KINDS:
            out[kind] += int(row.get(kind, 0) or 0)
    return out


def last_used(path: Path | None = None) -> datetime | None:
    rows = usage_rows(path)
    if not rows:
        return None
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(rows[-1]["ts"])
    return None


# ------------------------------------------------------------- the tools

_local_lock = threading.Lock()
_tools_cache: list[Any] | None = None


def serve_locally(plant: str, base_url: str) -> None:
    """Point the tool registry at this very plant, no registry lookup."""
    from fsmes import mcp_server
    mcp_server.serve_locally(plant, base_url)


def registry_tools() -> list[Any]:
    """The MCP tool objects, listed once per process."""
    global _tools_cache
    with _local_lock:
        if _tools_cache is None:
            from fsmes import mcp_server
            _tools_cache = asyncio.run(mcp_server.mcp.list_tools())
        return list(_tools_cache)


def catalogue(capabilities: set[str], *, for_kind: str | Kind = FLOOR) -> list[dict]:
    """The tools this person may have used on their behalf, as Anthropic tool
    definitions. `plant` is injected by the caller; the agent's identity and
    idempotency arguments are the loop's business, never the model's.

    **A kind that may not write gets its catalogue by exclusion.** Not by a list
    of the tools it may have - a list is a thing somebody adds to - but by
    dropping every tool that carries `dry_run`, which is what makes a tool a
    write in this product, and every tool named in `NEEDS` or `PER_CALL_NEEDS`,
    which is the independent list of what each write is gated on. A new write
    tool is therefore outside the analysis catalogue on the day it is written,
    by construction, and
    `test_the_analysis_catalogue_holds_no_tool_that_could_change_the_plant`
    asserts it against the registry rather than against this function.
    """
    kind = for_kind if isinstance(for_kind, Kind) else kind_named(for_kind)
    if "plant.read" not in capabilities:
        return []
    out = []
    for tool in registry_tools():
        if tool.name in HIDDEN:
            continue
        schema = deepcopy(tool.input_schema or {})
        props = schema.get("properties") or {}
        if "plant" not in props:
            continue
        write = "dry_run" in props
        if not kind.writes and (write or "on_behalf_of" in props
                                or tool.name in NEEDS or tool.name in PER_CALL_NEEDS):
            continue
        need = NEEDS.get(tool.name)
        if need and need not in capabilities:
            continue
        # A tool whose capability is decided per call is offered to somebody
        # who could write at least one setting, and refused at the API for any
        # key they may not write. Offering it to somebody who holds none of
        # them would be offering a tool that always refuses.
        any_of = needs_any(tool.name)
        if any_of is not None and not (any_of & capabilities):
            continue
        for hidden in ("plant", "dry_run", "on_behalf_of", "client_ref"):
            props.pop(hidden, None)
        schema["properties"] = props
        schema["required"] = [r for r in schema.get("required", []) if r in props]
        out.append({"name": tool.name, "description": (tool.description or "").strip(),
                    "input_schema": schema, "write": write})
    return out


def _result_payload(result: Any) -> Any:
    """What a tool returned, as plain data."""
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    texts = [getattr(block, "text", None) for block in (getattr(result, "content", None) or [])]
    text = "\n".join(t for t in texts if t)
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text}


def execute(name: str, args: dict, *, plant: str, on_behalf_of: str | None = None,
            dry_run: bool | None = None, client_ref: str | None = None) -> Any:
    """Run one tool against this plant. Writes pass dry_run explicitly.

    Which plant account the call signs in as is the conversation's, not this
    function's: `_drive` holds the kind's account open around every round
    (`mcp_server.acting_as`), so a tool called here reaches the plant as the
    agent that called it.
    """
    from fsmes import mcp_server
    call = dict(args)
    call["plant"] = plant
    if dry_run is not None:
        call["dry_run"] = dry_run
        call["on_behalf_of"] = on_behalf_of
        call["client_ref"] = client_ref
    try:
        result = asyncio.run(mcp_server.mcp.call_tool(name, call))
    except Exception as exc:  # a tool failure is a fact for the model, not a crash
        # The class is the fact; the message and the trace are for the plant's
        # own log, not for a browser (CodeQL py/stack-trace-exposure, #109).
        LOGGER.warning("agent: tool %s failed (%s)", name, type(exc).__name__, exc_info=exc)
        return {"error": f"{type(exc).__name__}: the tool failed; the plant's log has the detail"}
    payload = _result_payload(result)
    if getattr(result, "is_error", False) and isinstance(payload, dict) and "error" not in payload:
        payload = {"error": payload.get("text") or "tool failed", **payload}
    return payload


# --------------------------------------------------------------- sessions

@dataclass
class Proposal:
    id: str
    tool_use_id: str
    tool: str
    args: dict
    preview: Any
    surface: dict | None = None

    def public(self) -> dict:
        return {"id": self.id, "tool": self.tool, "args": self.args,
                "preview": self.preview, "surface": self.surface}


@dataclass
class Session:
    id: str
    user: str
    plant: str
    capabilities: set[str]
    tools: list[dict]
    #: Which agent this conversation is with. A conversation belongs to one
    #: kind for its whole life: the catalogue, the prompt and the budget were
    #: all read from it when it opened, and a message that arrives asking for
    #: the other kind opens a new conversation rather than changing this one's
    #: mind halfway through.
    kind: str = FLOOR
    history: list[Any] = field(default_factory=list)
    pending: dict[str, Proposal] = field(default_factory=dict)
    results: dict[str, dict] = field(default_factory=dict)   # tool_use_id -> tool_result block
    #: What each read actually returned, whole - tool_use_id -> the plant's own
    #: payload, before `_tool_result` shortened it for the model. It is what a
    #: chart is drawn from: the model names a call and the envelope comes from
    #: here, so a picture cannot carry a number the plant did not compute.
    #: Bounded to the last `PAYLOADS_KEPT` reads of the conversation, because
    #: this is a conversation in memory and not a second copy of the plant.
    payloads: dict[str, Any] = field(default_factory=dict)
    awaiting: list[str] = field(default_factory=list)        # tool_use ids of the paused turn
    done: list[dict] = field(default_factory=list)           # writes performed, for the evidence walk
    transcript: list[dict] = field(default_factory=list)
    touched: float = field(default_factory=time.monotonic)
    primed: bool = False

    #: The walkthroughs this person may be shown, read by the caller out of the
    #: short session it already had open. Empty is a plant with none, and then
    #: the two walk-me tools are not offered at all.
    guides: list[dict] = field(default_factory=list)

    #: The plant's own roles and what each grants, read by the caller out of
    #: the same short session, so a refusal can say who holds the capability
    #: the person lacks even after an admin has redefined a role. Empty means
    #: nobody asked a plant, and the answer falls back to the bundles the
    #: product ships (`capabilities.holders`).
    roles: dict[str, list[str]] = field(default_factory=dict)

    #: Every write action this person is *not* offered, with the capability and
    #: who holds it - one line each, appended to the cached system prompt.
    #: Built when the conversation opens because it is a fact about a
    #: capability set, and that does not change inside a conversation.
    withheld: str = ""

    #: The surface of the last change proposed in this conversation - the walk
    #: behind the card's own "Show me" button. Kept after the card is settled,
    #: because the question that needs it comes *after*: "could you show me
    #: where?" typed over an open proposal declines the card, and then the
    #: walk the person asked for is the one that just went off their screen.
    #: See `PROPOSAL_WALK`.
    last_surface: dict | None = None

    #: This turn only, for the turn log - reset when the person says something
    #: or settles a proposal, so a line is what that one exchange did.
    turn_tools: list[str] = field(default_factory=list)
    turn_proposals: list[dict] = field(default_factory=list)
    turn_usage: dict = field(default_factory=dict)
    turn_error: str | None = None
    turn_asked: str = ""
    #: The charts this turn asked for, each with the envelope the server
    #: attached. They go to the browser on the reply and are summarised - shape,
    #: tool, total, coverage word - for the trace: a chart's envelope in the
    #: trace would be a second copy of the plant's rows outside the tables that
    #: own them, which is the one thing `ai_turns` promises not to be.
    turn_charts: list[dict] = field(default_factory=list)
    #: Where this turn's own tool calls start in `transcript`. The transcript
    #: is cleared when the person says something and grows across a confirm
    #: and the rounds that follow it, so a turn's calls are the tail from
    #: here - without this the trace would show a confirm repeating every read
    #: of the turn before it.
    turn_from: int = 0

    #: The turn that just finished, in full: the log line plus the words on
    #: both sides and the tool calls the panel showed. The caller writes it to
    #: this plant's own `ai_turns` table, which is what the AI screen reads -
    #: built here rather than there because this is the only place that knows
    #: what one turn was, and left for the caller to store because a service
    #: holding a database session across a model call holds SQLite's single
    #: write lock across it.
    last_turn: dict | None = None

    #: The budget this conversation opened with - `[admin] agent_max_rounds`,
    #: `agent_session_ttl_seconds` and `agent_result_limit` as this plant had
    #: them at that moment.
    max_rounds: int = MAX_ROUNDS
    ttl: int = SESSION_TTL
    result_limit: int = RESULT_LIMIT

    #: What this conversation may spend, and what it has spent - the kind's own
    #: `MES_{env}_CONVERSATION_USD`, read once when it opened, for the reason
    #: the three above are read once. Zero is uncapped.
    cap_usd: float = 0.0
    spent_usd: float = 0.0

    @property
    def the_kind(self) -> Kind:
        return kind_named(self.kind)

    @property
    def tool_by_name(self) -> dict[str, dict]:
        return {t["name"]: t for t in self.tools}

    @property
    def guide_by_id(self) -> dict[str, dict]:
        return {g["id"]: g for g in self.guides}


_sessions: dict[str, Session] = {}
_sessions_lock = threading.Lock()


def _sweep() -> None:
    now = time.monotonic()
    # Each conversation against its own lifetime, not one global number: a
    # plant that lengthens the lifetime should not reach back and revive the
    # conversations that opened under the old one.
    for sid in [s for s, sess in _sessions.items() if now - sess.touched > sess.ttl]:
        _sessions.pop(sid, None)


def open_session(user: str, plant: str, capabilities: set[str], *,
                 kind: str | Kind = FLOOR,
                 max_rounds: int = MAX_ROUNDS, ttl: int = SESSION_TTL,
                 result_limit: int = RESULT_LIMIT,
                 guides: list[dict] | None = None,
                 roles: dict[str, list[str]] | None = None) -> Session:
    """Start a conversation, on this plant's budget.

    The three budgets are passed in rather than read here: this module holds no
    database session by design - it is a conversation and a model, and the one
    thing that must never happen is a plant's write lock held across a model
    call. The caller has a short read open already and hands them over. The
    walkthroughs come the same way and for the same reason.
    """
    the_kind = kind if isinstance(kind, Kind) else kind_named(kind)
    walks = list(guides or []) if the_kind.walks else []
    tools = catalogue(capabilities, for_kind=the_kind)
    if walks and tools:
        # Offered beside the plant's own tools, and only to somebody the
        # catalogue would speak to at all: without `plant.read` there is no
        # conversation to show anything in.
        tools += guide_tools()
    if the_kind.draws and tools:
        # Same rule for the same reason: nothing to draw without a read.
        tools += chart_tools()
    note = (withheld_note(set(capabilities), roles) if the_kind.writes
            else no_write_note(the_kind))
    sess = Session(id=uuid.uuid4().hex[:12], user=user, plant=plant,
                   capabilities=set(capabilities), tools=tools,
                   kind=the_kind.name,
                   max_rounds=int(max_rounds), ttl=int(ttl),
                   result_limit=int(result_limit), guides=walks,
                   roles=dict(roles or {}),
                   cap_usd=conversation_cap_usd(the_kind),
                   withheld=note)
    with _sessions_lock:
        _sweep()
        _sessions[sess.id] = sess
    return sess


def get_session(session_id: str | None, user: str,
                kind: str | Kind | None = None) -> Session | None:
    """This person's open conversation, if it is still open.

    `kind` is how a caller says which agent it means: a conversation opened with
    one kind is not handed to the other, because the tools, the prompt and the
    budget in it are that kind's. The caller then opens a new one, which is the
    honest thing - two agents, two conversations.
    """
    if not session_id:
        return None
    with _sessions_lock:
        sess = _sessions.get(session_id)
    if sess is None or sess.user != user:
        return None
    if kind is not None and sess.kind != kind_named(
            kind.name if isinstance(kind, Kind) else kind).name:
        return None
    sess.touched = time.monotonic()
    return sess


def forget(session_id: str) -> None:
    with _sessions_lock:
        _sessions.pop(session_id, None)


# ------------------------------------------------------------- the loop

def _anthropic_tools(sess: Session) -> list[dict]:
    tools = [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
             for t in sess.tools]
    if tools:
        # The catalogue is the biggest, most stable part of every request:
        # cache it, and the system prompt before it.
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    return tools


def _call_model(sess: Session) -> Any:
    """One request to the model. Replaced in tests."""
    from fsmes import shadow

    shadow.guard("llm.cloud_agent", detail=f"plant {sess.plant}")
    import anthropic
    client = anthropic.Anthropic()
    return client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=[{"type": "text",
                  "text": system_for(sess.kind).format(plant=sess.plant) + sess.withheld,
                  "cache_control": {"type": "ephemeral"}}],
        tools=_anthropic_tools(sess),
        messages=sess.history,
        thinking={"type": "adaptive"},
        output_config={"effort": EFFORT},
    )


def _usage_of(response: Any) -> dict:
    u = getattr(response, "usage", None)
    return {"input": getattr(u, "input_tokens", 0) or 0,
            "output": getattr(u, "output_tokens", 0) or 0,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0}


def _longest_list(payload: dict) -> str | None:
    """The field of a payload that is carrying the bulk of it, if that field
    is a list. `None` when nothing in it is a list worth dropping from."""
    lists = [(len(json.dumps(v, default=str)), k) for k, v in payload.items()
             if isinstance(v, list) and v]
    if not lists:
        return None
    return max(lists)[1]


def _dropping_whole_items(payload: dict, field: str, limit: int) -> str | None:
    """`payload` with trailing items dropped from `field` until it fits, and a
    `truncated` line saying how many of how many are shown.

    Whole items, never half of one. A JSON document cut at a character count
    is not shortened, it is broken: what the model saw on 2026-09-26 was a
    list of twenty-two settings ending mid-way through the eleventh, with
    nothing anywhere saying a list had been cut - so it read eleven as the
    whole and told Scott his setting did not exist.

    `None` when even an empty list does not fit, which leaves the caller its
    honest last resort.
    """
    items = payload[field]
    total = len(items)

    def built(keep: int) -> str:
        shown = {**payload, field: items[:keep]}
        shown["truncated"] = (
            f"showing {keep} of {total} {field}; the rest were dropped because this "
            f"answer was too long. Ask again, more narrowly, for the ones you need - "
            f"do not report these as all there are.")
        return json.dumps(shown, default=str)

    if len(built(0)) > limit:
        return None
    low, high = 0, total          # low always fits, high may not
    while low < high:
        middle = (low + high + 1) // 2
        if len(built(middle)) <= limit:
            low = middle
        else:
            high = middle - 1
    return built(low)


def _tool_result(tool_use_id: str, payload: Any, limit: int = RESULT_LIMIT) -> dict:
    """One tool's answer, as the model will see it - shortened honestly when
    it does not fit.

    A list loses whole trailing items and gains a line saying how many of how
    many are shown; anything else is cut and says how many characters went
    missing. What never happens again is the silent `…(truncated)` glued into
    the middle of a JSON string, which told the model nothing and left it
    reading half a list as a whole one.
    """
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    if len(text) > limit:
        shorter = None
        if isinstance(payload, dict):
            field = _longest_list(payload)
            if field is not None:
                shorter = _dropping_whole_items(payload, field, limit)
        if shorter is not None:
            text = shorter
        else:
            marker = " …(truncated: {} of {} characters not shown)"
            keep = max(0, limit - len(marker.format(len(text), len(text))))
            text = text[:keep] + marker.format(len(text) - keep, len(text))
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
    if isinstance(payload, dict) and "error" in payload:
        block["is_error"] = True
    return block


def _summary(payload: Any) -> str:
    if isinstance(payload, dict):
        if "error" in payload:
            return str(payload["error"])[:160]
        if "done" in payload:
            return str(payload["done"])[:160]
        if "would" in payload:
            return str(payload["would"])[:160]
        keys = list(payload)[:6]
        return ", ".join(f"{k}={len(v)} items" if isinstance(v, list) else f"{k}" for k, v in
                         ((k, payload[k]) for k in keys))[:160]
    return str(payload)[:160]


def _prime(sess: Session, name: str, role: str) -> str:
    """Who is asking, once per conversation, after the cached prefix."""
    if sess.primed:
        return ""
    sess.primed = True
    caps = ", ".join(sorted(sess.capabilities))
    return (f"[Signed in: {sess.user} ({name}, role {role}). Allowed here: {caps}. "
            f"Now: {datetime.now(UTC).isoformat(timespec='minutes')}]\n")


def _reply(sess: Session, kind: str, say: str, **extra: Any) -> dict:
    out = {"kind": kind, "session": sess.id, "say": say,
           "transcript": list(sess.transcript), "done": list(sess.done),
           "cost": _cost_so_far(sess), **extra}
    if sess.turn_charts:
        # The envelope goes to the browser, which draws it. It does not go to
        # the trace: `_record_turn` takes the summary beside it.
        out["charts"] = [dict(spec) for spec in sess.turn_charts]
    _record_turn(sess, kind, say, extra)
    return out


def _cost_so_far(sess: Session) -> dict:
    """What this turn cost, what the conversation has spent against its own
    cap, and where the month stands - so a reply can say what it cost without
    the screen asking a second endpoint and getting a different moment's answer.

    Estimates at list prices, every one of them; the Console is the bill, and
    every screen that draws these says so.
    """
    return {"turn_usd": round(_cost(MODEL, sess.turn_usage or {}), 6),
            "conversation_usd": round(sess.spent_usd, 6),
            "conversation_cap_usd": sess.cap_usd,
            "month_usd": round(spend_this_month(), 6),
            "month_cap_usd": monthly_cap_usd()}


def _record_turn(sess: Session, kind: str, say: str, extra: dict) -> None:
    """One line per turn, the way `log_usage` writes one line per model call.

    This is the record that would have answered the only question worth asking
    about the conversation of 2026-09-26 - *how many of those thirty turns
    reached the model?* - without reading any of what was said.
    """
    row = {"plant": sess.plant, "session": sess.id, "user": sess.user, "model": MODEL,
           "kind": kind, "tools": list(sess.turn_tools),
           "proposals": list(sess.turn_proposals), **(sess.turn_usage or _zero_usage())}
    row["usd"] = round(_cost(MODEL, sess.turn_usage or {}), 6)
    if kind == "guide" and extra.get("guide"):
        row["guide"] = extra["guide"].get("id")
    if sess.turn_error:
        row["error"] = sess.turn_error
    log_turn(row)
    # The same turn, with the words on both sides and the tool calls the panel
    # showed, for the caller to write into this plant's own trace. The system
    # prompt is not in it and cannot be: nothing here reads `SYSTEM`.
    sess.last_turn = {
        **row, "brain": sess.kind,
        "asked": sess.turn_asked, "said": say,
        "tools": [dict(entry) for entry in sess.transcript[sess.turn_from:]],
        "guide_steps": (len(extra["guide"].get("steps") or [])
                        if kind == "guide" and extra.get("guide") else None),
    }


def _zero_usage() -> dict:
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def _begin_turn(sess: Session, asked: str = "") -> None:
    sess.turn_tools = []
    sess.turn_proposals = []
    sess.turn_usage = _zero_usage()
    sess.turn_error = None
    sess.turn_asked = asked
    sess.turn_charts = []
    sess.turn_from = len(sess.transcript)
    sess.last_turn = None


# ----------------------------------------------- a history the API will take

def _kind_of(block: Any) -> str:
    return block.get("type", "") if isinstance(block, dict) else (getattr(block, "type", "") or "")


def _id_of(block: Any) -> str | None:
    return block.get("id") if isinstance(block, dict) else getattr(block, "id", None)


def _blocks(turn: dict) -> list:
    content = turn.get("content")
    return content if isinstance(content, list) else []


def history_shape(sess: Session) -> str:
    """Roles and block types, never content. What goes in a log line."""
    out = []
    for msg in sess.history:
        blocks = _blocks(msg)
        kinds = [_kind_of(b) or "?" for b in blocks] if blocks else ["text"]
        out.append(f"{msg.get('role')}[{','.join(kinds)}]")
    return " ".join(out)


def _repair_history(sess: Session) -> int:
    """Answer every `tool_use` that nothing answered, and say how many.

    The Messages API refuses a conversation in which an assistant turn holding
    `tool_use` blocks is not followed by their `tool_result`s. On 2026-09-26 a
    message typed over an open proposal produced exactly that shape, and
    **every later message in that conversation** came back
    `BadRequestError` - the session was unusable for good, because nothing
    ever repaired it. This runs before every call: a session already in that
    state recovers on the person's next message rather than staying broken
    until it times out.

    A result already waiting in `sess.results` is used; anything still missing
    gets a synthetic decline, which is the truth - the person moved on.
    """
    repaired = 0
    index = 0
    while index < len(sess.history):
        turn = sess.history[index]
        if turn.get("role") != "assistant":
            index += 1
            continue
        wants = [_id_of(b) for b in _blocks(turn) if _kind_of(b) == "tool_use"]
        wants = [w for w in wants if w]
        if not wants:
            index += 1
            continue
        following = sess.history[index + 1] if index + 1 < len(sess.history) else None
        answered, target = set(), None
        if following is not None and following.get("role") == "user":
            results = [b for b in _blocks(following) if _kind_of(b) == "tool_result"]
            if results:
                target = following
                answered = {b.get("tool_use_id") for b in results}
        missing = [w for w in wants if w not in answered]
        if missing:
            blocks = [sess.results.pop(w, None) or _tool_result(
                w, {"declined": "the person moved on without confirming"}, sess.result_limit)
                for w in missing]
            sess.awaiting = [a for a in sess.awaiting if a not in set(missing)]
            if target is not None:
                # tool_result blocks lead the turn they answer.
                target["content"] = blocks + list(target["content"])
            else:
                sess.history.insert(index + 1, {"role": "user", "content": blocks})
            repaired += len(blocks)
        index += 1
    return repaired


def message(sess: Session, text: str, *, name: str = "", role: str = "") -> dict:
    """The person said something. Drive the model until it replies or pauses."""
    _begin_turn(sess, asked=text)
    if sess.pending:
        # A new message while proposals wait means the answer is no - and the
        # decline has to reach the history *before* the person's words do.
        # Appending the text first left `assistant(tool_use)` beside
        # `user(text)`, which the API refuses; see `_repair_history`.
        for pid in list(sess.pending):
            # What they typed is not always a no. It is always a decline - the
            # card is settled either way - but a decline reported as "moved on"
            # is how "could you show me where?" became "there isn't a
            # walkthrough for that" on 2026-09-26. So the result says what
            # actually happened and where the walk they may be asking for is.
            reason = "the person typed something else instead of deciding"
            if sess.pending[pid].surface is not None:
                reason += (f"; if they were asking to be shown, the walk onto this form is "
                           f"{SHOW_GUIDE_TOOL}({PROPOSAL_WALK!r})")
            _resolve(sess, pid, None, declined=reason)
        _commit_results(sess)
    sess.transcript = []
    sess.turn_from = 0
    sess.done = []
    sess.history.append({"role": "user", "content": _prime(sess, name, role) + text})
    return _drive(sess)


#: Exception classes worth one more try, and the status codes that mean the
#: same thing. A connection that dropped or a minute that was too busy is not
#: an answer about this plant; a 400 is, and retrying it would only spend the
#: same money twice.
TRANSIENT = {"APIConnectionError", "APITimeoutError", "APIConnectionTimeoutError",
             "RateLimitError", "InternalServerError", "OverloadedError", "ServiceUnavailableError"}


def _is_transient(exc: Exception) -> bool:
    if type(exc).__name__ in TRANSIENT:
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (status == 429 or status >= 500)


def _call_once_more_if_it_was_the_line(sess: Session) -> Any:
    """One request, and a second only when the first failed for a reason that
    has nothing to do with what was asked."""
    try:
        return _call_model(sess)
    except Exception as exc:
        if not _is_transient(exc):
            raise
        LOGGER.warning(
            "agent: retrying after a transient model error (%s) session=%s history=%s",
            type(exc).__name__, sess.id, history_shape(sess))
        return _call_model(sess)


def _spent_its_own_budget(sess: Session) -> str:
    """Why this conversation has to stop, or "" while it may carry on.

    A per-conversation cap is a speed bump and not a wall, deliberately: the
    next conversation starts at zero, and the wall is the month's cap, which no
    new conversation gets around. What it stops is one exploration that keeps
    calling - which is the failure mode a kind with every read tool and no
    person watching each round has and the floor assistant does not.
    """
    if sess.cap_usd <= 0 or sess.spent_usd < sess.cap_usd:
        return ""
    return (f"this conversation has spent ${sess.spent_usd:.2f} of the ${sess.cap_usd:.2f} "
            f"one conversation with {sess.the_kind.title} may spend "
            f"({sess.the_kind.cap_env}). Ask again in a new conversation - the month's "
            f"budget is the one that does not reset (${spend_this_month():.2f} of "
            f"${monthly_cap_usd():.2f} so far)")


def _drive(sess: Session) -> dict:
    from fsmes import mcp_server

    ok, why = available(sess.kind)
    if not ok:
        # `reason` is what the panel reads: "off" is a plant without a key, a
        # spent budget or shadow mode, and only that turns the panel's brain
        # over to the local model. An error is not an "off".
        return _reply(sess, "unavailable", f"The cloud brain is not available: {why}.",
                      reason="off", why=why)
    _repair_history(sess)
    # Every round of this conversation reaches the plant as the kind's own
    # account: the floor assistant as AGENT, which is what every write in
    # this product is already audited under, and the analysis agent as
    # ANALYST, whose role holds `plant.read` and `audit.read` and nothing
    # that writes. The catalogue is the first gate on what it may call and
    # this is the second, and the second one does not depend on this file
    # being right.
    with mcp_server.acting_as(sess.the_kind.account):
        for _ in range(sess.max_rounds):
            spent = _spent_its_own_budget(sess)
            if spent:
                # Not "off": the plant's brain is fine and the next conversation
                # will reach it. The panel only hands over to the local model on
                # `reason: "off"`, so this says its own word.
                return _reply(sess, "unavailable", f"I have to stop there: {spent}.",
                              reason="spent", why=spent)
            try:
                response = _call_once_more_if_it_was_the_line(sess)
            except Exception as exc:  # reported to the person, never a 500
                sess.turn_error = type(exc).__name__
                LOGGER.warning(
                    "agent: the model did not answer (%s: %s) session=%s user=%s history=%s",
                    type(exc).__name__, str(exc)[:400], sess.id, sess.user, history_shape(sess))
                # The session stays callable: the history ends on the person's own
                # words or on a set of tool results, both of which the API takes.
                return _reply(
                    sess, "error",
                    "The assistant hit an error on that one. Say it again and I will try afresh.",
                    reason="error", error=type(exc).__name__)
            sess.history.append({"role": "assistant", "content": response.content})
            usage = _usage_of(response)
            for key, value in usage.items():
                sess.turn_usage[key] = sess.turn_usage.get(key, 0) + value
            sess.spent_usd = round(sess.spent_usd + _cost(MODEL, usage), 6)
            log_usage(sess.plant, sess.user, MODEL, usage)

            say = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
            tool_uses = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
            if getattr(response, "stop_reason", None) == "refusal":
                return _reply(sess, "reply", say or "I cannot help with that one.")
            if not tool_uses:
                return _reply(sess, "reply", say.strip() or "(no reply)")

            proposals: list[Proposal] = []
            shown: dict | None = None
            shown_by: str | None = None
            sess.awaiting = [b.id for b in tool_uses]
            for block in tool_uses:
                args = dict(block.input or {})
                spec = sess.tool_by_name.get(block.name)
                sess.turn_tools.append(block.name)
                if block.name == DRAW_TOOL and spec is not None:
                    payload, drawn = _draw(sess, args)
                    if drawn is not None:
                        sess.turn_charts.append(drawn)
                    sess.results[block.id] = _tool_result(block.id, payload, sess.result_limit)
                    sess.transcript.append({"tool": block.name, "args": args,
                                            "ok": drawn is not None,
                                            "summary": (_drawn_summary(drawn) if drawn
                                                        else payload["error"])})
                elif block.name in GUIDE_TOOLS and spec is not None:
                    before = shown
                    shown, payload = _walk_me(sess, block.name, args, already=shown)
                    if shown is not before:
                        shown_by = block.id
                    sess.results[block.id] = _tool_result(block.id, payload, sess.result_limit)
                    sess.transcript.append({"tool": block.name, "args": args,
                                            "ok": "error" not in payload,
                                            "summary": _summary(payload)})
                elif spec is None:
                    # Not offered because they may not use it, or not a tool at all.
                    # The first is the common one and has a true answer; the second
                    # keeps the sentence it always had.
                    payload = (not_this_kinds(block.name, sess.the_kind)
                               or withheld(block.name, sess.capabilities, args, sess.roles)
                               or {"error": f"no tool named {block.name!r} is available "
                                            f"to this person"})
                    sess.results[block.id] = _tool_result(block.id, payload, sess.result_limit)
                    sess.transcript.append({"tool": block.name, "args": args, "ok": False, "summary": payload["error"]})
                elif spec["write"]:
                    preview = execute(block.name, args, plant=sess.plant, on_behalf_of=sess.user, dry_run=True)
                    if isinstance(preview, dict) and "error" in preview:
                        sess.results[block.id] = _tool_result(block.id, preview, sess.result_limit)
                        sess.transcript.append({"tool": block.name, "args": args, "ok": False,
                                                "summary": _summary(preview)})
                        continue
                    from fsmes.services import assistant
                    prop = Proposal(id=uuid.uuid4().hex[:12], tool_use_id=block.id, tool=block.name, args=args,
                                    preview=preview,
                                    # The person's own capabilities, so the walk's
                                    # words can say which side of the gate they are
                                    # on rather than only that there is a gate.
                                    surface=assistant.surface_for(block.name, args,
                                                                  sess.capabilities))
                    proposals.append(prop)
                    if prop.surface is not None:
                        # Kept past the card's life: "show me where?" comes after.
                        sess.last_surface = prop.surface
                    sess.pending[prop.id] = prop
                    sess.turn_proposals.append({"id": prop.id, "tool": prop.tool,
                                                "args": dict(prop.args), "outcome": "open"})
                else:
                    payload = execute(block.name, args, plant=sess.plant)
                    # Kept whole, beside the shortened copy the model reads: a
                    # chart is drawn from what the plant returned, not from what
                    # fitted in the answer.
                    _remember_payload(sess, block.id, block.name, payload)
                    sess.results[block.id] = _tool_result(block.id, payload, sess.result_limit)
                    sess.transcript.append({"tool": block.name, "args": args,
                                            "ok": not (isinstance(payload, dict) and "error" in payload),
                                            "summary": _summary(payload)})
            if proposals:
                if shown is not None and shown_by is not None:
                    # A proposal card and a walkthrough in one round: the card is
                    # what the person is looking at, so the walk did not happen and
                    # the model is told so rather than left believing it did.
                    sess.results[shown_by] = _tool_result(
                        shown_by, {"shown": False,
                                   "why": "a proposal is waiting on the person; offer the walk "
                                          "again once they have decided"}, sess.result_limit)
                    shown = None
                return _reply(sess, "proposals", say.strip(), proposals=[p.public() for p in proposals])
            if shown is not None:
                # The walk goes on the person's screen now; the model's own
                # sentence goes above it. The results of this round are committed
                # first, so the next thing they say starts from a whole history.
                _commit_results(sess)
                return _reply(sess, "guide", say.strip(), guide=shown)
            _commit_results(sess)
        return _reply(sess, "reply", "I stopped after too many steps without finishing. Try a smaller ask.")


#: How many read payloads one conversation keeps for drawing. Twenty-four is
#: two full rounds of a twelve-round budget, so anything read in this turn or
#: the one before it can still be drawn, and a long exploration does not grow
#: without a bound.
PAYLOADS_KEPT = 24


def _remember_payload(sess: Session, tool_use_id: str, tool: str, payload: Any) -> None:
    """Keep what a read returned, so a chart can be drawn from it later.

    Errors are not kept: a chart of a refusal is not a chart, and keeping one
    would let `draw` name an id whose answer was a sentence about why there was
    no answer.
    """
    if not sess.the_kind.draws:
        return
    if not isinstance(payload, dict) or "error" in payload:
        return
    sess.payloads[tool_use_id] = {"tool": tool, "envelope": payload}
    while len(sess.payloads) > PAYLOADS_KEPT:
        sess.payloads.pop(next(iter(sess.payloads)))


def _drawn_summary(spec: dict) -> str:
    """How a chart reaches the trace: shape, tool, total, coverage word, and
    the title the person read - and never the envelope.

    It is the transcript line of the `draw` call, so it lands in `ai_turns.tools`
    with every other tool call of the turn and needs no column of its own. That
    is the whole record this table is allowed to keep of a picture: a second copy
    of the plant's rows here would be a way around the capabilities those rows
    are behind, which is the promise the AI screen makes in so many words."""
    parts = [f"{spec['shape']} of {spec['tool']}"]
    if spec.get("total") is not None:
        parts.append(f"total {spec['total']}")
    parts.append(f"coverage {spec['coverage']}")
    if spec.get("title"):
        parts.append(f"titled {spec['title']!r}")
    return ", ".join(parts)


def _total_of(envelope: dict) -> Any:
    """The envelope's own statement of how many things it is about, if it makes
    one. Never counted here: a total this function worked out would be a figure
    the plant never measured, which is rule 1 from the other side."""
    for key in ("total", "nodes_total", "groups_total", "orders_total",
                "machines_total", "turns_total"):
        if key in envelope:
            return envelope[key]
    return None


def _coverage_word(envelope: dict) -> str:
    """Which of the three coverage facts this payload carries, in the kit's own
    words - a figure, `null` when it could not be computed, or `absent` when the
    payload has no such field. They are three different facts and the trace says
    which one, the way the chart does."""
    if envelope.get("coverage") == "absent" or "coverage" not in envelope:
        return "absent"
    if envelope.get("coverage") is None:
        return "null"
    return str(envelope["coverage"])


def _draw(sess: Session, args: dict) -> tuple[dict, dict | None]:
    """The model asked for a chart. Hand back what the model is told, and the
    spec the browser draws - or nothing, and a plain sentence saying why.

    Two refusals, and both are the same rule from opposite ends. A spec that
    names no tool result has nothing of the plant's in it; a spec that carries
    its own numbers has something that is not the plant's in it. Either way the
    picture would be stating a figure this MES never measured, in the most
    convincing medium the product has.

    `from` is resolved by `_read_named`: an exact `tool_use` id, or a tool name
    meaning that tool's most recent answer here. Neither is a guess, and a
    refusal now says both ways of naming a read.
    """
    extra = sorted(k for k in args if k not in DRAW_KEYS)
    if extra:
        return ({"error":
                 f"a chart draws what the plant measured, so {DRAW_TOOL} takes no data of its "
                 f"own: it takes the id of a call you made ({', '.join(DRAW_KEYS)}) and nothing "
                 f"else. Drop {', '.join(extra)} and name the read you want drawn."}, None)
    shape = str(args.get("shape") or "").strip()
    if shape not in CHART_SHAPES:
        return ({"error": f"no chart shape {shape!r}: {', '.join(CHART_SHAPES)}"}, None)
    named = str(args.get("from") or "").strip()
    from_id, held = _read_named(sess, named)
    if held is None:
        read = [f"{row['tool']} ({tid})" for tid, row in sess.payloads.items()]
        tools = sorted({row["tool"] for row in sess.payloads.values()})
        return ({"error":
                 f"nothing in this conversation was read under the id or the tool name "
                 f"{named!r}, so there is nothing to draw: a chart is a tool result, and this "
                 f"tool has no numbers of its own. `from` takes the tool_use id of a call you "
                 f"made, or just the tool's name, which draws that tool's most recent answer. "
                 + (f"You have read: {', '.join(read)}. So {tools[0]!r} would draw the last "
                    f"{tools[0]}." if read
                    else "Read something first, then draw it.")}, None)
    tool, envelope = held["tool"], held["envelope"]
    spec = {"id": f"c{len(sess.turn_charts) + 1}", "from": from_id, "tool": tool,
            "shape": shape, "title": str(args.get("title") or "").strip() or tool,
            "total": _total_of(envelope), "coverage": _coverage_word(envelope),
            "envelope": envelope}
    told = {"drawn": f"{shape} of {tool} is beside your answer",
            "states": "the picture carries its own total and coverage from that payload; "
                      "say the figures in words too"}
    if named != from_id:
        # The model said a tool name; say which call that turned out to be, so the
        # next `draw` in the same turn can name the id if it means an earlier one.
        told["from"] = f"the most recent {tool} in this conversation ({from_id})"
    return (told, spec)


def _read_named(sess: Session, named: str) -> tuple[str, dict | None]:
    """Which read `from` means, and what it returned.

    Two ways to say it, and one of them costs the model nothing to get right.
    A `tool_use` id is exact and is what the loop always took. A bare **tool
    name** is the other, and it means "the most recent answer that tool gave in
    this conversation" - added 2026-09-29, because the first live exploration
    that drew anything spent two rounds guessing ids (`downtime_pareto_1`,
    `trace_graph_1`) before it used the real ones: the ids are in the
    conversation, but a model that has not looked at them has no way to be sure,
    and a round trip to find out is a round trip the person waits through.

    Nothing is guessed. `downtime_pareto_1` is neither an id nor a tool name and
    is still refused, with the sentence that now names the tool-name form - the
    refusal teaches the shorter way to say it rather than only saying no.
    """
    held = sess.payloads.get(named)
    if held is not None:
        return named, held
    # Most recent last: `sess.payloads` is insertion-ordered and older entries
    # fall off the front, so the last match is the latest answer.
    for tool_use_id, row in reversed(list(sess.payloads.items())):
        if row["tool"] == named:
            return tool_use_id, row
    return named, None


def _walks_on_offer(sess: Session) -> list[dict]:
    """Every walk this conversation can put on the screen right now.

    The plant's own walkthroughs, and - while a proposal has been made in this
    conversation - the card's own "Show me" under `PROPOSAL_WALK`. The
    proposal's walk comes first: it is about the thing they are talking about.
    """
    if sess.last_surface is None:
        return list(sess.guides)
    return [{"id": PROPOSAL_WALK, "title": sess.last_surface["title"],
             "when": "the change you proposed - the real form on the real screen with "
                     "your values already typed into it, for them to check and press "
                     "the button themselves",
             "steps": sess.last_surface["steps"],
             "evidence": sess.last_surface.get("evidence"),
             # It has an id so the model can name it and the turn log can
             # record it, but no endpoint serves it: it is this proposal's
             # arguments in this conversation. The screen saves such a walk
             # whole when it crosses to another page, rather than saving the
             # id and fetching a guide that does not exist there.
             "ephemeral": True},
            *sess.guides]


def _walk_me(sess: Session, tool: str, args: dict, *,
             already: dict | None) -> tuple[dict | None, dict]:
    """`guides()` and `show_guide(id)`: the conversation's own two tools.

    Returns the guide to put on the screen (or what was already going there)
    and the result the model sees. Nothing here touches the plant.
    """
    walks = _walks_on_offer(sess)
    if tool == GUIDES_TOOL:
        return already, {"guides": [{k: v for k, v in
                                     (("id", g["id"]), ("title", g["title"]),
                                      ("when", g.get("when", "")),
                                      ("you_may_not_follow_it", g.get("gated")))
                                     if v is not None}
                                    for g in walks],
                         "total": len(walks)}
    wanted = str(args.get("id") or "")
    guide = {g["id"]: g for g in walks}.get(wanted)
    if guide is None:
        return already, {"error": f"no walkthrough {wanted!r} is available to this "
                                  f"person; call {GUIDES_TOOL} for the ones that are",
                         "available": [g["id"] for g in walks]}
    if guide.get("gated"):
        # A signing walk offered to somebody who may not sign. It is listed
        # rather than hidden so that "approve the draft severity" is answered
        # with who signs it instead of "I cannot do that" (decision 0035), and
        # it is refused here rather than put on their screen, because walking
        # somebody to a button their role is not shown is worse than saying so.
        return already, {"error": guide["gated"], "needs": guide["needs"],
                         "shown": False}
    if already is not None:
        return already, {"shown": False,
                         "why": "one walkthrough at a time; the person is already being shown "
                                f"{already['id']}"}
    return guide, {"shown": True, "guide": guide["id"], "title": guide["title"],
                   "steps": len(guide["steps"]),
                   "note": "it is on their screen now - do not also describe the steps in words"}


def _commit_results(sess: Session) -> None:
    """Every tool call of the paused turn is answered: hand them all back at once."""
    blocks = [sess.results.pop(tid) for tid in sess.awaiting if tid in sess.results]
    sess.awaiting = []
    if blocks:
        sess.history.append({"role": "user", "content": blocks})


def _resolve(sess: Session, proposal_id: str, payload: Any, *, declined: str | None = None) -> Proposal:
    prop = sess.pending.pop(proposal_id)
    if declined is not None:
        payload = {"declined": declined, "would": prop.preview.get("would") if isinstance(prop.preview, dict) else None}
    sess.results[prop.tool_use_id] = _tool_result(
        prop.tool_use_id, payload, sess.result_limit)
    ok = not (isinstance(payload, dict) and ("error" in payload or "declined" in payload))
    sess.transcript.append({"tool": prop.tool, "args": prop.args, "ok": ok, "summary": _summary(payload),
                            "write": True, "declined": declined is not None})
    sess.turn_proposals.append(
        {"id": prop.id, "tool": prop.tool, "args": dict(prop.args),
         "outcome": "declined" if declined is not None else ("confirmed" if ok else "failed")})
    return prop


def confirm(sess: Session, proposal_id: str) -> dict:
    """The person said yes. Run it for real, then let the model continue."""
    _begin_turn(sess)
    if proposal_id not in sess.pending:
        return _reply(sess, "reply", "That proposal is no longer open.")
    prop = sess.pending[proposal_id]
    payload = execute(prop.tool, prop.args, plant=sess.plant, on_behalf_of=sess.user,
                      dry_run=False, client_ref=prop.id)
    _resolve(sess, proposal_id, payload)
    if not (isinstance(payload, dict) and "error" in payload):
        sess.done.append({"tool": prop.tool, "args": prop.args, "result": payload,
                          "evidence": (prop.surface or {}).get("evidence")})
    return _continue(sess)


def decline(sess: Session, proposal_id: str, reason: str | None = None) -> dict:
    _begin_turn(sess)
    if proposal_id not in sess.pending:
        return _reply(sess, "reply", "That proposal is no longer open.")
    _resolve(sess, proposal_id, None, declined=reason or "the person said no")
    return _continue(sess)


def _continue(sess: Session) -> dict:
    if sess.pending:
        return _reply(sess, "proposals", "", proposals=[p.public() for p in sess.pending.values()])
    _commit_results(sess)
    return _drive(sess)


def status(for_kind: str | Kind = FLOOR) -> dict:
    """What one kind costs and whether it is on - and the same for every kind.

    The top-level fields are the floor assistant's and keep the names the panel
    and the AI tab have always read; `kinds` is the same answer per kind, so a
    screen that can talk to two agents can say which is on and why the other is
    not without asking twice.
    """
    kind = for_kind if isinstance(for_kind, Kind) else kind_named(for_kind)
    ok, why = available(kind)
    return {"available": ok, "reason": why, "model": MODEL, "brain": brain(kind),
            "kind": kind.name,
            "spend_usd": spend_this_month(), "cap_usd": monthly_cap_usd(),
            "conversation_cap_usd": conversation_cap_usd(kind),
            # Tokens beside the dollars, because the dollars are this file's
            # price list and the tokens are the bill's own unit.
            "tokens_this_month": tokens_this_month(),
            "last_used": last_used().isoformat(timespec="seconds") if last_used() else None,
            "kinds": {name: _kind_status(spec) for name, spec in KINDS.items()},
            "total_kinds": len(KINDS)}


def _kind_status(kind: Kind) -> dict:
    ok, why = available(kind)
    return {"kind": kind.name, "title": kind.title, "available": ok, "reason": why,
            "account": kind.account, "role": kind.role, "writes": kind.writes,
            # Whether it may ask for a chart. A screen that offers one kind an
            # exploration and the other a column of sentences should read that
            # off the kind rather than hold a list of its own.
            "draws": kind.draws,
            "brain": brain(kind), "conversation_cap_usd": conversation_cap_usd(kind)}
