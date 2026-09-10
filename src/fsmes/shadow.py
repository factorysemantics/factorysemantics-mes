"""Shadow mode: the MES watches a real plant and can change nothing in it.

`MES_SHADOW=true` is the one setting that puts this MES beside a plant that
another system is already running. It still reads OPC UA tags, still books
production, still computes every KPI and still writes its own database — it
simply cannot act. Every path by which this process could change something
outside its own database is listed in `REGISTER` below and closed here.

Why one place. The safeguards existed piecemeal before this file: the
recommendation queue needed a human approval, `MES_ERP_MODE=off` existed,
the unified-namespace publisher was off by default. Three separate settings,
each of which someone could get wrong on the day, and no way to prove all
three were shut at once. A plant with two systems writing setpoints, or two
systems confirming to the ERP, is a plant with a problem — so the guarantee
has to be a single switch with a single register behind it.

How it is closed, in two layers:

* **Start-up.** A setting that would open a live connection is refused when
  the process starts, by a validator on `Settings` — so a live ERP adapter
  or a real MQTT client is never even constructed. This is what makes
  shadow mode sticky: there is no runtime toggle, and turning it off is a
  restart with the setting changed.
* **The call site.** Each path that cannot be settled at start-up — an OPC
  UA node write, an MQTT transport — checks `enabled()` and refuses with a
  logged, plain-language reason. Belt and braces, because the second layer
  is what a test can walk.

What shadow mode does *not* promise. It still opens an OPC UA session, so
the plant's server must permit a read-only client; it still needs its order
feed; it still writes files for a person to read (the file ERP adapter's
outbox, exports, logs), because a file nobody has imported has changed
nothing. Paths marked `allowed` in the register say why.

`tests/test_shadow_mode.py` walks every `refused` entry and proves the
refusal, and scans the source for outbound primitives so a new path cannot
be added without appearing here.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

log = structlog.get_logger("shadow")

SETTING = "MES_SHADOW"

#: What shadow mode says, everywhere it is said.
BANNER = (
    "Shadow mode: this MES is watching, not running. It reads the plant and "
    "books production; it cannot write a setpoint, reach a live ERP, publish "
    "to a broker, or send anything about this plant off this box."
)

#: How to leave it, in the one sentence that goes under the banner.
HOW_TO_LEAVE = (
    f"Unset {SETTING} and restart to let this MES act on the plant."
)


class ShadowRefused(RuntimeError):
    """An outbound path was asked to act while shadow mode was on."""


class ShadowMisconfigured(RuntimeError):
    """Settings ask for shadow mode and for something shadow mode forbids."""


@dataclass(frozen=True)
class Outbound:
    """One way this process can reach past its own database.

    `where` is `module:qualified_name` — the function that holds the
    primitive, which is what the source scan in the tests matches against.
    """

    name: str
    where: str
    reaches: str
    verdict: str  # refused | restricted | allowed
    note: str


#: Every outbound path in the package, and what shadow mode does to it.
#:
#: `refused`    — cannot happen at all while shadow mode is on.
#: `restricted` — the mode that would make it live is refused at start-up,
#:                so only the inert form of this path can run.
#: `allowed`    — it runs, because nothing about the plant leaves this box
#:                by it, and nothing outside this MES changes.
#:
#: **30 entries.** The count is stated because a register that quietly loses
#: a row is worse than no register, and `tests/test_shadow_mode.py` scans the
#: source for outbound primitives and fails on any call site not covered by
#: an entry here.
REGISTER: tuple[Outbound, ...] = (
    # ------------------------------------------------------- the plant floor
    Outbound(
        name="opc.node_write",
        where="fsmes.integrations.opc.agent:write_node",
        reaches="a tag on the plant's OPC UA server",
        verdict="refused",
        note="the one place the agent writes a value to a real server; both "
             "callers below go through it",
    ),
    Outbound(
        name="opc.order_code",
        where="fsmes.integrations.opc.agent:_order_code_loop",
        reaches="each machine's OrderCode tag",
        verdict="refused",
        note="the loop stops before its first write and says so once. This "
             "path was never approval-gated — a machine gets an order node "
             "by default — so it is the one shadow mode most needed to close",
    ),
    Outbound(
        name="opc.adjustment_write",
        where="fsmes.integrations.opc.agent:write_approved_adjustments",
        reaches="a writable setpoint on the plant's OPC UA server",
        verdict="refused",
        note="a person may still propose and approve; nothing is dispatched. "
             "The recommendations stay approved and unwritten rather than "
             "being marked failed - they did not fail, they were refused",
    ),
    Outbound(
        name="opc.verify_read",
        where="fsmes.integrations.opc.agent:verify_written_adjustments",
        reaches="the process value behind a setpoint",
        verdict="allowed",
        note="a read, and in shadow mode it has nothing to verify because "
             "nothing was written",
    ),
    Outbound(
        name="opc.inspect",
        where="fsmes.integrations.opc.inspect",
        reaches="the plant's OPC UA server",
        verdict="allowed",
        note="`fsmes opc-verify` and `fsmes opc-browse` connect, browse and "
             "read. Commissioning a shadow plant is exactly this",
    ),
    # ------------------------------------------------------------- the ERP
    Outbound(
        name="erp.adapter",
        where="fsmes.integrations.erp.base:make_adapter",
        reaches="whatever ERP the mode names",
        verdict="restricted",
        note="MES_ERP_MODE may only be 'off' or 'file' in shadow mode; "
             "'rest', 'erpnext' and any installed connector module are "
             "refused at start-up, so no live adapter is ever built",
    ),
    Outbound(
        name="erp.rest",
        where="fsmes.integrations.erp.rest_adapter",
        reaches="an ERP's REST API over HTTP",
        verdict="restricted",
        note="acknowledges orders and posts confirmations; only reachable "
             "through MES_ERP_MODE=rest, refused at start-up",
    ),
    Outbound(
        name="erp.erpnext",
        where="fsmes.integrations.erp.erpnext_adapter",
        reaches="an ERPNext/Frappe site over HTTP",
        verdict="restricted",
        note="posts submitted Manufacture stock entries — real inventory "
             "movement. Only reachable through MES_ERP_MODE=erpnext, "
             "refused at start-up",
    ),
    Outbound(
        name="erp.erpnext_setup",
        where="fsmes.integrations.erp.erpnext_setup",
        reaches="an ERPNext site's Custom Field doctype",
        verdict="restricted",
        note="`fsmes erp setup` creates custom fields; it already refuses "
             "unless MES_ERP_MODE is 'erpnext', which shadow mode refuses",
    ),
    Outbound(
        name="erp.file_inbox",
        where="fsmes.integrations.erp.file_adapter:FileErpAdapter.fetch_orders",
        reaches="the ERP exchange inbox folder",
        verdict="refused",
        note="the read is fine; the *move* is not. Normally a consumed file "
             "is renamed into the archive, which is how the interface "
             "acknowledges. Point that inbox at a folder the plant's own MES "
             "is reading and the move steals its orders — so in shadow mode "
             "the adapter reads each file and leaves it exactly where it was",
    ),
    Outbound(
        name="erp.file_outbox",
        where="fsmes.integrations.erp.file_adapter:FileErpAdapter.send_confirmation",
        reaches="files in the ERP exchange outbox folder",
        verdict="allowed",
        note="deliberately allowed: a shadow plant's whole purpose is to "
             "produce confirmations someone can compare against the "
             "incumbent's. A file changes nothing until something imports "
             "it — so set MES_ERP_OUTBOX to a folder no ERP is watching. "
             "Config, not code, at the plant boundary",
    ),
    # ------------------------------------------------- the namespace (MQTT)
    Outbound(
        name="uns.transport",
        where="fsmes.integrations.uns.transport:make_transport",
        reaches="the plant's MQTT broker",
        verdict="refused",
        note="MES_UNS_MODE may only be 'off' or 'log' in shadow mode; 'mqtt' "
             "is refused at start-up and refused again here",
    ),
    Outbound(
        name="uns.mqtt_connect",
        where="fsmes.integrations.uns.transport:MqttTransport.connect",
        reaches="the plant's MQTT broker",
        verdict="refused",
        note="unreachable in shadow mode because the transport is never built",
    ),
    Outbound(
        name="uns.mqtt_publish",
        where="fsmes.integrations.uns.transport:MqttTransport.publish",
        reaches="a topic on the plant's MQTT broker",
        verdict="refused",
        note="same gate. A subscriber acting on an event it should not have "
             "seen is a plant change by another name",
    ),
    Outbound(
        name="uns.log_publish",
        where="fsmes.integrations.uns.transport:LogTransport.publish",
        reaches="the log file",
        verdict="allowed",
        note="builds every topic and payload and contacts nothing; this is "
             "how a shadow plant sees its namespace",
    ),
    Outbound(
        name="uns.publish_loop",
        where="fsmes.integrations.uns.publisher:cycle",
        reaches="whichever transport was built",
        verdict="restricted",
        note="holds no primitive of its own; inert because only the log "
             "transport can exist",
    ),
    # ------------------------------------------------------ language models
    Outbound(
        name="llm.cloud_agent",
        where="fsmes.services.agent:_call_model",
        reaches="Anthropic's API, over the internet",
        verdict="refused",
        note="the in-UI agent's cloud brain. It changes nothing in the "
             "plant, but it carries this plant's numbers off the box, and a "
             "plant lending us its data to watch did not agree to that. "
             "`available()` refuses in shadow mode and says why; the local "
             "model below still answers",
    ),
    Outbound(
        name="llm.cloud_design",
        where="fsmes.services.design:ask_claude",
        reaches="Anthropic's API, over the internet",
        verdict="refused",
        note="the design chat, a development tool. Refused for the same "
             "reason, and it falls back to the local model as it already "
             "does when there is no key",
    ),
    Outbound(
        name="llm.local_assistant",
        where="fsmes.services.assistant:_ask_model",
        reaches="the local model server (Ollama) on this box",
        verdict="allowed",
        note="asks a model on this machine for words. Nothing leaves the "
             "box, and what it proposes meets the same refusals as anything "
             "else",
    ),
    Outbound(
        name="llm.local_drafting",
        where="fsmes.services.drafting:_ask",
        reaches="the local model server (Ollama) on this box",
        verdict="allowed",
        note="drafts work instructions for a person to approve",
    ),
    Outbound(
        name="llm.local_design",
        where="fsmes.services.design:_ollama",
        reaches="the local model server (Ollama) on this box",
        verdict="allowed",
        note="a development tool; no plant path",
    ),
    Outbound(
        name="llm.local_status",
        where="fsmes.services.ai_status",
        reaches="the local model server (Ollama), and nvidia-smi",
        verdict="allowed",
        note="reads which models are loaded and what the GPU is doing, for "
             "the Ops screen",
    ),
    # ------------------------------------------- this MES, talking to itself
    Outbound(
        name="mcp.plant_api",
        where="fsmes.mcp_server",
        reaches="this MES's own HTTP API",
        verdict="allowed",
        note="the agent surface operates the product the way a browser does. "
             "Every write it makes lands in this MES's database and nowhere "
             "else; the outward acts it could ask for meet the refusals "
             "above. It reports shadow mode per plant, from /health",
    ),
    Outbound(
        name="plant.supervisor",
        where="fsmes.plant",
        reaches="the health endpoints and processes of this box's own plants",
        verdict="allowed",
        note="`fsmes plant … status|start|stop` supervises MES processes on "
             "this machine",
    ),
    Outbound(
        name="ops.liveness_probe",
        where="fsmes.api.routers.ops:_port_open",
        reaches="a TCP port, opened and closed",
        verdict="allowed",
        note="the Ops screen asks whether the OPC endpoint is listening. It "
             "sends no bytes",
    ),
    # ---------------------------------------------------------- the demo
    Outbound(
        name="cli.demo",
        where="fsmes.cli:_demo",
        reaches="a simulated line, a mock ERP and a REST adapter, all in "
                "this process",
        verdict="refused",
        note="`fsmes demo` builds its own RestErpAdapter, around make_adapter "
             "and every other gate. An installation pointed at a real plant "
             "must not also be running a fake one, so the command refuses "
             "before it starts anything",
    ),
    Outbound(
        name="cli.demo_wait",
        where="fsmes.cli:_wait_for",
        reaches="the demo's own API and mock ERP, on this box",
        verdict="refused",
        note="reachable only from the demo, which refuses first",
    ),

    # ------------------------------------------- the simulator and the labs
    Outbound(
        name="sim.opc_server",
        where="fsmes.integrations.opc.simulator",
        reaches="the MES's own bundled OPC UA server",
        verdict="allowed",
        note="the simulator writes the tags it also serves. It is not a "
             "plant, and a plant in shadow mode does not run it",
    ),
    Outbound(
        name="sim.replay",
        where="fsmes.integrations.opc.csv_replay",
        reaches="the MES's own bundled OPC UA server",
        verdict="allowed",
        note="replays recorded tables into that same bundled server",
    ),
    Outbound(
        name="sim.harness",
        where="fsmes.sim",
        reaches="a simulated plant's own API, on this box",
        verdict="allowed",
        note="the scoring harness drives fake plants; nothing it touches is "
             "real, and it is not part of a plant deployment",
    ),
)


def enabled(settings=None) -> bool:
    """Whether this process is in shadow mode.

    Read from settings, never from a runtime flag: shadow mode is decided at
    start-up so nothing can switch it off part-way through a shift.
    """
    if settings is None:
        from fsmes.config import get_settings

        settings = get_settings()
    return bool(getattr(settings, "shadow", False))


def path(name: str) -> Outbound:
    """One register entry by name, or a KeyError naming what exists."""
    for entry in REGISTER:
        if entry.name == name:
            return entry
    raise KeyError(f"no outbound path named {name!r} (known: {', '.join(p.name for p in REGISTER)})")


def refuse(name: str, detail: str = "") -> ShadowRefused:
    """The refusal for one register entry: logged, and returned to raise.

    Returned rather than raised so a caller that must carry on — the
    adjustment loop records the refusal against the recommendation and moves
    to the next one — uses the same words as a caller that stops.
    """
    entry = path(name)
    reason = f"{BANNER} Refused: {entry.reaches}."
    if detail:
        reason = f"{reason} {detail}"
    log.warning("outbound path refused by shadow mode", path=entry.name,
                where=entry.where, reaches=entry.reaches, detail=detail or None)
    return ShadowRefused(reason)


def guard(name: str, settings=None, detail: str = "") -> None:
    """Raise if shadow mode is on. The one line a gated call site adds."""
    if enabled(settings):
        raise refuse(name, detail)


def summary(settings=None) -> dict:
    """What every screen, `fsmes info`, the API and the MCP server report.

    `closed` counts the paths shadow mode shuts; `total` counts the register,
    because a list that does not state its total invites the reader to assume
    it is complete.
    """
    if settings is None:
        from fsmes.config import get_settings

        settings = get_settings()
    on = enabled(settings)
    closed = [p.name for p in REGISTER if p.verdict in ("refused", "restricted")]
    return {
        "shadow": on,
        "setting": SETTING,
        "means": BANNER if on else "This MES can act on the plant.",
        "how_to_leave": HOW_TO_LEAVE if on else None,
        "outbound_paths_closed": len(closed) if on else 0,
        "outbound_paths_total": len(REGISTER),
        # What is actually in force, not what was asked for: shadow mode
        # leaves an unchosen ERP or namespace mode off, and a reader should
        # see the mode rather than infer it.
        "erp_mode": getattr(settings, "erp_mode", None),
        "uns_mode": getattr(settings, "uns_mode", None),
    }


# ------------------------------------------------------------ start-up gate

#: ERP modes that reach nothing live. Anything else — `rest`, `erpnext`, an
#: installed connector module — is a connection to somebody's system of
#: record. There is deliberately no `log` ERP mode to allow: the ERP port has
#: none, and shadow mode does not invent one.
ERP_MODES_ALLOWED = ("off", "file")

#: Namespace modes that contact no broker.
UNS_MODES_ALLOWED = ("off", "log")


def defaults_shadow_mode_settles(settings) -> list[str]:
    """Modes nobody chose, closed rather than refused. Returns what changed.

    `MES_ERP_MODE` defaults to 'rest' - it points at the bundled mock ERP so
    a laptop runs with no setup. A default is not a decision, and refusing to
    start over one a person never made would read as shadow mode being
    broken. So an unset mode becomes 'off', and `summary()` reports what is
    actually in force. A mode somebody did set is a decision, and a decision
    that contradicts shadow mode is refused out loud.
    """
    if not getattr(settings, "shadow", False):
        return []
    settled = []
    chosen = getattr(settings, "model_fields_set", set())
    if "erp_mode" not in chosen and getattr(settings, "erp_mode", "off") not in ERP_MODES_ALLOWED:
        settled.append(f"MES_ERP_MODE was not set, so shadow mode leaves it off "
                       f"(the default, '{settings.erp_mode}', reaches a live ERP)")
        settings.erp_mode = "off"
    if "uns_mode" not in chosen and (getattr(settings, "uns_mode", "off") or "off").lower() \
            not in UNS_MODES_ALLOWED:
        settled.append("MES_UNS_MODE was not set, so shadow mode leaves it off")
        settings.uns_mode = "off"
    return settled


def settings_problems(settings) -> list[str]:
    """Plain-language reasons this configuration cannot run in shadow mode.

    Empty means it can. Separate from the validator so a test — and a person
    reading the code — can ask the question without building a `Settings`.
    """
    if not getattr(settings, "shadow", False):
        return []
    problems = []
    erp_mode = getattr(settings, "erp_mode", "off")
    if erp_mode not in ERP_MODES_ALLOWED:
        problems.append(
            f"MES_ERP_MODE is '{erp_mode}', which connects to a live ERP. In shadow mode it "
            f"may only be {' or '.join(repr(m) for m in ERP_MODES_ALLOWED)} — 'file' writes "
            "confirmations to the outbox folder for a person to compare, and changes nothing "
            "in the ERP."
        )
    uns_mode = (getattr(settings, "uns_mode", "off") or "off").lower()
    if uns_mode not in UNS_MODES_ALLOWED:
        problems.append(
            f"MES_UNS_MODE is '{uns_mode}', which publishes to a real broker. In shadow mode it "
            f"may only be {' or '.join(repr(m) for m in UNS_MODES_ALLOWED)} — 'log' builds every "
            "topic and payload and contacts nothing."
        )
    return problems


def check_settings(settings):
    """Refuse to start on a configuration shadow mode cannot honour."""
    settled = defaults_shadow_mode_settles(settings)
    problems = settings_problems(settings)
    if problems:
        raise ValueError(
            f"{SETTING} is on, so this MES may not change anything outside its own database. "
            + " ".join(problems)
            + f" Fix the setting, or unset {SETTING} if this MES is meant to run the plant."
        )
    for line in settled:
        log.info("shadow mode settled a default", detail=line)
    return settings


def plain_error(exc) -> str | None:
    """The plain sentence inside a pydantic ValidationError, if it is ours.

    A person who set one environment variable wrongly should read one
    sentence, not a validation report with a URL at the end of it.
    """
    try:
        errors = exc.errors()
    except Exception:
        return None
    prefix = "Value error, "
    for error in errors:
        message = str(error.get("msg", ""))
        if message.startswith(prefix) and SETTING in message:
            return message[len(prefix):]
    return None
