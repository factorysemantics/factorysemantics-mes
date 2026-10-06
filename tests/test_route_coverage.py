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

from fsmes.api.app import create_app
from fsmes.assist_coverage import page_scripts, screen_pattern

# Routes no screen calls, and why. Reasons name the plan phase that closes
# them, so this list is also the work queue. Plan: 2026-09-02 surfaces.
EXCLUDED = {
    # auth and session plumbing a screen does not call by path
    # design chat history is a dev tool with no browse screen
    "GET /design/conversations": "dev tool; conversations are read by /design-triage",
    "GET /design/conversations/{conversation_id}": "dev tool; conversations are read by /design-triage",
    # the plant's downtime vocabulary: the station screen reads the catalogue
    # and the plant dashboard's panel is how a draft is found and signed
    "GET /equipment/downtime-reasons/drafts":
        "the vocabulary's own screen is the follow-up; /dashboard's pending-approvals "
        "panel reads the cross-kind endpoint instead, so this one is for an agent and "
        "the API",
    "POST /equipment/downtime-reasons/{code}/approve/{revision}":
        "the panel does call it, by the path the server hands each row - which is what "
        "lets one panel sign several kinds - so the path is never written in a script",
    # a sample of n pieces, posted whole. No screen posts one yet: the panel
    # that shows a sample and the form that records one are
    # `spc-xbar-chart-and-panel`. What posts samples today is the simulated
    # floor, over HTTP, like any other client - and the agent's record_sample
    # tool sends it, so a person asking their assistant can already record one.
    "POST /quality/samples":
        "the sample panel and its form are the follow-up (spc-xbar-chart-and-panel); "
        "the simulated floor and the agent's record_sample tool are what post one "
        "today",
    # the plant's non-conformance severities: the same two, for the same two
    # reasons. That the excuses read identically is the point - the second
    # vocabulary joined the panel without the panel being touched.
    "GET /quality/severities/drafts":
        "the vocabulary's own screen lists everything instead; /dashboard's "
        "pending-approvals panel reads the cross-kind endpoint, so this one is for an "
        "agent and the API",
    "POST /quality/severities/{code}/approve/{revision}":
        "the panel does call it, by the path the server hands each row - which is what "
        "lets one panel sign several kinds - so the path is never written in a script",
    # the three deep-analysis reads: the plant's own arithmetic behind the
    # worked example of docs/design/deep-analysis.md, serving the tools until
    # the tab that draws them exists
    "GET /analysis/trace/rollup": "D5 draws them - `analysis-in-the-ai-tab`, rewritten "
                                  "against the design page. Until then these serve the "
                                  "analysis agent's trace_rollup",
    "GET /analysis/trace/graph": "D5 draws them; D4 (`analysis-graph-shape`) is the "
                                 "`graph` shape in kit.js this one feeds",
    "GET /analysis/maintenance/mttr": "D5 draws them; until then it serves "
                                      "maintenance_mttr",
    # naming a stop after the fact: no screen offers it yet
    "POST /equipment/{code}/stops/label":
        "no screen names a past stop yet - the station screen labels one at the "
        "moment it begins, which is the moment nobody knows why. The simulated "
        "floor and an operator with the API are the callers until the panel that "
        "opens on a point (`spc-point-panel`) grows the control",
    # system plumbing
    "GET /metrics": "for Prometheus, not a screen",
    "GET /pack": "for the fleet console, which is a page of its own and not a plant's screen",
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
    "GET /quality/nonconformances/{code}": "the Quality screen's list already carries each "
                                           "non-conformance's history; the detail route serves "
                                           "the nonconformance tool",
    "GET /coa/pallet/{serial}/data": "the screen renders the pallet's certificate; the data route serves the "
                                     "pallet_certificate tool",
    "GET /workorders/{code}/wip": "shown per line on the Line page; the Order page shows it in phase 4",
    "GET /dashboard/config": "the index of the Configuration workspaces. The nav bar draws them "
                             "from FS.NAV, which is checked against the registry by "
                             "test_ui_nav; this route exists so that something outside the "
                             "browser - the assistant searching every workspace for the setting "
                             "somebody described in their own words - can ask which workspaces "
                             "there are instead of naming one that does not exist and reading "
                             "the 404",
}


def api_routes() -> list[tuple[str, str]]:
    """Every documented route, from the OpenAPI document the app itself
    publishes - the one listing that survives FastAPI's routing internals."""
    paths = create_app().openapi()["paths"]
    return sorted((method.upper(), path) for path, ops in paths.items() for method in ops)


# `page_scripts` and `screen_pattern` are imported rather than written here,
# where they used to live. The sibling ratchet - every write route has a tool
# or a reason - reports *which* screen calls a route, and must count a route as
# reached in exactly the way this test does; two copies of the rule would be
# two answers to "does a screen call it".


def test_every_route_has_a_screen_or_a_reason():
    scripts = page_scripts()
    covered = {f"{m} {p}" for m, p in api_routes() if screen_pattern(p).search(scripts)}
    every = {f"{m} {p}" for m, p in api_routes()}

    unexplained = sorted(every - covered - set(EXCLUDED))
    assert not unexplained, (
        "API routes no screen calls and no reason explains - wire a screen, "
        f"or add them to EXCLUDED with a reason: {unexplained}")

    stale = sorted(k for k in EXCLUDED if k in covered)
    assert not stale, f"routes now reached by a screen; remove them from EXCLUDED: {stale}"

    unknown = sorted(k for k in EXCLUDED if k not in every)
    assert not unknown, f"EXCLUDED names routes that do not exist: {unknown}"
