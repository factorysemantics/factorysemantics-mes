"""Every action a person can take, measured against what the assistant can do.

`test_route_coverage` has ratcheted *screens* against routes since 2026-09-02:
every API route has a screen, or a written reason not to. `test_mcp_parity`
claimed to do the same for *tools* from 2026-09-08 and did not: it matched the
tool source with the method thrown away, so `lots()` reading
`GET /execution/lots` counted as covering `POST /execution/lots`, and three of
its reasons were plan phases rather than rules. Nothing, in other words,
measured the floor assistant against the screens - and on 2026-09-25 and
2026-09-26 the person who found the gaps was the maintainer, twice, by asking
the assistant for something a screen does and watching it fail.

This is that measurement, done properly, and it replaces `test_mcp_parity`. It
is principle 3 - every function callable via REST and MCP - as a ratchet: for
every write route in the API, POST, PUT, PATCH or DELETE, either a write tool
sends it, or a line in `EXCLUDED` says why not. The list only shrinks: a route
named there that has since grown a tool fails the ratchet.

## What counts as a tool sending a route

Read out of the source, not written down again here. Every `@mcp.tool()` in
`mcp_server.py` and `mcp/*.py` is parsed, and the calls it makes to the
server's `write()` and `call()` helpers give the method and the path template
it sends. An f-string's `{...}` stands for one path segment, so
`f"/workorders/{code}/release"` covers `POST /workorders/{code}/release`.

One nicety earns its code: a tool whose path ends in a variable the tool
itself only accepts a handful of literal values for - `order_action`'s
`action`, which must be release, hold, resume, close or cancel -
is expanded into those literals, so the table says which five routes it
covers rather than "some route of this shape".

## What the reasons may say

A reason is not an excuse to skip the work. The three the product currently
gives are each a rule written down somewhere else:

* **An approval is the person's signature.** Decision 0035: the `agent` role
  holds `*.define` and never `*.approve`, and a confirmed "Do it" runs as the
  AGENT account on behalf of the person - so a tool that approved on their
  behalf would be forging the signature the approval exists to record. The
  honest answer to "approve it for me" is a walk to the control.
* **An agent never holds a password.** `/auth/...` is a person at a keyboard.
* **Plumbing is not an action.** `/assist/*` and `/design/*` are how the
  assistant is spoken to; a tool for them would be the assistant calling
  itself.

## Why this is not in `services/`

It reads the app's own routes, and a service may not import the API - the
ladder in `fsmes/kernel/__init__.py`, kept by `test_core_purity`. A measurement
of the product's surfaces is an edge like the surfaces it measures: it reads the
routes FastAPI publishes, the tools' own source, and the browser's scripts.

## What this module does not measure

Whether the walk exists (`SURFACES` in `services.assistant`, reported here as
a column and nothing more), and whether the model actually reaches for the
right tool. Those are separate handoffs; a tool with no surface is offered and
proposed but cannot be shown.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

#: The methods that change a plant. A GET is reached by `test_route_coverage`;
#: what a person *does* is one of these four.
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

_SRC = Path(__file__).resolve().parent
#: Where the tools live: the core server and one file per module, per
#: `modules.py`'s `tools=`.
TOOL_FILES = (_SRC / "mcp_server.py", *sorted((_SRC / "mcp").glob("*.py")))
#: The page scripts, matched exactly as `test_route_coverage` matches them.
WEB = _SRC / "web"

#: Write routes no tool sends, and why. Each reason names the rule it follows,
#: not the work somebody could do; a reason that reads like a to-do belongs in
#: a handoff, and the route belongs to a tool.
#:
#: The list only shrinks. `test_every_write_route_has_a_tool_or_a_reason`
#: fails on a line here whose route has grown a tool.
EXCLUDED: dict[str, str] = {
    # ---- approvals: the person's own signature (decision 0035) -----------
    "POST /documents/{code}/approve/{revision}":
        "putting a revision in force is the approver's signature, recorded "
        "against them by name. Decision 0035: the agent role holds "
        "documents.write and never documents.approve, and a confirmed \"Do "
        "it\" runs as AGENT on the person's behalf - so a tool here would "
        "sign for them. The assistant drafts (create_document, "
        "revise_document) and walks them to Approve.",
    "POST /documents/{code}/withdraw":
        "taking an instruction out of force is the same signature in reverse, "
        "gated on the same documents.approve, and withdrawn procedure is what "
        "the floor is working to until somebody notices. A person does it.",
    "POST /triggers/{code}/approve":
        "a trigger in force is logic acting on a live plant with nobody in "
        "the loop after it. Decision 0035 gives the agent triggers.write and "
        "never triggers.approve; draft_trigger drafts, a person signs.",
    "POST /triggers/{code}/withdraw":
        "withdrawing a trigger stops a machine being protected by it. Gated "
        "on triggers.approve for that reason, and not a tool for the same one.",
    "POST /adjustments/{code}/approve":
        "the human in the loop before a PLC write - the whole point of the "
        "adjustment queue. propose_adjustment proposes; approving is the "
        "person the queue exists to ask.",
    "POST /adjustments/{code}/reject":
        "rejecting is that same judgement, and an agent that could reject "
        "could quietly clear the queue it filled.",
    "POST /equipment/downtime-reasons/{code}/approve/{revision}":
        "the vocabulary every stop from now on is named with. "
        "draft_downtime_reason drafts it; process.approve is a person's, and "
        "the walk to the pending-approvals panel is the honest answer.",
    "POST /quality/severities/{code}/approve/{revision}":
        "the same again for the severities a non-conformance is graded at - "
        "the second vocabulary, and deliberately the same reason word for "
        "word: quality.approve is a person's signature.",
    # ---- accounts: an agent never holds a password ------------------------
    "POST /auth/users/{code}/password":
        "an agent never types a password. Setting somebody's is a person at a "
        "keyboard with the account holder in front of them, and a password "
        "that passed through a model's context is a password to rotate.",
    "POST /auth/login":
        "signing in is how a person becomes the person this all acts for. The "
        "tools sign in as AGENT themselves, in code, with no model in the loop.",
    "POST /auth/logout":
        "ending a person's session is not an action on the plant, and an "
        "assistant that could sign somebody out could sign them out mid-shift.",
    # ---- plumbing: the assistant is not an action on the plant ------------
    "POST /assist/ask":
        "this is how the assistant is asked. A tool for it would be the "
        "assistant calling itself.",
    "POST /assist/agent":
        "the same: the floor assistant's own conversation endpoint.",
    "POST /assist/agent/confirm":
        "\"Do it\" - the person's confirmation of a proposal. The loop reads "
        "it; a tool that pressed it would be the assistant confirming itself.",
    "POST /assist/agent/decline":
        "the other half of that confirmation, for the same reason.",
    "POST /design/chat":
        "the design partner, which is how this product is built rather than "
        "how a plant is run. Not a floor action.",
    # ---- roles: a judgement call, said out loud --------------------------
    "DELETE /admin/roles/{code}":
        "a judgement call, decided against. create_role and update_role exist "
        "because defining what a role grants is work an admin can be helped "
        "with and is reversible from the screen that did it. Deleting one is "
        "not: every account holding it loses every capability at once, and "
        "what it granted is not recoverable from the row that is gone. The "
        "screen keeps it; the assistant walks an admin to it.",
}

#: Write routes with no `require(...)` of their own because their gate is a
#: property of an argument. The sentence says where the gate really is; the
#: capabilities are read from the registry the route reads, never listed here.
#:
#: There is one, and it is the same shape as `agent.PER_CALL_NEEDS`: one tool
#: serves every domain's live settings and no single capability gates them all.
PER_KEY: dict[str, tuple[str, str]] = {
    "PATCH /dashboard/config/{domain}/settings/{key}":
        ("the `define` of the ConfigSection the key is listed under",
         "write_plant_setting"),
}

#: Prefix of the one line of the generated page that is not pinned by a test.
GENERATED_LINE = "Generated by `fsmes assist coverage --markdown` on "

#: Where the generated page lives, relative to the repository root.
DOC_PATH = Path("docs") / "operate" / "assistant-coverage.md"


# --------------------------------------------------------------- the routes

@dataclass(frozen=True)
class Route:
    method: str
    path: str
    #: What the route itself gates on, read off its own `require(...)`
    #: dependency. `None` is a route with no capability gate at all.
    capability: str | None

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


def _gate(route) -> str | None:
    """The capability one route requires, read from its own dependency.

    `deps.require` closes over `capability`; reading the cell is asking the
    router what it gates on rather than keeping a second copy of the answer
    here that could drift from it.
    """
    for dep in route.dependant.dependencies:
        code = getattr(dep.call, "__code__", None)
        names = tuple(code.co_freevars) if code else ()
        if "capability" in names:
            return dep.call.__closure__[names.index("capability")].cell_contents
    return None


def _api_routes(routes, prefix: str = ""):
    """Every `APIRoute` under an app, with the prefix it was mounted at.

    FastAPI wraps an included router rather than copying its routes up, so the
    app's own `routes` list is a tree and the paths on the leaves are missing
    the prefix `include_router` gave them.
    """
    from fastapi.routing import APIRoute

    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
            continue
        context = getattr(route, "include_context", None)
        inner = getattr(context, "included_router", None)
        if inner is not None:
            yield from _api_routes(inner.routes, prefix + (context.prefix or ""))


def write_routes() -> list[Route]:
    """Every route that changes the plant, with the capability it gates on."""
    from fsmes.api.app import create_app

    out: dict[str, Route] = {}
    for path, route in _api_routes(create_app().routes):
        capability = _gate(route)
        for method in route.methods:
            if method in WRITE_METHODS:
                out[f"{method} {path}"] = Route(method, path, capability)
    return [out[k] for k in sorted(out)]


# ---------------------------------------------------------------- the tools

@dataclass(frozen=True)
class ToolWrite:
    tool: str
    file: str
    method: str
    #: The path the tool sends, with `{...}` standing for one segment.
    path: str


def _segments(path: str) -> list[str]:
    return path.strip("/").split("/")


def _literal_choices(func: ast.FunctionDef, name: str) -> list[str]:
    """The literal values a tool accepts for one of its own arguments.

    `order_action` refuses anything but release, hold, resume, close or cancel
    before it touches the API, and says so as `if action not in (...)`. Reading
    that tuple is what lets the table name the five routes it covers.
    """
    for node in ast.walk(func):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], ast.In | ast.NotIn):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == name):
            continue
        right = node.comparators[0]
        if isinstance(right, ast.Tuple | ast.List | ast.Set):
            values = [e.value for e in right.elts
                      if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if values:
                return values
    return []


def _path_template(node: ast.expr, func: ast.FunctionDef) -> list[str] | None:
    """The path (or paths) one call argument names, or None if it is not one.

    A plain string is itself. An f-string becomes one template per combination
    of the literal values its placeholders accept, and a placeholder with no
    literal set becomes `{...}` - one segment of any value.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value] if node.value.startswith("/") else None
    if not isinstance(node, ast.JoinedStr):
        return None
    paths = [""]
    for part in node.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            paths = [p + part.value for p in paths]
            continue
        if not isinstance(part, ast.FormattedValue):
            return None
        inner = part.value
        choices = (_literal_choices(func, inner.id)
                   if isinstance(inner, ast.Name) else [])
        paths = [p + c for p in paths for c in (choices or ["{...}"])]
    return [p.split("?")[0] for p in paths if p.startswith("/")]


#: The helpers a tool sends a request through. `write` is the previewable one
#: every write tool uses; `call` is the raw one, and a write through it names
#: its method as a string literal.
_WRITE_HELPERS = ("_write", "write")
_CALL_HELPERS = ("_call", "call")


def _tool_functions(tree: ast.Module):
    """Every `@mcp.tool()` in one file, wherever it is nested.

    The core server declares its tools at module level; a module's tool file
    declares them inside `register()`, which is how they get the helpers.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            call = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(call, ast.Attribute) and call.attr == "tool":
                yield node
                break


def _method_of(call: ast.Call) -> str:
    """The method a `write()` sends: POST unless it says otherwise.

    Putting a live setting in force is a PATCH on a value that already exists,
    and it says so with `method=`.
    """
    for keyword in call.keywords:
        if keyword.arg == "method" and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value).upper()
    return "POST"


def tool_writes() -> list[ToolWrite]:
    """Every write one of the product's MCP tools sends, read from the source.

    Parsed rather than imported: the answer must not depend on which modules
    this checkout has switched on, because the ratchet is about the code that
    ships and not about one deployment's `MES_MODULES`.
    """
    out: list[ToolWrite] = []
    for file in TOOL_FILES:
        if file.name == "__init__.py":
            continue
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for func in _tool_functions(tree):
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                name = (node.func.id if isinstance(node.func, ast.Name)
                        else getattr(node.func, "attr", ""))
                if name in _WRITE_HELPERS and len(node.args) >= 2:
                    method, arg = _method_of(node), node.args[1]
                elif name in _CALL_HELPERS and len(node.args) >= 3:
                    verb = node.args[1]
                    if not (isinstance(verb, ast.Constant)
                            and str(verb.value).upper() in WRITE_METHODS):
                        continue
                    method, arg = str(verb.value).upper(), node.args[2]
                else:
                    continue
                for path in _path_template(arg, func) or []:
                    out.append(ToolWrite(func.name, file.name, method, path))
    return out


def _sends(route: Route, write: ToolWrite) -> bool:
    """Whether one tool's write reaches one route.

    Segment by segment: a route's `{parameter}` takes any segment, and a
    tool's `{...}` is a value this reader could not pin down, which reaches any
    literal. Lengths must match, so `/coa/{order}` never counts as covering
    `/coa/pallet/{serial}`.
    """
    if route.method != write.method:
        return False
    theirs, ours = _segments(route.path), _segments(write.path)
    if len(theirs) != len(ours):
        return False
    return all(t.startswith("{") or o == "{...}" or t == o
               for t, o in zip(theirs, ours, strict=True))


# -------------------------------------------------------------- the screens

@lru_cache(maxsize=1)
def _scripts() -> tuple[tuple[str, str], ...]:
    """Each page script, named, with `${...}` reduced to one segment.

    The same reduction `test_route_coverage` makes, so a route counted as
    reached there is counted as reached here.
    """
    files = sorted(WEB.glob("*.js")) + sorted((WEB / "line").glob("*.js"))
    return tuple((f.name, re.sub(r"\$\{[^}]*\}", "X", f.read_text(encoding="utf-8")))
                 for f in files)


def page_scripts() -> str:
    """Every page script, joined, with `${...}` reduced to one segment.

    What `test_route_coverage` matches against, kept here so that test and this
    report never disagree about what a screen calls.
    """
    return "\n".join(text for _name, text in _scripts())


def screen_pattern(path: str) -> re.Pattern:
    """`test_route_coverage`'s textual match for one route path.

    Coverage is textual and ignores the method - deliberately loose, because
    the question is whether anything wired the module up, not whether a
    particular button works.
    """
    parts = []
    for segment in path.strip("/").split("/"):
        if segment.startswith("{"):
            parts.append(r"[^/\"'`?\s]+")
        else:
            parts.append("(?:" + re.escape(segment) + "|X)")
    return re.compile(r"[\"'`]/" + "/".join(parts) + r"(?=[\"'`?\sX])")


def screens_calling(path: str) -> tuple[str, ...]:
    """The page scripts that name this path, by file name."""
    pattern = screen_pattern(path)
    return tuple(name for name, text in _scripts() if pattern.search(text))


# ----------------------------------------------------------------- the scan

@dataclass(frozen=True)
class Action:
    """One write route, and what the assistant can do about it."""

    route: Route
    #: Tools that send it, in the order the source declares them.
    tools: tuple[str, ...]
    #: Why no tool sends it, from `EXCLUDED`. Present only when `tools` is empty.
    reason: str | None
    #: Page scripts that call it, by file name.
    screens: tuple[str, ...]
    #: Of `tools`, the ones with a "Show me" walk in `assistant.SURFACES`.
    walks: tuple[str, ...]
    #: The capabilities any one of which lets a person perform this route.
    #: One for a route with its own `require(...)`; several for a route gated
    #: per argument; empty for a route with no capability gate at all.
    any_of: frozenset[str] = frozenset()
    #: Where the gate is, when it is not the route's own dependency.
    per_key: str | None = None

    @property
    def method(self) -> str:
        return self.route.method

    @property
    def path(self) -> str:
        return self.route.path

    @property
    def key(self) -> str:
        return self.route.key

    @property
    def capability(self) -> str | None:
        return self.route.capability

    @property
    def gate(self) -> str:
        """What the capability column says: one name, or where to read it."""
        if self.capability:
            return self.capability
        if self.per_key:
            return f"per key: {self.per_key}"
        return "none"

    def held_by(self, capabilities: set[str]) -> bool:
        """Whether somebody with these capabilities may perform this route.

        A route with no gate at all is held by everybody who can sign in; that
        is a fact about the route and is reported as `none` beside it.
        """
        return not self.any_of or bool(self.any_of & capabilities)

    def proposable_by(self, capabilities: set[str]) -> bool:
        """Whether the assistant could propose this action for them: a tool
        sends it, and they are allowed to perform it."""
        return bool(self.tools) and self.held_by(capabilities)


@dataclass(frozen=True)
class Run:
    actions: tuple[Action, ...]
    #: Every write tool found in the source, whether or not its route matched.
    tools: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.actions)

    @property
    def on_a_screen(self) -> tuple[Action, ...]:
        return tuple(a for a in self.actions if a.screens)

    @property
    def unexplained(self) -> tuple[Action, ...]:
        return tuple(a for a in self.actions if not a.tools and a.reason is None)

    @property
    def stale_reasons(self) -> tuple[Action, ...]:
        return tuple(a for a in self.actions if a.tools and a.reason is not None)

    @property
    def reasons_for_routes_that_do_not_exist(self) -> tuple[str, ...]:
        keys = {a.key for a in self.actions}
        return tuple(sorted(k for k in EXCLUDED if k not in keys))


def covering_tools(method: str, path: str,
                   writes: list[ToolWrite] | None = None) -> tuple[str, ...]:
    """The write tools that send one method and path, in source order.

    Public because the ratchet is not the only reader: `test_module_wall` asks
    it of a plant with a module switched off, whose route list is shorter than
    this process's, and it must ask in exactly the way the ratchet asks.
    """
    route = Route(method.upper(), path, None)
    out: list[str] = []
    for write in (writes if writes is not None else tool_writes()):
        if _sends(route, write) and write.tool not in out:
            out.append(write.tool)
    return tuple(out)


def _gate_of(route: Route) -> tuple[frozenset[str], str | None]:
    """The capabilities that let a person perform one route, and where they
    come from when it is not the route's own dependency.

    The per-key set is `agent.needs_any`'s - the registry read the endpoint
    itself does - rather than a list written out again here, so a domain whose
    settings become live is covered without touching this file.
    """
    if route.capability:
        return frozenset({route.capability}), None
    named = PER_KEY.get(route.key)
    if named is None:
        return frozenset(), None
    sentence, tool = named
    from fsmes.services import agent

    return frozenset(agent.needs_any(tool) or set()), sentence


def scan() -> Run:
    """Read the routes, the tools and the screens, and line them up."""
    from fsmes.services.assistant import SURFACES

    writes = tool_writes()
    actions = []
    for route in write_routes():
        tools = covering_tools(route.method, route.path, writes)
        any_of, per_key = _gate_of(route)
        actions.append(Action(
            route=route,
            tools=tools,
            reason=EXCLUDED.get(route.key),
            screens=screens_calling(route.path),
            walks=tuple(t for t in tools if t in SURFACES),
            any_of=any_of,
            per_key=per_key,
        ))
    return Run(actions=tuple(actions),
               tools=tuple(sorted({w.tool for w in writes})))


# --------------------------------------------------------------- the report

def role_capabilities(role: str) -> set[str]:
    """What a built-in role grants. Raises `KeyError` on a name that is not one."""
    from fsmes.services import capabilities

    return set(capabilities.BUILTIN_ROLES[role]["capabilities"])


#: The roles the summary reports on unless one is named: the three a plant
#: signs people in as, plus the account the tools themselves run as.
REPORTED_ROLES = ("operator", "supervisor", "admin", "agent")


def summary(run: Run, role: str) -> str:
    """One sentence per role, in the shape the handoff asked for."""
    caps = role_capabilities(role)
    on_screens = run.on_a_screen
    proposable = [a for a in on_screens if a.proposable_by(caps)]
    walks = [a for a in proposable if a.walks]
    written = [a for a in on_screens if not a.tools and a.reason]
    return (f"{role}: {len(proposable)} of {len(on_screens)} screen actions the "
            f"assistant can propose; {len(walks)} with a walk; "
            f"{len(written)} by written reason.")


def _wrap(text: str, width: int, indent: str) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width=width, initial_indent=indent,
                         subsequent_indent=indent) or [indent.rstrip()]


def _tool_cell(action: Action) -> str:
    if action.tools:
        return ", ".join(action.tools)
    if action.reason is None:
        return "NOTHING, and no reason given"
    return "(reason below)"


def as_text(run: Run, *, role: str | None = None, width: int = 100) -> list[str]:
    """The table, for a terminal, and the reasons in full underneath.

    The reasons are sentences and a sentence does not fit in a column, so the
    table says which rows have one and they are printed below in the words the
    source keeps them in. Truncating them would be the report hiding the only
    part of itself that is an argument.
    """
    roles = (role,) if role else REPORTED_ROLES
    caps = {r: role_capabilities(r) for r in roles}
    rows = []
    for action in run.actions:
        rows.append((
            action.method,
            action.path,
            ", ".join(action.screens) or "-",
            _tool_cell(action),
            action.gate,
            "yes" if action.walks else ("-" if not action.tools else "no"),
            " ".join(("y" if action.held_by(caps[r]) else "n") for r in roles),
        ))
    headers = ("METHOD", "PATH", "SCREEN", "TOOL", "CAPABILITY", "WALK",
               " ".join(r[:4] for r in roles))
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    out = ["  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)).rstrip(),
           "  ".join("-" * w for w in widths)]
    for row in rows:
        out.append("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip())
    out.append("")
    out.append(f"{run.total} write routes in the API; {len(run.on_a_screen)} of them "
               f"a screen calls; {len(run.tools)} write tools send one.")
    for r in roles:
        out.append(summary(run, r))
    reasoned = [a for a in run.actions if not a.tools and a.reason]
    if reasoned:
        out += ["", f"{len(reasoned)} write routes have no tool, and why:"]
        for action in reasoned:
            out.append(f"  {action.key}")
            out += _wrap(action.reason or "", width, "      ")
    unexplained = run.unexplained
    if unexplained:
        out += ["", f"{len(unexplained)} with neither a tool nor a reason:"]
        out += [f"  {a.key}" for a in unexplained]
    return out


def as_markdown(run: Run, *, now: datetime | None = None) -> str:
    """The same table as the docs page, which is this command's output."""
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%d")
    lines = [
        "# What the assistant can do, against what a person can do",
        "",
        "Every route in this MES that changes something, and whether the floor",
        "assistant can perform it on somebody's behalf. Either a tool sends it,",
        "or the row says why not - and the reason is a rule, not a to-do.",
        "",
        f"{GENERATED_LINE}{stamp}.",
        "Do not edit it by hand: `tests/test_every_write_route_has_a_tool_or_a_reason.py`",
        "regenerates it and fails if this file differs.",
        "",
        f"There are **{run.total} write routes**; **{len(run.on_a_screen)}** of them a screen "
        f"calls; **{len(run.tools)}** write tools send one.",
        "",
    ]
    for role in REPORTED_ROLES:
        lines.append(f"- {summary(run, role)}")
    lines += [
        "",
        "A *screen action* is a write route some page script calls: the things a",
        "person can do by pointing at them. The route's own capability gate says",
        "whether a role may perform it, and a *walk* is a \"Show me\" surface in",
        "`services/assistant.py` - a tool without one can be proposed but not",
        "shown.",
        "",
        "## Every write route",
        "",
        "| Method | Path | Screen | Tool, or why not | Capability | Walk |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for action in run.actions:
        if action.tools:
            tool = ", ".join(f"`{t}`" for t in action.tools)
        elif action.reason:
            tool = "no tool — " + action.reason
        else:
            tool = "**nothing, and no reason given**"
        screens = ", ".join(f"`{s}`" for s in action.screens) or "—"
        walk = "yes" if action.walks else ("—" if not action.tools else "no")
        gate = f"`{action.capability}`" if action.capability else action.gate
        lines.append(f"| {action.method} | `{action.path}` | {screens} | {tool} "
                     f"| {gate} | {walk} |")
    lines += [
        "",
        "## Who holds what",
        "",
        "Whether each role may perform the route at all, from the route's own",
        "capability gate. A role that may not perform it is never offered the tool.",
        "",
        "| Action | Capability | " + " | ".join(REPORTED_ROLES) + " |",
        "| --- | --- | " + " | ".join("---" for _ in REPORTED_ROLES) + " |",
    ]
    caps = {r: role_capabilities(r) for r in REPORTED_ROLES}
    for action in run.actions:
        held = " | ".join("yes" if action.held_by(caps[r]) else "no"
                          for r in REPORTED_ROLES)
        gate = f"`{action.capability}`" if action.capability else action.gate
        lines.append(f"| {action.method} `{action.path}` | {gate} | {held} |")
    # No trailing blank line: `typer.echo` adds the newline that ends the file,
    # and a page one line longer than the command prints is a page the staleness
    # test calls stale.
    return "\n".join(lines)


def as_json(run: Run) -> str:
    import json

    caps = {r: role_capabilities(r) for r in REPORTED_ROLES}
    return json.dumps({
        "total_write_routes": run.total,
        "on_a_screen": len(run.on_a_screen),
        "write_tools": list(run.tools),
        "summary": {r: summary(run, r) for r in REPORTED_ROLES},
        "actions": [
            {"method": a.method, "path": a.path, "capability": a.capability,
             "screens": list(a.screens), "tools": list(a.tools),
             "reason": a.reason if not a.tools else None,
             "walk": bool(a.walks),
             "roles": {r: a.held_by(caps[r]) for r in REPORTED_ROLES}}
            for a in run.actions
        ],
    }, indent=2, sort_keys=True)
