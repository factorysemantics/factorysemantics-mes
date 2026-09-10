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
import os
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MODEL = os.environ.get("MES_AGENT_MODEL", "claude-sonnet-5")
EFFORT = os.environ.get("MES_AGENT_EFFORT", "low")
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

# Tools the plant's own process has no use for.
HIDDEN = {"list_plants"}

# What a person must be allowed to do for a write tool to be proposed on
# their behalf. A write tool missing here is still gated by plant.read and by
# the AGENT role at the API; a capability named here must exist in
# capabilities.CAPABILITIES (a test checks).
NEEDS: dict[str, str] = {
    "record_check": "quality.record",
    "close_nonconformance": "quality.close_nc",
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
    "propose_adjustment": "adjustments.propose",
    "plan_order": "scheduling.plan",
    "plan_all_orders": "scheduling.plan",
    "add_shift": "scheduling.plan",
    "add_calendar_exception": "scheduling.plan",
}

SYSTEM = """You are the assistant inside FactorySemantics MES, a manufacturing execution system, \
helping the person signed in at plant "{plant}".

You have the plant's own tools. Reads are free: use them to find machines, materials, \
characteristics, orders and specifications before you act - never invent a code. When a tool \
changes the plant, the person sees a preview and decides; the tool result tells you whether it \
was confirmed or declined, so never claim something was done until the result says so.

Speak plainly, in at most four sentences, to someone standing at a machine. State the numbers \
you found. If you cannot do what was asked, say what you can do instead."""


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
        return {"error": f"{type(exc).__name__}: {exc}"}
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

    @property
    def tool_by_name(self) -> dict[str, dict]:
        return {t["name"]: t for t in self.tools}


_sessions: dict[str, Session] = {}
_sessions_lock = threading.Lock()


def _sweep() -> None:
    now = time.monotonic()
    for sid in [s for s, sess in _sessions.items() if now - sess.touched > SESSION_TTL]:
        _sessions.pop(sid, None)


def open_session(user: str, plant: str, capabilities: set[str]) -> Session:
    sess = Session(id=uuid.uuid4().hex[:12], user=user, plant=plant,
                   capabilities=set(capabilities), tools=catalogue(capabilities))
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


def _tool_result(tool_use_id: str, payload: Any) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    if len(text) > RESULT_LIMIT:
        text = text[:RESULT_LIMIT] + " …(truncated)"
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
    return out


def message(sess: Session, text: str, *, name: str = "", role: str = "") -> dict:
    """The person said something. Drive the model until it replies or pauses."""
    if sess.pending:
        # A new message while proposals wait means the answer is no.
        for pid in list(sess.pending):
            _resolve(sess, pid, None, declined="the person moved on without confirming")
    sess.transcript = []
    sess.done = []
    sess.history.append({"role": "user", "content": _prime(sess, name, role) + text})
    return _drive(sess)


def _drive(sess: Session) -> dict:
    ok, why = available()
    if not ok:
        return _reply(sess, "unavailable", f"The cloud brain is not available: {why}.")
    for _ in range(MAX_ROUNDS):
        try:
            response = _call_model(sess)
        except Exception as exc:  # reported to the person, never a 500
            return _reply(sess, "unavailable", f"The cloud brain did not answer ({type(exc).__name__}).")
        sess.history.append({"role": "assistant", "content": response.content})
        log_usage(sess.plant, sess.user, MODEL, _usage_of(response))

        say = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        tool_uses = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
        if getattr(response, "stop_reason", None) == "refusal":
            return _reply(sess, "reply", say or "I cannot help with that one.")
        if not tool_uses:
            return _reply(sess, "reply", say.strip() or "(no reply)")

        proposals: list[Proposal] = []
        sess.awaiting = [b.id for b in tool_uses]
        for block in tool_uses:
            args = dict(block.input or {})
            spec = sess.tool_by_name.get(block.name)
            if spec is None:
                payload = {"error": f"no tool named {block.name!r} is available to this person"}
                sess.results[block.id] = _tool_result(block.id, payload)
                sess.transcript.append({"tool": block.name, "args": args, "ok": False, "summary": payload["error"]})
            elif spec["write"]:
                preview = execute(block.name, args, plant=sess.plant, on_behalf_of=sess.user, dry_run=True)
                if isinstance(preview, dict) and "error" in preview:
                    sess.results[block.id] = _tool_result(block.id, preview)
                    sess.transcript.append({"tool": block.name, "args": args, "ok": False,
                                            "summary": _summary(preview)})
                    continue
                from fsmes.services import assistant
                prop = Proposal(id=uuid.uuid4().hex[:12], tool_use_id=block.id, tool=block.name, args=args,
                                preview=preview, surface=assistant.surface_for(block.name, args))
                proposals.append(prop)
                sess.pending[prop.id] = prop
            else:
                payload = execute(block.name, args, plant=sess.plant)
                sess.results[block.id] = _tool_result(block.id, payload)
                sess.transcript.append({"tool": block.name, "args": args,
                                        "ok": not (isinstance(payload, dict) and "error" in payload),
                                        "summary": _summary(payload)})
        if proposals:
            return _reply(sess, "proposals", say.strip(), proposals=[p.public() for p in proposals])
        _commit_results(sess)
    return _reply(sess, "reply", "I stopped after too many steps without finishing. Try a smaller ask.")


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
    sess.results[prop.tool_use_id] = _tool_result(prop.tool_use_id, payload)
    ok = not (isinstance(payload, dict) and ("error" in payload or "declined" in payload))
    sess.transcript.append({"tool": prop.tool, "args": prop.args, "ok": ok, "summary": _summary(payload),
                            "write": True, "declined": declined is not None})
    return prop


def confirm(sess: Session, proposal_id: str) -> dict:
    """The person said yes. Run it for real, then let the model continue."""
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
