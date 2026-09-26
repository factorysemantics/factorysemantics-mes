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
RESULT_LIMIT = 6000        # characters of a tool result the model sees

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
# their behalf. A write tool missing here is still gated by plant.read and by
# the AGENT role at the API; a capability named here must exist in
# capabilities.CAPABILITIES (a test checks).
NEEDS: dict[str, str] = {
    "record_check": "quality.record",
    "close_nonconformance": "quality.close_nc",
    "review_nonconformance": "quality.close_nc",
    "disposition_nonconformance": "quality.close_nc",
    "produce_units": "production.book",
    "book_output": "production.book",
    "issue_material": "production.consume",
    "set_machine_state": "equipment.state",
    "create_order": "orders.create",
    "order_action": "orders.release",
    "perform_maintenance": "maintenance.perform",
    "raise_corrective_maintenance": "maintenance.perform",
    "raise_due_maintenance": "maintenance.perform",
    "create_maintenance_plan": "maintenance.plan",
    "create_material": "masterdata.write",
    "create_equipment": "masterdata.write",
    "create_routing": "masterdata.write",
    "create_spec": "masterdata.write",
    "add_bom_component": "masterdata.write",
    "create_user": "users.manage",
    "create_role": "users.manage",
    "update_role": "users.manage",
    "assign_role": "users.manage",
    "create_document": "documents.write",
    "draft_instruction": "documents.write",
    "draft_trigger": "triggers.write",
    "draft_downtime_reason": "process.define",
    "draft_nc_severity": "quality.define",
    "propose_adjustment": "adjustments.propose",
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

SYSTEM = """You are the assistant inside FactorySemantics MES, a manufacturing execution system, \
helping the person signed in at plant "{plant}".

You have the plant's own tools. Reads are free: use them to find machines, materials, \
characteristics, orders and specifications before you act - never invent a code. When a tool \
changes the plant, the person sees a preview and decides; the tool result tells you whether it \
was confirmed or declined, so never claim something was done until the result says so.

A proposal is not a change. When you offer one, say what you are about to change and that you \
are waiting for them to press "Do it" - never "updating it now", never any words that describe \
the write as already happening or under way. Nothing has changed until a tool result says it has.

A list is only what it says it is. If a result carries "total", "showing", "more" or \
"truncated", it is part of a longer list: say so, and call again - with a narrower search or \
the offset it names - rather than treating what you were shown as everything there is. \
Never answer that the plant has no such thing when all you have seen is part of a list.

When the person says they have changed something, or asks what is recorded, read the plant \
again before you answer. Never say "no change is recorded" without having just read the record \
in this turn - settings have setting_changes, which reads the audit trail.

When someone asks to be shown - "show me how", "where do I click", "I want to do it myself" - \
the answer is a proposal's "Show me" button or the show_guide tool, never a description of the \
screen. Call guides() to see what walks this plant has; show_guide(id) puts one on their screen.

Speak plainly, in at most four sentences, to someone standing at a machine. State the numbers \
you found. If you cannot do what was asked, say what you can do instead."""


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
                        "rather than have it done.",
         "input_schema": {"type": "object",
                          "properties": {"id": {"type": "string",
                                                "description": "a guide id from guides()"}},
                          "required": ["id"]}},
    ]


# ------------------------------------------------------------ availability

def brain() -> str:
    """claude | off. (qwen joins in a later phase.)"""
    return os.environ.get("MES_AGENT_BRAIN", "auto").strip().lower()


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


def available() -> tuple[bool, str]:
    """Can the cloud brain be used right now, and if not, why."""
    from fsmes import shadow

    if shadow.enabled():
        # It changes nothing in the plant, but it carries the plant's own
        # numbers off the box, and a plant lending us its data to watch did
        # not agree to that. The local model on this machine still answers.
        return False, ("shadow mode: this plant's data does not leave the box, so the cloud "
                       f"brain is not used. Unset {shadow.SETTING} and restart to allow it.")
    if brain() == "off":
        return False, "the cloud brain is switched off (MES_AGENT_BRAIN=off)"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False, "no ANTHROPIC_API_KEY in this plant's environment"
    if not sdk_installed():
        return False, "the anthropic package is not installed (pip install 'fsmes[agent]')"
    spent = spend_this_month()
    if spent >= monthly_cap_usd():
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


def catalogue(capabilities: set[str]) -> list[dict]:
    """The tools this person may have used on their behalf, as Anthropic tool
    definitions. `plant` is injected by the caller; the agent's identity and
    idempotency arguments are the loop's business, never the model's."""
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
    """Run one tool against this plant. Writes pass dry_run explicitly."""
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
    history: list[Any] = field(default_factory=list)
    pending: dict[str, Proposal] = field(default_factory=dict)
    results: dict[str, dict] = field(default_factory=dict)   # tool_use_id -> tool_result block
    awaiting: list[str] = field(default_factory=list)        # tool_use ids of the paused turn
    done: list[dict] = field(default_factory=list)           # writes performed, for the evidence walk
    transcript: list[dict] = field(default_factory=list)
    touched: float = field(default_factory=time.monotonic)
    primed: bool = False

    #: The walkthroughs this person may be shown, read by the caller out of the
    #: short session it already had open. Empty is a plant with none, and then
    #: the two walk-me tools are not offered at all.
    guides: list[dict] = field(default_factory=list)

    #: This turn only, for the turn log - reset when the person says something
    #: or settles a proposal, so a line is what that one exchange did.
    turn_tools: list[str] = field(default_factory=list)
    turn_proposals: list[dict] = field(default_factory=list)
    turn_usage: dict = field(default_factory=dict)
    turn_error: str | None = None

    #: The budget this conversation opened with - `[admin] agent_max_rounds`,
    #: `agent_session_ttl_seconds` and `agent_result_limit` as this plant had
    #: them at that moment.
    max_rounds: int = MAX_ROUNDS
    ttl: int = SESSION_TTL
    result_limit: int = RESULT_LIMIT

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
                 max_rounds: int = MAX_ROUNDS, ttl: int = SESSION_TTL,
                 result_limit: int = RESULT_LIMIT,
                 guides: list[dict] | None = None) -> Session:
    """Start a conversation, on this plant's budget.

    The three budgets are passed in rather than read here: this module holds no
    database session by design - it is a conversation and a model, and the one
    thing that must never happen is a plant's write lock held across a model
    call. The caller has a short read open already and hands them over. The
    walkthroughs come the same way and for the same reason.
    """
    walks = list(guides or [])
    tools = catalogue(capabilities)
    if walks and tools:
        # Offered beside the plant's own tools, and only to somebody the
        # catalogue would speak to at all: without `plant.read` there is no
        # conversation to show anything in.
        tools += guide_tools()
    sess = Session(id=uuid.uuid4().hex[:12], user=user, plant=plant,
                   capabilities=set(capabilities), tools=tools,
                   max_rounds=int(max_rounds), ttl=int(ttl),
                   result_limit=int(result_limit), guides=walks)
    with _sessions_lock:
        _sweep()
        _sessions[sess.id] = sess
    return sess


def get_session(session_id: str | None, user: str) -> Session | None:
    if not session_id:
        return None
    with _sessions_lock:
        sess = _sessions.get(session_id)
    if sess is None or sess.user != user:
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
        system=[{"type": "text", "text": SYSTEM.format(plant=sess.plant),
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
           "transcript": list(sess.transcript), "done": list(sess.done), **extra}
    _record_turn(sess, kind, extra)
    return out


def _record_turn(sess: Session, kind: str, extra: dict) -> None:
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


def _zero_usage() -> dict:
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def _begin_turn(sess: Session) -> None:
    sess.turn_tools = []
    sess.turn_proposals = []
    sess.turn_usage = _zero_usage()
    sess.turn_error = None


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
    _begin_turn(sess)
    if sess.pending:
        # A new message while proposals wait means the answer is no - and the
        # decline has to reach the history *before* the person's words do.
        # Appending the text first left `assistant(tool_use)` beside
        # `user(text)`, which the API refuses; see `_repair_history`.
        for pid in list(sess.pending):
            _resolve(sess, pid, None, declined="the person moved on without confirming")
        _commit_results(sess)
    sess.transcript = []
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


def _drive(sess: Session) -> dict:
    ok, why = available()
    if not ok:
        # `reason` is what the panel reads: "off" is a plant without a key, a
        # spent budget or shadow mode, and only that turns the panel's brain
        # over to the local model. An error is not an "off".
        return _reply(sess, "unavailable", f"The cloud brain is not available: {why}.",
                      reason="off", why=why)
    _repair_history(sess)
    for _ in range(sess.max_rounds):
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
            if block.name in GUIDE_TOOLS and spec is not None:
                before = shown
                shown, payload = _walk_me(sess, block.name, args, already=shown)
                if shown is not before:
                    shown_by = block.id
                sess.results[block.id] = _tool_result(block.id, payload, sess.result_limit)
                sess.transcript.append({"tool": block.name, "args": args,
                                        "ok": "error" not in payload,
                                        "summary": _summary(payload)})
            elif spec is None:
                payload = {"error": f"no tool named {block.name!r} is available to this person"}
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
                sess.pending[prop.id] = prop
                sess.turn_proposals.append({"id": prop.id, "tool": prop.tool, "outcome": "open"})
            else:
                payload = execute(block.name, args, plant=sess.plant)
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


def _walk_me(sess: Session, tool: str, args: dict, *,
             already: dict | None) -> tuple[dict | None, dict]:
    """`guides()` and `show_guide(id)`: the conversation's own two tools.

    Returns the guide to put on the screen (or what was already going there)
    and the result the model sees. Nothing here touches the plant.
    """
    if tool == GUIDES_TOOL:
        return already, {"guides": [{"id": g["id"], "title": g["title"], "when": g.get("when", "")}
                                    for g in sess.guides],
                         "total": len(sess.guides)}
    guide = sess.guide_by_id.get(str(args.get("id") or ""))
    if guide is None:
        return already, {"error": f"no walkthrough {args.get('id')!r} is available to this "
                                  f"person; call {GUIDES_TOOL} for the ones that are",
                         "available": [g["id"] for g in sess.guides]}
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
        {"id": prop.id, "tool": prop.tool,
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


def status() -> dict:
    ok, why = available()
    return {"available": ok, "reason": why, "model": MODEL, "brain": brain(),
            "spend_usd": spend_this_month(), "cap_usd": monthly_cap_usd(),
            "last_used": last_used().isoformat(timespec="seconds") if last_used() else None}
