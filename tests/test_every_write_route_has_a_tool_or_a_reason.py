"""Every write route has an MCP tool, or a written reason not to.

Principle 3 - every function callable via REST and MCP - as a ratchet, and the
sibling of `test_route_coverage`, which has held the same line for *screens*
since 2026-09-02.

The rule: for every route with method POST, PUT, PATCH or DELETE, either a
write tool sends it, or `assist_coverage.EXCLUDED` carries a line saying why
not. The list only shrinks - a route named there that has since grown a tool
fails here.

## This replaces `test_mcp_parity.py`

That file made the same claim from 2026-09-08 and was not strong enough to
catch either of the two gaps Scott found in the week of 2026-09-25, for three
reasons worth keeping written down:

* **It matched the tool source without the method.** `lots()` reads
  `GET /execution/lots`, and the string of that path in the source counted as
  covering `POST /execution/lots` - so a read tool covered a write route and
  the plant had no way to book a lot in. This reads each tool's calls out of
  its own syntax tree, with the method each one sends.
* **Three of its reasons were phases, not rules** - *"no operation
  start/complete tools yet (surfaces, phase 4)"* for the most basic thing an
  operator does. A list that may only shrink starts growing the moment a
  reason is allowed to mean "not done"; there is a test below that refuses one.
* **It measured nothing per role and printed nothing.** Whether an operator can
  be helped with the operator's own work was not a question it could answer.
  `fsmes assist coverage` answers it, and this file checks that the docs page
  is that command's output.

These tests key on identity, never on counts. `fsmes assist coverage` is where
the numbers are; a test that pinned them would go red on the day somebody adds
a route, which is the day it should stay green and say the new route has no
tool.
"""

from pathlib import Path

from fsmes import assist_coverage
from fsmes.assist_coverage import DOC_PATH, EXCLUDED, GENERATED_LINE

REPO = Path(__file__).resolve().parents[1]


def test_every_write_route_has_a_tool_or_a_written_reason():
    run = assist_coverage.scan()
    unexplained = sorted(a.key for a in run.unexplained)
    assert not unexplained, (
        "write routes no MCP tool sends and no reason explains - the assistant "
        "cannot do what a person can. Write the tool, or add the route to "
        "assist_coverage.EXCLUDED with the rule that keeps it a person's: "
        f"{unexplained}")


def test_a_reason_is_removed_once_its_route_has_a_tool():
    """The list only shrinks."""
    run = assist_coverage.scan()
    stale = sorted(a.key for a in run.stale_reasons)
    assert not stale, (
        "routes now sent by a tool; remove them from assist_coverage.EXCLUDED: "
        f"{stale}")


def test_no_reason_names_a_route_that_does_not_exist():
    run = assist_coverage.scan()
    unknown = run.reasons_for_routes_that_do_not_exist
    assert not unknown, f"EXCLUDED names routes that do not exist: {list(unknown)}"


def test_every_reason_says_why_rather_than_naming_the_work():
    """A reason is a rule. "not yet" is a handoff, not a reason.

    The failure this guards against is the ratchet being satisfied by a line
    that admits the gap instead of arguing for it, which is how a list that may
    only shrink starts growing.
    """
    excuses = ("not yet", "todo", "to do", "later", "phase", "coming")
    weak = sorted(key for key, reason in EXCLUDED.items()
                  if any(word in reason.lower() for word in excuses))
    assert not weak, (
        "these reasons read like work somebody has not done rather than a rule "
        f"that keeps the action a person's: {weak}")


def test_no_tool_performs_an_approval():
    """Decision 0035, from the other end.

    The agent role holds every `*.define` and no `*.approve`, and a confirmed
    "Do it" runs as AGENT on the person's behalf - so a tool that approved
    would sign the person's name. This asserts it against the routes rather
    than against the role table: nothing reaches a route gated on an approval.
    """
    run = assist_coverage.scan()
    signed = sorted(a.key for a in run.actions
                    if a.capability and a.capability.endswith(".approve") and a.tools)
    assert not signed, (
        "a tool reaches a route gated on an approval capability, which is the "
        f"person's signature (decision 0035): {signed}")


def test_no_tool_sets_a_password_or_ends_a_session():
    run = assist_coverage.scan()
    reached = sorted(a.key for a in run.actions
                     if a.path.startswith("/auth/") and a.tools
                     and a.path != "/auth/users")
    assert not reached, (
        "an agent never types a password and never signs anybody out: "
        f"{reached}")


def test_the_new_operator_actions_are_reachable_by_an_operator():
    """The four gaps this ratchet was written to close, named.

    Starting and completing a step of an order is the most basic thing an
    operator does, and `order_action` stopped at the order. Named here so that
    removing one of these tools fails a test that says what it was for.
    """
    run = assist_coverage.scan()
    tools = {a.key: a.tools for a in run.actions}
    assert "start_operation" in tools["POST /workorders/{code}/operations/{seq}/start"]
    assert "complete_operation" in tools["POST /workorders/{code}/operations/{seq}/complete"]
    assert "create_lot" in tools["POST /execution/lots"]
    assert "revise_document" in tools["POST /documents/{code}/revise"]

    operator = assist_coverage.role_capabilities("operator")
    by_key = {a.key: a for a in run.actions}
    for key in ("POST /workorders/{code}/operations/{seq}/start",
                "POST /workorders/{code}/operations/{seq}/complete",
                "POST /execution/lots"):
        assert by_key[key].proposable_by(operator), key


def test_the_docs_page_is_what_the_command_prints():
    """The page is the command's output, so it cannot drift from the code.

    Everything but one line: the date it was generated. Pinning that would
    turn the suite red at midnight, and a stale date on a page whose table is
    pinned misleads nobody - so it is deliberately not checked, and this
    docstring is where that is admitted.
    """
    page = (REPO / DOC_PATH).read_text(encoding="utf-8")
    fresh = assist_coverage.as_markdown(assist_coverage.scan())

    def undated(text: str) -> list[str]:
        return [line for line in text.splitlines()
                if not line.startswith(GENERATED_LINE)]

    assert undated(page) == undated(fresh), (
        f"{DOC_PATH} is out of date. Regenerate it: "
        "`fsmes assist coverage --markdown > docs/operate/assistant-coverage.md`")


def test_the_page_says_it_was_generated_and_when():
    page = (REPO / DOC_PATH).read_text(encoding="utf-8")
    dated = [line for line in page.splitlines() if line.startswith(GENERATED_LINE)]
    assert len(dated) == 1, "the page should say once when it was generated"


def test_a_tool_path_with_a_closed_set_of_actions_is_expanded():
    """`order_action` covers five routes, and the reader knows which five.

    The one piece of cleverness in the scanner: a tool whose path ends in an
    argument it only accepts a handful of literal values for is expanded into
    them, so the table names the routes rather than a shape.
    """
    writes = [w for w in assist_coverage.tool_writes() if w.tool == "order_action"]
    assert {w.path for w in writes} == {
        "/workorders/{...}/release", "/workorders/{...}/hold",
        "/workorders/{...}/resume", "/workorders/{...}/close",
        "/workorders/{...}/cancel"}


def test_a_write_that_is_not_a_post_is_read_with_its_own_method():
    """Putting a live setting in force is a PATCH, and the scanner reads that
    off the `method=` the tool passes rather than assuming every write posts."""
    writes = [w for w in assist_coverage.tool_writes()
              if w.tool == "write_plant_setting"]
    assert [(w.method, w.path) for w in writes] == [
        ("PATCH", "/dashboard/config/{...}/settings/{...}")]


def test_a_tool_never_counts_as_covering_a_longer_path():
    """`/coa/{order}` is not `/coa/pallet/{serial}`.

    A wildcard segment matches one segment, so two paths of different lengths
    never match - the mistake that would let one tool appear to cover a route
    nobody wrote.
    """
    run = assist_coverage.scan()
    by_key = {a.key: a for a in run.actions}
    assert by_key["POST /coa/{order}"].tools == ("issue_certificate",)
    assert by_key["POST /coa/pallet/{serial}"].tools == ("issue_pallet_certificate",)


def test_the_route_walk_finds_exactly_what_the_api_publishes():
    """The ratchet's own denominator, checked against a second reader.

    The routes come from walking the app, because a route object carries the
    capability it gates on and the published document does not. A walker that
    quietly missed a branch of the tree - FastAPI wraps an included router
    rather than copying its routes up, and that is an internal - would make
    every test in this file pass by having nothing to check. So the walk is
    compared against the OpenAPI document, which is what `test_route_coverage`
    counts, and the two must name the same routes.
    """
    from fsmes.api.app import create_app

    published = {(method.upper(), path)
                 for path, ops in create_app().openapi()["paths"].items()
                 for method in ops
                 if method.upper() in assist_coverage.WRITE_METHODS}
    walked = {(r.method, r.path) for r in assist_coverage.write_routes()}
    assert walked == published, (
        "the route walk and the published document disagree; "
        f"only walked: {sorted(walked - published)}, "
        f"only published: {sorted(published - walked)}")


def test_the_write_routes_with_no_capability_gate_are_the_ones_we_know_about():
    """A route whose gate this reader cannot see reads as ungated, and an
    ungated write route in the table is a claim that anybody signed in may
    perform it. Four are genuinely ungated - signing in, signing out, and the
    assistant's own two front doors - and one is gated per key by the section
    the key belongs to. Anything else appearing here is either a route somebody
    forgot to gate or a gate written somewhere this reader does not look.
    """
    run = assist_coverage.scan()
    ungated = sorted(a.key for a in run.actions if not a.any_of)
    assert ungated == [
        "POST /assist/agent",
        "POST /assist/agent/confirm",
        "POST /assist/agent/decline",
        "POST /assist/ask",
        "POST /auth/login",
        "POST /auth/logout",
    ], ungated
    per_key = [a for a in run.actions if a.per_key]
    assert [a.key for a in per_key] == [
        "PATCH /dashboard/config/{domain}/settings/{key}"]
    # And its capabilities are read from the registry, so they are the sections'
    # own `define` names rather than a list kept here.
    assert per_key[0].any_of >= {"process.define", "quality.define"}
    assert not per_key[0].held_by(assist_coverage.role_capabilities("operator"))
    assert per_key[0].held_by(assist_coverage.role_capabilities("admin"))
