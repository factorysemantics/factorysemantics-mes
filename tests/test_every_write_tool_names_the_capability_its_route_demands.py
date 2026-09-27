"""Every write tool names the capability its own route demands.

The sibling of `test_every_write_route_has_a_tool_or_a_reason`, from the other
end. That ratchet asks whether every write *route* has a tool. This one asks
whether every write *tool* says what a person must hold for it to be offered -
because a tool that does not say is offered to everybody who can sign in.

## What went wrong, measured on `f9705990`

Nine write tools - `add_person`, `register_gauge`, `calibrate_gauge`,
`issue_certificate`, `issue_pallet_certificate`, `produce_batch`, `pack_unit`,
`set_unit_status`, `erp_retry` - were in neither `agent.NEEDS` nor
`agent.PER_CALL_NEEDS`. `agent.catalogue` filters on those two dictionaries and
nothing else, so it offered all nine to anybody holding `plant.read`: seven of
them to an operator, two to a supervisor, seven to the AGENT account itself.

Nothing was written. Each route still refused the call with its own
`require(...)`, which is the second of the two gates decision 0035 asks for.
But the model was shown a tool it would be refused on, the person got a refusal
for something their own assistant had just offered them, and
`docs/operate/assistant-coverage.md` - which reads the route - said an operator
may not issue a certificate while the catalogue was offering to issue one. The
page was right about the API and wrong about the assistant, which is the worse
kind of wrong, because the page is what somebody reads instead of trying it.

## The rule

Read, never written down twice: `assist_coverage` already parses each tool's
source for the writes it sends and each route for its own `require(...)`, so the
capability in `NEEDS` is checked against the capability the route demands rather
than against a second list kept here.

Two tools send routes gated on more than one capability - `create_order` can
release as well as create, and `order_action` covers release, hold, resume,
close and cancel across two gates. `NEEDS` names one capability per tool, so
for those two it names one the routes really demand and the API refuses the
rest per call. That is said out loud in `test_a_tool_whose_routes_want_two`
rather than left as a hole this file steps around.

These tests key on identity, never on counts.
"""

import pytest

from fsmes import assist_coverage
from fsmes.services import agent
from fsmes.services import capabilities as caps

#: The nine. Named so that a change putting one of them back out of `NEEDS`
#: fails a test that says what it was for, the way
#: `test_the_new_operator_actions_are_reachable_by_an_operator` does for #111.
WAS_UNGATED = (
    "add_person", "calibrate_gauge", "erp_retry", "issue_certificate",
    "issue_pallet_certificate", "pack_unit", "produce_batch", "register_gauge",
    "set_unit_status",
)


def route_capabilities() -> dict[str, frozenset[str]]:
    """Per write tool, the capabilities the routes it sends demand.

    Empty for a tool every one of whose routes has no `require(...)` of its
    own - which today is none of them, and is asserted below.
    """
    run = assist_coverage.scan()
    out: dict[str, set[str]] = {tool: set() for tool in run.tools}
    for action in run.actions:
        for tool in action.tools:
            if action.capability:
                out[tool].add(action.capability)
            else:
                out[tool] |= set(action.any_of)
    return {tool: frozenset(found) for tool, found in out.items()}


def test_every_write_tool_names_a_capability_or_says_it_is_decided_per_call():
    """The ratchet. A write tool in neither dictionary is offered to anybody who
    can sign in, and this is the test that was missing when nine of them were.
    """
    run = assist_coverage.scan()
    named = set(agent.NEEDS) | set(agent.PER_CALL_NEEDS)
    silent = sorted(set(run.tools) - named)
    assert not silent, (
        "these write tools name no capability, so `agent.catalogue` offers them "
        "to anybody holding plant.read and the route refuses the call when it "
        "arrives. Add each one to agent.NEEDS with the capability its route "
        "demands, or to agent.PER_CALL_NEEDS with why one name will not do: "
        f"{silent}")


def test_nothing_names_a_capability_for_a_tool_that_does_not_write():
    """The list only covers write tools; a read tool in it would be a gate on
    something that changes nothing."""
    run = assist_coverage.scan()
    named = set(agent.NEEDS) | set(agent.PER_CALL_NEEDS)
    not_writes = sorted(named - set(run.tools))
    assert not not_writes, (
        "these are named as needing a capability but send no write: "
        f"{not_writes}")


def test_the_capability_named_is_one_the_tools_own_route_demands():
    """Read from the route, not chosen. A tool gated on a capability its route
    does not ask for is either offered to people the API will refuse, or
    withheld from people the API would have allowed."""
    wanted = route_capabilities()
    wrong = {tool: (need, sorted(wanted.get(tool, ())))
             for tool, need in agent.NEEDS.items()
             if wanted.get(tool) and need not in wanted[tool]}
    assert not wrong, (
        "NEEDS names a capability the tool's own routes do not demand "
        f"(tool: named, demanded): {wrong}")


@pytest.mark.parametrize("tool", WAS_UNGATED)
def test_the_nine_that_were_offered_to_everybody_name_their_capability(tool):
    """One case per tool, named, so removing an entry fails a test that says
    which gap it closed."""
    assert tool in agent.NEEDS, f"{tool} is back to being offered to anybody"
    assert agent.NEEDS[tool] in caps.CAPABILITIES
    assert agent.NEEDS[tool] in route_capabilities()[tool]


def test_every_write_tool_sends_a_route_with_a_gate_of_its_own():
    """The denominator. If a tool's route had no `require(...)` this file would
    have nothing to read the capability from, and every test above would pass
    by vacuum."""
    ungated = sorted(tool for tool, found in route_capabilities().items() if not found)
    assert not ungated, (
        "these write tools send only routes with no capability gate at all, so "
        f"there is nothing for NEEDS to be read from: {ungated}")


def test_a_tool_whose_routes_want_two_capabilities_names_one_of_them():
    """Said out loud rather than stepped around.

    `create_order` posts the order and can release it in the same call;
    `order_action` sends five routes across two gates. `NEEDS` is one capability
    per tool, read before any argument exists, so it names the one that gets the
    tool offered and the API refuses the rest per call - the same two-gate shape
    as `PER_CALL_NEEDS`, narrower because the set is two and not the registry.
    """
    wanted = route_capabilities()
    several = {tool: sorted(found) for tool, found in wanted.items() if len(found) > 1
               and tool not in agent.PER_CALL_NEEDS}
    assert several == {"create_order": ["orders.create", "orders.release"],
                       "order_action": ["orders.close", "orders.release"]}, several
    assert agent.NEEDS["create_order"] == "orders.create"
    assert agent.NEEDS["order_action"] == "orders.release"


@pytest.mark.parametrize("role", assist_coverage.REPORTED_ROLES)
def test_the_catalogue_offers_exactly_what_the_coverage_page_says_it_may(role):
    """The two readers of the same question, made to agree.

    `docs/operate/assistant-coverage.md` answers "may this role perform this
    route" from the route's own gate. `agent.catalogue` answers "is this role
    offered this tool" from `NEEDS`. On `f9705990` they disagreed about seven
    tools for an operator and two for a supervisor, and the page is what
    somebody reads instead of trying it. Nothing makes them agree except this
    test, so it is the one that keeps the page true about the assistant as well
    as about the API.
    """
    run = assist_coverage.scan()
    held = assist_coverage.role_capabilities(role)
    offered = {t["name"] for t in agent.catalogue(held) if t["write"]}
    page_says = {tool for action in run.actions for tool in action.tools
                 if action.held_by(held)}
    assert offered == page_says, (
        f"offered to a {role} but the page says they may not: "
        f"{sorted(offered - page_says)}; the page says they may but it is not "
        f"offered: {sorted(page_says - offered)}")
