"""Every API route has a screen, or a written reason not to.

On 2026-09-02 an audit found 44 of 111 routes with no screen at all - six
modules built and invisible. Nothing had noticed because nothing measured
it. This is the measurement, as a ratchet: a route that no screen's script
mentions must be listed below with a reason, and a listed route that a
screen now calls must be removed from the list. The list only shrinks.

Coverage is textual - a route counts as reached when a page script contains
its path, with `${...}` interpolations standing for any segment - and it
ignores the HTTP method, so a path a screen only reads counts as reached
for its writes too. That is deliberately loose: the point is to notice a
module nobody wired up, not to prove a button works. (`POST /quality/specs`
has no form yet, but the Quality screen reads the path; phase 5.)
"""

import re
from pathlib import Path

from fsmes.api.app import create_app

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"

# Routes no screen calls, and why. Reasons name the plan phase that closes
# them, so this list is also the work queue. Plan: 2026-09-02 surfaces.
EXCLUDED = {
    # auth and session plumbing a screen does not call by path
    # design chat history is a dev tool with no browse screen
    "GET /design/conversations": "dev tool; conversations are read by /design-triage",
    "GET /design/conversations/{conversation_id}": "dev tool; conversations are read by /design-triage",
    # system plumbing
    "GET /health": "for load balancers and fsmes plant status, not a screen",
    "GET /metrics": "for Prometheus, not a screen",
    "GET /audit": "the Ops screen reads the richer /ops/activity instead",
    # kpis router: superseded by /dashboard/summary and /analysis; candidate for removal
    "GET /kpis/oee/{equipment_code}": "the `fsmes demo` walkthrough reads it; screens use /equipment/{code}/oee",
    "GET /kpis/orders": "the `fsmes demo` walkthrough reads it; screens use /workorders/summary",
    # maintenance workspace: surfaces, phase 3
    # scheduling workspace: surfaces, phase 4
    # quality depth: surfaces, phase 5
    # serialization: surfaces, phase 6
    # master data: surfaces, phase 7
    "GET /execution/bom/{material_code}": "no material page yet (surfaces, phase 7)",
    "GET /triggers/{code}/firings": "the screen shows the plant-wide firing log; "
                                     "this one serves the trigger_firings tool",
    "GET /adjustments/{code}": "the screen lists the queue; the detail route serves the adjustment tool",
    "GET /coa/pallet/{serial}/data": "the screen renders the pallet's certificate; the data route serves the "
                                     "pallet_certificate tool",
    "GET /workorders/{code}/wip": "shown per line on the Line page; the Order page shows it in phase 4",
}


def api_routes() -> list[tuple[str, str]]:
    """Every documented route, from the OpenAPI document the app itself
    publishes - the one listing that survives FastAPI's routing internals."""
    paths = create_app().openapi()["paths"]
    return sorted((method.upper(), path) for path, ops in paths.items() for method in ops)


def page_scripts() -> str:
    text = []
    for path in sorted(WEB.glob("*.js")) + sorted((WEB / "line").glob("*.js")):
        text.append(path.read_text(encoding="utf-8"))
    joined = "\n".join(text)
    # `${anything}` stands for one path segment of any value.
    return re.sub(r"\$\{[^}]*\}", "X", joined)


def _pattern(path: str) -> re.Pattern:
    parts = []
    for segment in path.strip("/").split("/"):
        if segment.startswith("{"):
            parts.append(r"[^/\"'`?\s]+")
        else:
            parts.append("(?:" + re.escape(segment) + "|X)")
    # A path may be followed by a quote, a query string, or an interpolated
    # tail (`/analysis/oee${query}` normalises to `/analysis/oeeX`).
    return re.compile(r"[\"'`]/" + "/".join(parts) + r"(?=[\"'`?\sX])")


def test_every_route_has_a_screen_or_a_reason():
    scripts = page_scripts()
    covered = {f"{m} {p}" for m, p in api_routes() if _pattern(p).search(scripts)}
    every = {f"{m} {p}" for m, p in api_routes()}

    unexplained = sorted(every - covered - set(EXCLUDED))
    assert not unexplained, (
        "API routes no screen calls and no reason explains - wire a screen, "
        f"or add them to EXCLUDED with a reason: {unexplained}")

    stale = sorted(k for k in EXCLUDED if k in covered)
    assert not stale, f"routes now reached by a screen; remove them from EXCLUDED: {stale}"

    unknown = sorted(k for k in EXCLUDED if k not in every)
    assert not unknown, f"EXCLUDED names routes that do not exist: {unknown}"
