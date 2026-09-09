"""Every write route has an agent tool, or a written reason not to.

Principle 3: every function callable via REST and MCP. The product MCP
server was born with 17 tools and grew to 27 while six modules landed with
none at all, because nothing measured it. Same ratchet as the screens
(`test_route_coverage.py`), for the agent surface: a write route that no
tool module mentions must be listed with a reason, and a listed route that a
tool now calls must come off the list. Reads are exempt for now - the tool
count would triple with lookalikes of every list - and join the ratchet
module by module as their read tools land.
"""

import re
from pathlib import Path

from fsmes.api.app import create_app

SRC = Path(__file__).resolve().parents[1] / "src" / "fsmes"

# Write routes no tool calls, and why. The reasons name the plan phase that
# closes them; approvals held by humans are permanent exclusions.
EXCLUDED = {
    # session plumbing: the agent signs in once, as itself
    "POST /auth/logout": "the server owns the agent's session",
    "POST /auth/users/{code}/password": "a password is a human secret; not an agent action",
    # dev tooling
    "POST /design/chat": "dev tool, not a plant operation",
    # human-held approvals (principle 3: agents propose, people approve)
    "POST /documents/{code}/approve/{revision}": "approval is a human capability",
    "POST /documents/{code}/withdraw": "withdrawal is a human capability",
    "POST /triggers/{code}/approve": "putting logic in force against a live plant is a human capability",
    "POST /triggers/{code}/withdraw": "taking it out of force is the same human capability",
    "POST /adjustments/{code}/approve": "the human in the loop before a PLC write; never a tool",
    "POST /adjustments/{code}/reject": "the same decision, the other way",
    # operations the assistant widget owns
    "POST /assist/ask": "the in-UI assistant; agents ask the tools directly",
    "POST /assist/agent": "the in-UI agent's own front door; it calls the tools itself",
    "POST /assist/agent/confirm": "a person's click on a proposal card; never a tool",
    "POST /assist/agent/decline": "the same click, the other way",
    # POST /execution/report came off this list when book_output landed (agent-in-the-UI, phase 2)
    # maintenance: surfaces, phase 3
    # scheduling: surfaces, phase 4
    # quality depth: surfaces, phase 5
    # serialization: surfaces, phase 6
    # master data and orders detail: surfaces, phase 7
    "POST /workorders/{code}/operations/{seq}/start": "no operation start/complete tools yet (surfaces, phase 4)",
    "POST /workorders/{code}/operations/{seq}/complete": "no operation start/complete tools yet (surfaces, phase 4)",
    "POST /documents/{code}/revise": "no revise tool yet (surfaces, phase 7)",
}


def write_routes() -> list[tuple[str, str]]:
    paths = create_app().openapi()["paths"]
    return sorted((method.upper(), path) for path, ops in paths.items() for method in ops
                  if method.upper() in {"POST", "PUT", "PATCH", "DELETE"})


def tool_source() -> str:
    text = [(SRC / "mcp_server.py").read_text(encoding="utf-8")]
    text += [p.read_text(encoding="utf-8") for p in sorted((SRC / "mcp").glob("*.py"))]
    # `{anything}` inside an f-string stands for one path segment.
    return re.sub(r"\{[^}]*\}", "X", "\n".join(text))


def _pattern(path: str) -> re.Pattern:
    parts = []
    for segment in path.strip("/").split("/"):
        if segment.startswith("{"):
            parts.append(r"[^/\"'?\s]+")
        else:
            parts.append("(?:" + re.escape(segment) + "|X)")
    return re.compile(r"[\"']/" + "/".join(parts) + r"(?=[\"'?\s])")


def test_every_write_route_has_a_tool_or_a_reason():
    source = tool_source()
    every = {f"{m} {p}" for m, p in write_routes()}
    covered = {f"{m} {p}" for m, p in write_routes() if _pattern(p).search(source)}

    unexplained = sorted(every - covered - set(EXCLUDED))
    assert not unexplained, (
        "write routes no tool calls and no reason explains - add a tool in "
        f"src/fsmes/mcp/<module>.py, or an EXCLUDED entry with a reason: {unexplained}")

    stale = sorted(k for k in EXCLUDED if k in covered)
    assert not stale, f"routes now reached by a tool; remove them from EXCLUDED: {stale}"

    unknown = sorted(k for k in EXCLUDED if k not in every)
    assert not unknown, f"EXCLUDED names routes that do not exist: {unknown}"
