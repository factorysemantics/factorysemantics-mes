"""The MES itself, exposed as MCP tools — the product's agent surface.

The simulation MCP server drives and scores fake plants. This one operates the
MES the way a person does: orders, quality, material, machines, master data,
audit. It exists because "every function callable via REST and MCP" was only
half true - the REST half - and an agent asked to release the next order or
watch fill weights had no tools to do it with.

Three rules, all deliberate:

Everything goes through each plant's HTTP API as a signed-in AGENT account -
never the database. The role system constrains the agent exactly as it would a
person, and every action lands in the same append-only audit spine, attributed
to AGENT, so "what did the agent do to my plant" is one audit query.

Write tools take dry_run. With dry_run=true they describe exactly what would
be posted and post nothing - the preview an approval gate wants to show.

Tools stay thin. Argument parsing and one API call; anything smarter belongs in
the product, where a person's screen gets it too.
"""

from __future__ import annotations

import os
import threading
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

from fsmes import plant as plants

mcp = MCPServer(
    name="fsmes",
    instructions=(
        "Operate the FactorySemantics MES plants: work orders, quality checks "
        "and non-conformances, material lots and genealogy, machine states and "
        "OEE, routings, and the audit trail. Reads are free. Writes accept "
        "dry_run=true to preview the exact request without performing it; "
        "every performed write is audited as the AGENT account, for the person "
        "named in on_behalf_of, and a client_ref makes a repeated write return "
        "its first answer instead of running twice. AGENT holds the agent "
        "role: production and recording, never approvals, accounts or master "
        "data - those come back as refusals naming the capability. Plants are "
        "named - start with list_plants() to see them."
    ),
)

# The checkout whose registry, line data and venv this server serves: found
# upward from the working directory by default; FSMES_ROOT names another (a
# worktree running a scenario registry through FSMES_PLANT_REGISTRY, or a
# production checkout).
ROOT = plants.find_root(Path(os.environ["FSMES_ROOT"]) if os.environ.get("FSMES_ROOT") else None)

# The agent's own sign-in, created by `fsmes plant init` in the `agent` role:
# it runs production and records what it sees, reads the audit trail and
# drafts instructions. It never approves, never administers accounts and
# never defines master data. A tool that needs more comes back as a readable
# refusal naming the capability; an admin grants it per plant, deliberately.
AGENT_USER = "AGENT"
AGENT_PASSWORD = os.environ.get("FSMES_AGENT_PASSWORD", plants.LAB_ONLY_AGENT_PASSWORD)

# Who the current tool call acts for, and its idempotency key. Set by every
# write tool from its own arguments; read by _call when it sends the request.
_identity: ContextVar[tuple[str | None, str | None]] = ContextVar("identity", default=(None, None))

_clients: dict[str, httpx.Client] = {}
_lock = threading.Lock()


# A plant process running the assistant's agent in-process registers itself
# here, so the tools call *this* plant with no registry lookup - prod has no
# registry file next to the code, and a plant should never need one to
# operate itself.
_local: dict[str, str] = {}


def serve_locally(plant: str, base_url: str) -> None:
    with _lock:
        _local[plant] = base_url
        _clients.pop(plant, None)


def _registry() -> dict[str, dict]:
    return plants.load_registry(ROOT)


def _client(plant: str) -> httpx.Client:
    if plant in _local:
        with _lock:
            client = _clients.get(plant)
            if client is None:
                client = _clients[plant] = httpx.Client(base_url=_local[plant], timeout=20.0)
            return client
    registry = _registry()
    if plant not in registry:
        raise KeyError(f"Unknown plant {plant!r}. Known: {', '.join(registry)}.")
    with _lock:
        client = _clients.get(plant)
        if client is None:
            cfg = registry[plant]
            host = cfg.get("api_host", "127.0.0.1")
            client = httpx.Client(base_url=f"http://{host}:{cfg['api_port']}",
                                  timeout=20.0)
            _clients[plant] = client
        return client


def _call(plant: str, method: str, path: str, body: dict | None = None) -> Any:
    """One API call, signing in (or back in) as AGENT when needed."""
    client = _client(plant)
    on_behalf_of, client_ref = _identity.get()
    headers = {}
    if on_behalf_of:
        headers["X-On-Behalf-Of"] = on_behalf_of
    if client_ref and method != "GET":
        headers["Idempotency-Key"] = client_ref
    for attempt in (1, 2):
        r = client.request(method, path, json=body, headers=headers)
        if r.status_code == 401 and attempt == 1:
            login = client.post("/auth/login",
                                json={"code": AGENT_USER, "password": AGENT_PASSWORD})
            if login.status_code != 200:
                raise RuntimeError(
                    f"{plant}: the AGENT account cannot sign in "
                    f"({login.status_code}). Run `fsmes plant {plant} init` to create it.")
            continue
        break
    if r.status_code >= 400:
        is_json = r.headers.get("content-type", "").startswith("application/json")
        detail = r.json().get("detail") if is_json else r.text
        return {"error": f"{r.status_code}: {detail}", "plant": plant, "path": path}
    return r.json() if r.content else {"ok": True}


def _write(plant: str, path: str, body: dict, dry_run: bool, would: str) -> dict:
    if dry_run:
        return {"dry_run": True, "plant": plant, "would": would,
                "request": {"method": "POST", "path": path, "body": body}}
    result = _call(plant, "POST", path, body)
    if isinstance(result, dict) and "error" in result:
        return result
    out = {"done": would, "plant": plant, "response": result, "audited_as": AGENT_USER}
    on_behalf_of, client_ref = _identity.get()
    if on_behalf_of:
        out["on_behalf_of"] = on_behalf_of
    if client_ref:
        out["client_ref"] = client_ref
    return out


# ------------------------------------------------------------------- plants

@mcp.tool()
def list_plants() -> dict:
    """The plants this server can operate, with reachability."""
    out = []
    for name, cfg in _registry().items():
        try:
            healthy = _client(name).get("/health", timeout=4.0).status_code == 200
        except httpx.HTTPError:
            healthy = False
        out.append({"plant": name, "label": cfg.get("label"),
                    "dashboard": plants.dashboard_url(cfg), "reachable": healthy})
    return {"plants": out}


# ------------------------------------------------------------------- orders

@mcp.tool()
def orders(plant: str, status: str | None = None, q: str | None = None,
           limit: int = 50, offset: int = 0) -> dict:
    """Work orders, newest first, one page at a time.

    Filter by status (planned/released/running/completed/closed/cancelled) or
    match a code with q. `total` says how many exist beyond this page.
    """
    path = f"/workorders?limit={limit}&offset={offset}"
    if status:
        path += f"&status={status}"
    if q:
        path += f"&q={q}"
    out = _call(plant, "GET", path)
    if isinstance(out, dict) and "error" in out:
        return out
    # `total` matters to an agent for the same reason it matters to a person:
    # without it a page reads like the whole list.
    return {"plant": plant, "orders": out.get("items", []),
            "total": out.get("total"), "has_more": out.get("has_more")}


@mcp.tool()
def order_detail(plant: str, code: str) -> dict:
    """One order: route progress per operation, plus its genealogy -
    what material went in (and which earlier order made it) and what came out."""
    detail = _call(plant, "GET", f"/workorders/{code}")
    if isinstance(detail, dict) and "error" in detail:
        return detail
    genealogy = _call(plant, "GET", f"/execution/genealogy/{code}")
    return {"plant": plant, "order": detail,
            "genealogy": genealogy if "error" not in genealogy else None}


@mcp.tool()
def create_order(plant: str, code: str, material: str, quantity: float,
                 release: bool = True, dry_run: bool = False,
                 on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
    """Create a work order (and by default release it to the floor)."""
    _identity.set((on_behalf_of.upper() if on_behalf_of else None, client_ref))
    would = f"create order {code}: {quantity} x {material}" + (
        " and release it" if release else "")
    if dry_run:
        return _write(plant, "/workorders",
                      {"code": code, "material": material, "quantity": quantity},
                      True, would)
    made = _write(plant, "/workorders",
                  {"code": code, "material": material, "quantity": quantity},
                  False, would)
    if "error" in made:
        return made
    if release:
        released = _call(plant, "POST", f"/workorders/{code}/release")
        if isinstance(released, dict) and "error" in released:
            made["release_error"] = released["error"]
    return made


@mcp.tool()
def order_action(plant: str, code: str, action: str, reason: str | None = None,
                 dry_run: bool = False,
                 on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
    """release, hold, resume, close, or cancel an order.

    A hold requires a reason - the person resuming it has to know what was
    wrong. The rest are supervisor actions and audited as such.
    """
    _identity.set((on_behalf_of.upper() if on_behalf_of else None, client_ref))
    if action not in ("release", "hold", "resume", "close", "cancel"):
        return {"error": f"Unknown action {action!r}. "
                         f"Expected release, hold, resume, close or cancel."}
    body = {"reason": reason} if action == "hold" else {}
    if action == "hold" and not reason:
        return {"error": "a hold needs a reason"}
    return _write(plant, f"/workorders/{code}/{action}", body, dry_run,
                  f"{action} order {code}" + (f" ({reason})" if reason else ""))


# ------------------------------------------------------------------ quality

@mcp.tool()
def quality(plant: str) -> dict:
    """The quality picture: specs, recent checks with pass/fail, and open
    non-conformances."""
    specs = _call(plant, "GET", "/quality/specs")
    checks = _call(plant, "GET", "/quality/checks")
    ncs = _call(plant, "GET", "/quality/nonconformances?status=open&limit=50")
    if isinstance(checks, dict) and "items" in checks:
        items = checks["items"]
        failed = [c for c in items if c.get("result") == "fail"]
        summary = {"checks": checks.get("total"), "failed_on_this_page": len(failed),
                   "recent": items[:15]}
    else:
        summary = checks
    if isinstance(ncs, dict) and "items" in ncs:
        # The newest fifty and how many there are: a page, said to be one.
        open_ncs = {"open": ncs["total"], "shown": len(ncs["items"]), "has_more": ncs["has_more"],
                    "items": ncs["items"]}
    else:
        open_ncs = ncs
    return {"plant": plant, "specs": specs, "checks": summary, "open_nonconformances": open_ncs}


@mcp.tool()
def record_check(plant: str, material: str, characteristic: str, value: float,
                 order: str | None = None, dry_run: bool = False,
                 on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
    """Record a quality measurement. Out-of-spec values automatically open a
    non-conformance - that is the MES working, not an error."""
    _identity.set((on_behalf_of.upper() if on_behalf_of else None, client_ref))
    body = {"material": material, "characteristic": characteristic, "value": value}
    if order:
        body["order"] = order
    return _write(plant, "/quality/checks", body, dry_run,
                  f"record {characteristic}={value} on {material}"
                  + (f" against {order}" if order else ""))


@mcp.tool()
def close_nonconformance(plant: str, code: str, dry_run: bool = False,
                 on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
    """Close a non-conformance - a supervisor action, audited as AGENT."""
    _identity.set((on_behalf_of.upper() if on_behalf_of else None, client_ref))
    return _write(plant, f"/quality/nonconformances/{code}/close", {}, dry_run,
                  f"close non-conformance {code}")


# ----------------------------------------------------------------- material

@mcp.tool()
def lots(plant: str) -> dict:
    """Material lots and how much of each remains."""
    out = _call(plant, "GET", "/execution/lots?limit=200")
    if isinstance(out, dict) and "error" in out:
        return out
    return {"plant": plant, "lots": out.get("items", []),
            "total": out.get("total")}


@mcp.tool()
def issue_material(plant: str, order: str, lot: str, quantity: float,
                   dry_run: bool = False,
                 on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
    """Issue material from a lot to an order - the consumption side of
    genealogy."""
    _identity.set((on_behalf_of.upper() if on_behalf_of else None, client_ref))
    return _write(plant, "/execution/consume",
                  {"order": order, "lot": lot, "quantity": quantity}, dry_run,
                  f"issue {quantity} from {lot} to {order}")


# ----------------------------------------------------------------- machines

@mcp.tool()
def machines(plant: str) -> dict:
    """Every machine: current state, current order, its analog value, and OEE."""
    summary = _call(plant, "GET", "/dashboard/summary")
    if isinstance(summary, dict) and "error" in summary:
        return summary
    return {"plant": plant, "plant_summary": summary.get("plant"),
            "machines": summary.get("machines")}


@mcp.tool()
def book_output(plant: str, equipment: str, good: int, scrap: int = 0, order: str | None = None,
                dry_run: bool = False, on_behalf_of: str | None = None,
                client_ref: str | None = None) -> dict:
    """Book output by hand: good and scrap counts made on a machine, against
    an order if named (otherwise the order the machine is running). The OPC
    agent books what it sees automatically; this is for what it cannot see."""
    _identity.set((on_behalf_of.upper() if on_behalf_of else None, client_ref))
    body: dict = {"equipment": equipment, "good": good, "scrap": scrap}
    if order:
        body["order"] = order
    return _write(plant, "/execution/report", body, dry_run,
                  f"book {good} good, {scrap} scrap on {equipment}" + (f" against {order}" if order else ""))


@mcp.tool()
def set_machine_state(plant: str, equipment: str, state: str,
                      reason: str | None = None, dry_run: bool = False,
                 on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
    """Set a machine's state (running/idle/down/setup...). Labelling downtime
    with a reason is the difference between a pareto and a shrug."""
    _identity.set((on_behalf_of.upper() if on_behalf_of else None, client_ref))
    body: dict = {"state": state}
    if reason:
        body["reason"] = reason
    return _write(plant, f"/equipment/{equipment}/state", body, dry_run,
                  f"set {equipment} to {state}" + (f" ({reason})" if reason else ""))


@mcp.tool()
def downtime(plant: str, hours: float = 8.0) -> dict:
    """Downtime pareto over the window: which reasons cost what, and how much
    is unlabelled - reported honestly rather than hidden."""
    return {"plant": plant,
            "downtime": _call(plant, "GET", f"/analysis/downtime?hours={hours}")}


@mcp.tool()
def tag_trend(plant: str, equipment: str, tag: str | None = None,
              hours: float = 1.0) -> dict:
    """A machine's process value over time - the signal that drifts before a
    failure. Omit tag for the machine's primary analog."""
    path = f"/analysis/tag/{equipment}?hours={hours}"
    if tag:
        path += f"&tag={tag}"
    return {"plant": plant, "trend": _call(plant, "GET", path)}


# -------------------------------------------------------------- master data

@mcp.tool()
def routings(plant: str) -> dict:
    """Every routing: the ordered operations and the machine each runs on."""
    return {"plant": plant, "routings": _call(plant, "GET", "/masterdata/routings")}


@mcp.tool()
def create_routing(plant: str, code: str, name: str, material: str,
                   operations: list[dict], dry_run: bool = False,
                 on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
    """Define a routing: operations as
    [{"seq": 10, "name": "Cut", "equipment": "SAW01"}, ...].
    An admin action - this is master data, and it is audited."""
    _identity.set((on_behalf_of.upper() if on_behalf_of else None, client_ref))
    body = {"code": code, "name": name, "material": material,
            "operations": operations}
    return _write(plant, "/masterdata/routings", body, dry_run,
                  f"create routing {code} ({len(operations)} operations) for {material}")


# ------------------------------------------------------- work instructions

@mcp.tool()
def instructions(plant: str) -> dict:
    """Every controlled work instruction, and which revision is in force."""
    return {"plant": plant, "instructions": _call(plant, "GET", "/documents")}


@mcp.tool()
def instruction(plant: str, code: str, revision: int | None = None) -> dict:
    """One instruction in full. Ask for a revision to read what it said then.

    An agent answering a floor question should quote the approved text rather
    than invent procedure, and should be able to say which revision it quoted.
    """
    path = f"/documents/{code}"
    if revision is not None:
        path += f"?revision={revision}"
    return {"plant": plant, "instruction": _call(plant, "GET", path)}


@mcp.tool()
def instructions_for(plant: str, material: str | None = None,
                     characteristic: str | None = None,
                     operation: str | None = None,
                     equipment: str | None = None) -> dict:
    """The approved instructions covering a piece of work.

    This is the tool to reach for before telling anyone how to do something:
    the plant's own procedure outranks a model's idea of one.
    """
    params = {"material": material, "characteristic": characteristic,
              "operation": operation, "equipment": equipment}
    query = "&".join(f"{k}={v}" for k, v in params.items() if v)
    return {"plant": plant, "asked": {k: v for k, v in params.items() if v},
            "instructions": _call(plant, "GET", f"/documents/for?{query}")}


@mcp.tool()
def draft_instruction(plant: str, code: str, title: str, body: str,
                      material: str | None = None,
                      characteristic: str | None = None,
                      dry_run: bool = False,
                 on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
    """Draft a work instruction. It arrives unapproved.

    An agent may write a procedure; it may not put one in force. Approving is
    a separate capability that the AGENT account holds only in this lab, and a
    procedure nobody read and approved is not a procedure.
    """
    _identity.set((on_behalf_of.upper() if on_behalf_of else None, client_ref))
    anchors = {k: v for k, v in
               {"material": material, "characteristic": characteristic}.items() if v}
    body_json = {"code": code, "title": title, "body": body, "anchors": anchors}
    return _write(plant, "/documents", body_json, dry_run,
                  f"draft instruction {code} (unapproved)")


# --------------------------------------------------------- people and roles

@mcp.tool()
def capabilities() -> dict:
    """Every capability a role can grant, and what each one means.

    The vocabulary the whole system gates on - the API, these tools, and the
    screens all ask the same question.
    """
    from fsmes.services import capabilities as caps

    return {"capabilities": [{"name": n, "description": d}
                             for n, d in sorted(caps.CAPABILITIES.items())]}


@mcp.tool()
def roles(plant: str) -> dict:
    """Roles defined in this plant, with the capabilities each grants."""
    return {"plant": plant, "roles": _call(plant, "GET", "/admin/roles")}


@mcp.tool()
def users(plant: str) -> dict:
    """Accounts, their role, and what that role lets them do."""
    out = _call(plant, "GET", "/admin/users?limit=200")
    if isinstance(out, dict) and "error" in out:
        return out
    return {"plant": plant, "users": out.get("items", []),
            "total": out.get("total")}


@mcp.tool()
def create_role(plant: str, code: str, name: str, capabilities: list[str],
                description: str | None = None, dry_run: bool = False,
                 on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
    """Define a role as a bundle of capabilities.

    This is how a role the product never shipped comes to exist - "Quality
    Inspector" records checks and nothing else. An unrecognised capability is
    refused rather than silently granting nothing.
    """
    _identity.set((on_behalf_of.upper() if on_behalf_of else None, client_ref))
    body = {"code": code, "name": name, "capabilities": capabilities,
            "description": description}
    return _write(plant, "/admin/roles", body, dry_run,
                  f"create role {code} granting {', '.join(capabilities)}")


@mcp.tool()
def assign_role(plant: str, user: str, role: str, dry_run: bool = False,
                 on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
    """Put a person into a role. Effective on their very next action, because
    capabilities are read live rather than carried in their session."""
    _identity.set((on_behalf_of.upper() if on_behalf_of else None, client_ref))
    if dry_run:
        return {"dry_run": True, "plant": plant,
                "would": f"assign {user} the {role} role",
                "request": {"method": "PUT", "path": f"/admin/users/{user}/role",
                            "body": {"role": role}}}
    result = _call(plant, "PUT", f"/admin/users/{user}/role", {"role": role})
    if isinstance(result, dict) and "error" in result:
        return result
    return {"done": f"assign {user} the {role} role", "plant": plant,
            "response": result, "audited_as": AGENT_USER,
            "on_behalf_of": _identity.get()[0]}


@mcp.tool()
def create_user(plant: str, code: str, name: str, password: str,
                role: str = "operator", dry_run: bool = False,
                 on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
    """Create a sign-in account in a role."""
    _identity.set((on_behalf_of.upper() if on_behalf_of else None, client_ref))
    return _write(plant, "/auth/users",
                  {"code": code, "name": name, "password": password, "role": role},
                  dry_run, f"create account {code} as {role}")


# -------------------------------------------------------------------- audit

@mcp.tool()
def audit(plant: str, actor: str | None = None, limit: int = 30) -> dict:
    """The append-only audit trail, newest first - humans and agents in the
    same spine. actor="AGENT" answers "what has the agent done to my plant"
    in one call."""
    path = f"/audit?limit={limit}"
    if actor:
        path += f"&actor={actor}"
    return {"plant": plant, "actor_filter": actor,
            "audit": _call(plant, "GET", path)}


# ---------------------------------------------------------------- modules
# Tool files per module register against the one server (ARCHITECTURE,
# 2026-09-02). Each gets the same `_call`, so every tool speaks to the
# plants as the same AGENT account.

from fsmes.mcp import adjustments as _adjustments_tools  # noqa: E402
from fsmes.mcp import coa as _coa_tools  # noqa: E402
from fsmes.mcp import equipment as _equipment_tools  # noqa: E402
from fsmes.mcp import erp as _erp_tools  # noqa: E402
from fsmes.mcp import maintenance as _maintenance_tools  # noqa: E402
from fsmes.mcp import masterdata as _masterdata_tools  # noqa: E402
from fsmes.mcp import quality as _quality_tools  # noqa: E402
from fsmes.mcp import scheduling as _scheduling_tools  # noqa: E402
from fsmes.mcp import serialization as _serialization_tools  # noqa: E402
from fsmes.mcp import triggers as _triggers_tools  # noqa: E402


def _identify(on_behalf_of: str | None, client_ref: str | None) -> None:
    """What every module's write tool calls first: who it acts for, and the
    key that makes a retry safe."""
    _identity.set((on_behalf_of.upper() if on_behalf_of else None, client_ref))


# Each module hands its tools back; they become attributes here so anything
# that calls the server's tools in-process (the tests, the CLI) reaches
# every module's the same way.
globals().update(_equipment_tools.register(mcp, _call))
globals().update(_maintenance_tools.register(mcp, _call, _write, _identify))
globals().update(_scheduling_tools.register(mcp, _call, _write, _identify))
globals().update(_quality_tools.register(mcp, _call, _write, _identify))
globals().update(_serialization_tools.register(mcp, _call, _write, _identify))
globals().update(_masterdata_tools.register(mcp, _call, _write, _identify))
globals().update(_erp_tools.register(mcp, _call, _write, _identify))
globals().update(_triggers_tools.register(mcp, _call, _write, _identify))
globals().update(_adjustments_tools.register(mcp, _call, _write, _identify))
globals().update(_coa_tools.register(mcp, _call, _write, _identify))


def _allowed_hosts(host: str, port: int) -> list[str]:
    """The Host headers this server answers to: its own bind address, localhost,
    and whatever FSMES_MCP_ALLOWED_HOSTS names (comma-separated, e.g. a tailnet
    or LAN name the clients use). DNS-rebinding protection refuses the rest."""
    names = [host, "127.0.0.1", "localhost"]
    names += [h.strip() for h in os.environ.get("FSMES_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    out: list[str] = []
    for name in names:
        for candidate in (name, f"{name}:{port}"):
            if candidate not in out:
                out.append(candidate)
    return out


def main() -> None:
    import argparse

    import anyio
    from mcp.server.transport_security import TransportSecuritySettings

    parser = argparse.ArgumentParser(description="Serve the MES product tools over MCP.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8310)
    args = parser.parse_args()

    security = TransportSecuritySettings(
        allowed_hosts=_allowed_hosts(args.host, args.port),
        allowed_origins=["*"],
    )
    anyio.run(lambda: mcp.run_streamable_http_async(
        host=args.host, port=args.port, transport_security=security))


if __name__ == "__main__":
    main()
