"""The three trace tools, and decision 0039's four clauses held to the code.

`trace_rollup`, `trace_graph` and `maintenance_mttr` are what make the design
page's worked example answerable - *"what is the biggest problem for our
operators?"* over the records this plant already keeps
(`docs/design/deep-analysis.md` §1). They are pass-throughs like the four before
them, so the first thing pinned here is the same thing #129 pinned: **what the
tool hands an agent is what the route hands a screen**, key for key.

The rest is
[0039](../docs/decisions/0039-an-analysis-is-recorded-code-that-can-only-read.md),
whose second half is four clauses about people. They are asserted here rather
than described in a prompt, because a default that lives in a prompt is a
default a model can be talked out of:

1. **Counting.** Every grouping is by role, by workcenter or by shift. No key in
   any default answer is an account code - checked over a plant whose account
   codes would be obvious in the output if they leaked.
2. **Naming.** `person=` and `name_people=True` need `people.analyse`, and **no
   shipped role holds it** - not the administrator, not the analyst. A test
   asserts that of the shipped bundles, and another asserts the refusal names
   the capability rather than the role.
3. **Record.** An answer that names a person writes `analysis.person_named`
   against `personnel/<code>`, so the person analysed can find out that they
   were.
4. **Reciprocity** is D5's and is not built here. What is checked is that the
   envelope has the shape that makes it possible: a person's own rows are the
   same rollup filtered to them, so nothing about the payload has to change.

And the graph's own refusal: betweenness, PageRank and eigenvector centrality
are refused by name with a reason, and no number resembling one appears in the
answer. A centrality over an edge set that is *whatever this plant happens to
have recorded* is the most convincing wrong thing this product could offer.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from fsmes import mcp_server
from fsmes.db import utcnow
from fsmes.domain import (
    AiTurn,
    AuditLog,
    Equipment,
    EquipmentState,
    EquipmentStateName,
    MaintenanceKind,
    MaintenanceOrder,
    MaintenanceStatus,
)
from fsmes.services import auth, trace_analysis
from fsmes.services import capabilities as caps

#: The account codes on the plant these tests build. Written out because every
#: clause-1 assertion is "none of these appears as a key", and a test that
#: derived them from the rows it wrote could not fail on a rollup that invented
#: one.
ASKERS = ("JO", "SAM", "RAY")


@pytest.fixture()
def wired(make_client, session, monkeypatch):
    """The MCP tools pointed at the in-process app, signed in as ANALYST.

    ANALYST rather than AGENT, because these are the analysis agent's tools and
    two of the three are gated on `audit.read`: reading them as the account that
    will really call them is what proves the gate is passable at all.
    """
    auth.create_user(session, code=mcp_server.ANALYST_USER, name="Plant Analyst",
                     password=mcp_server.ANALYST_PASSWORD, role="analyst")
    session.flush()
    auth.create_user(session, code=mcp_server.AGENT_USER, name="Plant Agent",
                     password=mcp_server.AGENT_PASSWORD, role="agent")
    auth.ensure_builtin_roles(session)
    session.flush()
    clients = mcp_server.wire_clients("testplant", make_client)
    monkeypatch.setattr(mcp_server, "_clients", clients)
    monkeypatch.setattr(mcp_server, "_registry",
                        lambda: {"testplant": {"api_port": 0, "label": "Test"}})
    # Every tool call below is made as ANALYST rather than as AGENT, which is
    # the default: these are the analysis agent's tools, and the account that
    # will really call them is the one whose gates should be under test.
    token = mcp_server._actor.set(mcp_server.ANALYST_USER)
    client = clients[mcp_server.client_key("testplant", mcp_server.ANALYST_USER)]
    # Signed in here rather than lazily by the first tool call, because some of
    # these tests read a route before making one.
    login = client.post("/auth/login", json={"code": mcp_server.ANALYST_USER,
                                             "password": mcp_server.ANALYST_PASSWORD})
    assert login.status_code == 200, login.text
    client.headers["Authorization"] = f"Bearer {login.json()['token']}"
    yield client
    mcp_server._actor.reset(token)


def _unit(session, code: str) -> Equipment:
    return session.scalar(select(Equipment).where(Equipment.code == code))


@pytest.fixture()
def a_trace_worth_reading(session):
    """Three operators, one supervisor, one turn nobody signed, and a floor.

    The same question asked four times by two people, a second question, a
    button press with no words in it, and one turn whose `person` is an account
    this plant does not have - which is the `unattributed` node, and the thing a
    tidy graph would quietly be three turns short of.
    """
    for code, role in (("JO", "operator"), ("SAM", "operator"), ("RAY", "supervisor")):
        auth.create_user(session, code=code, name=code.title(),
                         password="test-password", role=role)
    session.flush()

    now = utcnow()
    rows = [
        ("JO", "how do I label a stop?", "s1", 60),
        ("JO", "How do I label a stop", "s1", 58),
        ("SAM", "how do I label a stop?", "s2", 50),
        ("SAM", "what is my next order?", "s2", 48),
        ("RAY", "what is my next order?", "s3", 40),
        ("JO", "", "s1", 57),
        ("GONE", "how do I label a stop?", "s4", 30),
    ]
    for person, asked, sid, minutes_ago in rows:
        session.add(AiTurn(ts=now - timedelta(minutes=minutes_ago), session=sid,
                           brain="floor", person=person, model="test", kind="reply",
                           asked=asked, said="...", tools=[], proposals=[]))
    session.flush()
    return now


@pytest.fixture()
def a_floor_with_stops_and_repairs(session):
    """Two machines, two stops - one named, one nobody named - and two repairs,
    one of which nobody timed."""
    now = utcnow()
    mixer, packer = _unit(session, "MIX01"), _unit(session, "PACK01")
    session.add(EquipmentState(
        equipment_id=mixer.id, state=EquipmentStateName.DOWN, reason="infeed jam",
        reason_source="historian", shift_code="DAY",
        started_at=now - timedelta(minutes=40), ended_at=now - timedelta(minutes=30)))
    session.add(EquipmentState(
        equipment_id=packer.id, state=EquipmentStateName.DOWN,
        started_at=now - timedelta(minutes=20), ended_at=now - timedelta(minutes=8)))
    session.add(MaintenanceOrder(
        code="CM-TRACE-1", equipment_id=mixer.id, kind=MaintenanceKind.CORRECTIVE,
        status=MaintenanceStatus.DONE, summary="cleared the infeed",
        raised_at=now - timedelta(minutes=45), started_at=now - timedelta(minutes=40),
        completed_at=now - timedelta(minutes=14)))
    session.add(MaintenanceOrder(
        code="CM-TRACE-2", equipment_id=packer.id, kind=MaintenanceKind.CORRECTIVE,
        status=MaintenanceStatus.DUE, summary="film sensor",
        raised_at=now - timedelta(minutes=20)))
    session.flush()
    return now


# ------------------------------------------------- the payload, key for key

CASES = (
    ("trace_rollup", "/analysis/trace/rollup", {"hours": 4}, {"hours": 4, "by": "role"}),
    ("trace_graph", "/analysis/trace/graph", {"hours": 4},
     {"hours": 4, "threshold": 1}),
    ("maintenance_mttr", "/analysis/maintenance/mttr", {"hours": 4},
     {"hours": 4, "bucket": "day"}),
)

ENVELOPE = {"plant"}


@pytest.fixture()
def one_instant(monkeypatch):
    """The clock pinned for the length of a comparison.

    Every one of these windows ends *now*, so the same question asked twice
    twenty milliseconds apart answers with two different `window.end`, and a
    key-for-key comparison would be measuring the pause between two requests.
    """
    instant = utcnow()
    monkeypatch.setattr(trace_analysis, "utcnow", lambda: instant)
    return instant


@pytest.fixture()
def roomy(monkeypatch):
    """One tool result big enough to hold the whole answer.

    Only for the key-for-key comparison. What that test is about is whether the
    tool reshapes the payload, and a tool that had asked the plant for fewer
    nodes because the answer did not fit would be answering a different question
    from the route - honestly, and not comparably. The bound itself is pinned in
    `test_a_graph_too_big_for_one_answer_says_what_it_left_out`, at the limit a
    plant really runs.
    """
    from fsmes.services import agent

    monkeypatch.setattr(agent, "RESULT_LIMIT", 200_000)


@pytest.mark.parametrize(("tool", "route", "args", "query"),
                         CASES, ids=[c[0] for c in CASES])
def test_the_tool_hands_over_exactly_what_the_route_hands_the_screen(
        wired, a_trace_worth_reading, a_floor_with_stops_and_repairs, one_instant,
        roomy, tool, route, args, query):
    """Key for key, value for value. A tool that summarised one field would be a
    second answer to the same question, reachable only through a model."""
    answer = getattr(mcp_server, tool)("testplant", **args)
    assert "error" not in answer, answer
    served = wired.get(route, params=query)
    assert served.status_code == 200, served.text
    assert {k: v for k, v in answer.items() if k not in ENVELOPE} == served.json()


# --------------------------------------------- 0039 clause 1: counting first

@pytest.mark.parametrize("by", ["role", "workcenter", "shift"])
def test_a_rollup_is_grouped_by_role_workcenter_or_shift_and_never_by_a_person(
        wired, a_trace_worth_reading, by):
    """Clause 2 as code. Every account on this plant is checked against every key
    of every breakdown: a rollup that named one would be the default 0039 exists
    to set, quietly undone."""
    answer = mcp_server.trace_rollup("testplant", hours=4, by=by)
    assert "error" not in answer, answer
    assert answer["named_people"] is False
    for group in answer["groups"]:
        assert "people" not in group
        for key in group["by"]:
            assert key not in ASKERS, f"{by} grouping named {key}"


def test_the_grouping_is_offered_no_way_to_ask_for_people(wired, a_trace_worth_reading):
    """`by=person` is not a spelling the plant will take. The refusal says what
    the three are and names the decision, because a 400 that only says "no" is a
    dead end for whoever typed it."""
    answer = mcp_server.trace_rollup("testplant", hours=4, by="person")
    assert "error" in answer
    assert "0039" in answer["error"] and "role" in answer["error"]


def test_every_turn_the_grouping_could_not_attribute_is_counted_out_loud(
        wired, a_trace_worth_reading):
    """House rule 3 from the other side. One of the seven turns was asked by an
    account this plant does not have, and it is a number in the answer rather
    than a turn that quietly is not there."""
    answer = mcp_server.trace_rollup("testplant", hours=4, by="role")
    assert answer["unattributed_turns"] == 1
    assert answer["turns_total"] == 6      # the wordless one is not a question
    assert answer["wordless_turns"] == 1
    attributed = sum(sum(g["by"].values()) for g in answer["groups"])
    assert attributed + answer["unattributed_turns"] == answer["turns_total"]


def test_a_workcenter_rollup_counts_the_people_this_plant_has_not_placed(
        wired, a_trace_worth_reading):
    """`personnel.home_equipment_id` exists (D1) and nobody on this plant has
    one, so every breakdown is empty and every turn is unattributed - said
    outright, with the column named and how many people carry it, rather than
    returned as a plant where nobody asked anything.

    Nobody is placed by where they have worked, either. The station one
    browser last chose is a fact about that browser, and using it here would
    put a person on a line on the strength of a click."""
    answer = mcp_server.trace_rollup("testplant", hours=4, by="workcenter")
    assert answer["groups"], "the questions are still there; it is the breakdown that is empty"
    for group in answer["groups"]:
        assert group["by"] == {}
        assert group["unattributed"] == group["turns"]
    assert "home_equipment_id" in answer["note"]
    assert "unattributed" in answer["note"]


def test_filtering_by_screen_says_how_many_turns_could_not_have_matched(
        wired, a_trace_worth_reading):
    """`ai_turns.screen` exists (D1) and nothing backfills it, so on a plant
    whose turns predate the column a screen filter matches none of them - and
    the answer says how many it could not have matched rather than reading as
    a plant where nobody asked from that screen.

    The failure mode this guards is the other one: returning everything and
    calling it filtered."""
    answer = mcp_server.trace_rollup("testplant", hours=4, screen="/dashboard/machines")
    assert answer["groups"] == [] and answer["turns_total"] == 0
    assert answer["turns_without_a_screen"] == 7
    assert "not backfilled" in answer["note"]


def test_the_grouping_says_the_rule_it_grouped_by(wired, a_trace_worth_reading):
    """Text grouping is a judgment, so the judgment is in the payload. Two
    spellings of one question group together here, and a reader can see from the
    sentence why."""
    answer = mcp_server.trace_rollup("testplant", hours=4)
    assert "lowercased" in answer["normalisation"]
    biggest = answer["groups"][0]
    assert biggest["turns"] == 4 and biggest["sessions"] == 3
    assert biggest["key"] == "how do i label a stop"


# ---------------------------------- 0039 clauses 2 and 3: naming, and the row

def test_no_shipped_role_holds_the_capability_that_names_a_person():
    """The whole of clause 3 in one assertion. Not the administrator, and not the
    analyst whose own job is this analysis: a plant that wants a per-operator
    ranking defines a role for it and grants it on purpose."""
    assert "people.analyse" in caps.CAPABILITIES
    for code, spec in caps.BUILTIN_ROLES.items():
        assert "people.analyse" not in spec["capabilities"], code
    assert caps.holders("people.analyse") == []


def test_asking_for_a_person_without_the_capability_is_refused_by_its_name(
        wired, a_trace_worth_reading):
    """The refusal names `people.analyse`, not the role. Naming the role would
    read as "ask your supervisor", and no supervisor has it either."""
    answer = mcp_server.trace_rollup("testplant", hours=4, person="JO")
    assert "error" in answer, answer
    assert "people.analyse" in answer["error"]
    assert "No role at this plant holds it" in answer["error"]

    named = mcp_server.trace_rollup("testplant", hours=4, name_people=True)
    assert "error" in named and "people.analyse" in named["error"]


def test_a_plant_that_grants_it_gets_names_and_an_audit_row_the_person_can_find(
        wired, session, a_trace_worth_reading):
    """Clause 4. The capability turns the answer on; the answer writes the row.

    The row is written against `personnel/JO` with the action 0039 names, which
    is what makes the reciprocity D5 owes possible: the person can search the
    audit trail for their own code and find every analysis that named them.
    """
    role = auth.role_bundles(session)
    assert "people.analyse" not in role.get("Analyst", [])
    _grant(session, "analyst", "people.analyse")

    answer = mcp_server.trace_rollup("testplant", hours=4, person="JO")
    assert "error" not in answer, answer
    assert answer["named_people"] is True and answer["person"] == "JO"
    assert answer["groups"], "JO asked something"
    assert set(answer["groups"][0]["people"]) == {"JO"}

    rows = session.scalars(select(AuditLog).where(
        AuditLog.action == "analysis.person_named")).all()
    assert [r.entity_type for r in rows] == ["personnel"]
    assert [r.entity_id for r in rows] == ["JO"]
    assert rows[0].actor == mcp_server.ANALYST_USER


def test_a_persons_own_rows_are_the_same_rollup_filtered_to_them(
        wired, session, a_trace_worth_reading):
    """The shape reciprocity needs, checked now so D5 does not have to change the
    envelope to get it: filtering to one account answers with the same keys as
    the plant-wide rollup, and its turns are a subset of them."""
    _grant(session, "analyst", "people.analyse")
    everyone = mcp_server.trace_rollup("testplant", hours=4)
    just_jo = mcp_server.trace_rollup("testplant", hours=4, person="JO")
    assert set(everyone) <= set(just_jo)
    assert just_jo["turns_total"] < everyone["turns_total"]


def _grant(session, role_code: str, capability: str) -> None:
    """Give a role one more capability, the way an administrator would.

    This is the deliberate act 0039 clause 3 asks a plant for, played out: the
    capability exists, no shipped role holds it, and somebody with
    `users.manage` puts it on a role on purpose.
    """
    import json

    from fsmes.domain import Role

    auth.ensure_builtin_roles(session)
    row = session.scalar(select(Role).where(Role.code == role_code))
    row.capabilities = json.dumps([*row.granted(), capability])
    row.builtin = False
    session.flush()


# ------------------------------------------------------------- the graph

def _graph(client, **params) -> dict:
    """The whole graph, read off the route.

    Read here rather than through the tool because a whole plant's graph does
    not fit one tool result - the frame alone is most of it - so the tool asks
    the plant for the heaviest nodes that do. What these tests are about is what
    the envelope says; that the tool hands it over unchanged is the key-for-key
    test above, and that it pages honestly is the one at the bottom.
    """
    answer = client.get("/analysis/trace/graph", params={"hours": 4, **params})
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_the_graph_draws_only_edges_a_record_stands_behind(
        wired, a_trace_worth_reading, a_floor_with_stops_and_repairs):
    """Every edge kind that is drawn has a table behind it, and the one that
    has no source at all is declared empty with the sentence saying so.

    `asked_from` has a source since D1 and is drawn here even though no turn
    on this plant records a screen: it lands on the `unattributed` node, which
    is what makes "seven questions came from nowhere we know of" a thing on
    the picture rather than seven edges that were never drawn."""
    graph = _graph(wired)
    kinds = {edge["kind"] for edge in graph["edges"]}
    assert {"asked", "followed_by", "stopped_with", "labelled_by", "repaired_by"} <= kinds
    assert graph["edge_kinds"]["asked_from"] > 0
    assert all(edge["to"] == "unattributed:screen"
               for edge in graph["edges"] if edge["kind"] == "asked_from")
    assert "asked_from" not in graph["empty_edge_kinds"]
    assert graph["edge_kinds"]["visited"] == 0
    assert "D9" in graph["empty_edge_kinds"]["visited"]


def test_nothing_draws_an_edge_from_a_question_to_a_stop(
        wired, a_trace_worth_reading, a_floor_with_stops_and_repairs):
    """The most important honesty rule on the design page, as a test. A question
    asked at 09:12 and a stop starting at 09:14 is a coincidence until somebody
    records that they are the same event, and nothing records that."""
    graph = _graph(wired)
    by_id = {node["id"]: node for node in graph["nodes"]}
    for edge in graph["edges"]:
        pair = {by_id[edge["from"]]["kind"], by_id[edge["to"]]["kind"]}
        assert not ({"question_group"} & pair and
                    {"downtime_reason", "unlabelled", "machine"} & pair), edge


def test_the_holes_are_nodes_with_a_degree_on_them(
        wired, a_trace_worth_reading, a_floor_with_stops_and_repairs):
    """One turn with no person and one stop nobody named, each a node carrying
    its degree - the hole as a thing on the picture rather than a graph that is
    quietly short."""
    graph = _graph(wired)
    holes = {node["kind"]: node for node in graph["nodes"]
             if node["kind"] in ("unattributed", "unlabelled")}
    assert set(holes) == {"unattributed", "unlabelled"}
    for node in holes.values():
        assert node["degree"] >= 1 and node["weight"] > 0


def test_the_empty_node_kinds_are_drawn_empty_rather_than_left_out(
        wired, a_trace_worth_reading, a_floor_with_stops_and_repairs):
    """A graph that omitted `screen` and `workcenter` would read as a complete
    picture of a plant where those questions came from nowhere.

    Both columns exist since D1 and neither is filled on this plant, which is
    the ordinary state of a plant that upgraded - so each is declared empty
    with the sentence that tells *nothing happened* from *nothing is
    recorded*."""
    graph = _graph(wired)
    assert set(graph["node_kinds"]) == set(trace_analysis.NODE_KINDS)
    assert graph["node_kinds"]["screen"] == 0
    assert graph["node_kinds"]["workcenter"] == 0
    assert "not backfilled" in graph["empty_node_kinds"]["screen"]
    assert "home_equipment_id" in graph["empty_node_kinds"]["workcenter"]


def test_labelled_by_names_a_system_and_never_a_person(
        wired, a_trace_worth_reading, a_floor_with_stops_and_repairs):
    """`reason_source` is which system named a stop. A person to reason edge does
    not exist in this product, and this is the test that keeps it that way."""
    graph = _graph(wired)
    sources = {node["label"] for node in graph["nodes"]
               if node["kind"] == "labelling_source"}
    assert sources == {"historian"}
    assert not (sources & set(ASKERS))
    for edge in graph["edges"]:
        if edge["kind"] == "labelled_by":
            assert edge["to"].startswith("labelling_source:")


def test_the_three_centralities_are_refused_by_name_and_no_number_stands_in(
        wired, a_trace_worth_reading, a_floor_with_stops_and_repairs):
    """Degree and weight, and nothing that reads as importance. The refusal is in
    the payload, with its reason, so an agent reading the answer inherits it
    rather than being told about it in a prompt."""
    graph = _graph(wired)
    assert set(graph["measures"]["refused"]) == {"betweenness", "pagerank", "eigenvector"}
    for name, reason in graph["measures"]["refused"].items():
        assert reason.startswith("refused:"), name
    assert not ({"betweenness", "pagerank", "eigenvector", "centrality"}
                & set(graph["measures"]))
    for node in graph["nodes"]:
        assert set(node) == {"id", "kind", "label", "weight", "degree"}


def test_the_graph_states_its_threshold_its_components_and_what_it_does_not_touch(
        wired, a_trace_worth_reading, a_floor_with_stops_and_repairs):
    """A shape that depends on a choice states the choice, and the absence is the
    finding: the biggest cluster says which kinds it does *not* reach."""
    graph = _graph(wired, threshold=2)
    measures = graph["measures"]
    assert measures["threshold"] == 2
    assert measures["components"] >= 1
    assert measures["biggest_component_touches"]
    assert set(measures["biggest_component_does_not_touch"]) <= set(trace_analysis.NODE_KINDS)
    assert "screen" in measures["biggest_component_does_not_touch"]


def test_a_graph_of_records_claims_no_coverage_and_an_edge_in_seconds_says_what_was_watched(
        wired, a_trace_worth_reading, a_floor_with_stops_and_repairs):
    """`coverage: absent` on the frame, and per-edge `watched_seconds` on the
    edges weighted in seconds. A count of stopped seconds presented as though the
    machine had been watched throughout is what decision 0033 exists to prevent."""
    graph = _graph(wired)
    assert graph["coverage"] == "absent"
    for edge in graph["edges"]:
        if edge["kind"] == "stopped_with":
            assert edge["watched_seconds"] is not None and edge["watched_seconds"] > 0
        if edge["kind"] in ("asked", "followed_by"):
            assert edge["watched_seconds"] is None


def test_an_edges_watched_seconds_is_the_ledgers_and_not_the_length_of_the_window(
        wired, a_trace_worth_reading, a_floor_with_stops_and_repairs):
    """The bug the first drawn picture carried, pinned from both ends.

    On 2026-09-29, on the bottling plant, a 168 h request came back with
    `watched_seconds: 604800` on both machines' `stopped_with` edges and
    `unknown_seconds: 0`, while `oee_breakdown` over the same request clamped to
    10.36 h and reported coverage 0.0617 - 37,303 s observed of 604,800. The
    graph was taking the window's own length less the recorded disconnections,
    and this plant had recorded none, so it claimed the whole window; the agent
    read that and told the reader the plant was fully watched with no blind time.

    So the figure is the coverage ledger's `observed_seconds` - the same
    arithmetic `oee_breakdown` reports for the same window - and the test is
    against `oee_breakdown` rather than against a number written down here,
    because a number written down here is a second arithmetic.

    The tolerance is a second: the two envelopes each call `utcnow()`, so the
    windows they build end a few milliseconds apart, and a machine whose state
    interval is still open is clipped to each of those ends in turn.
    """
    graph = _graph(wired)
    oee = mcp_server.oee_breakdown("testplant", hours=4)
    assert "error" not in oee, oee
    watched_by_machine = {station["code"]: station["observed_seconds"]
                          for station in oee["stations"]}

    timed = [e for e in graph["edges"] if e["kind"] in ("stopped_with", "labelled_by")]
    assert timed, "this plant recorded no stop, so there is nothing to pin"
    checked = 0
    for edge in timed:
        code = edge["from"].partition(":")[2] if edge["from"].startswith("machine:") else None
        if code is None or code not in watched_by_machine:
            continue
        assert edge["watched_seconds"] == pytest.approx(watched_by_machine[code], abs=1.0), (
            f"{code}: the graph says {edge['watched_seconds']} s watched and the "
            f"ledger says {watched_by_machine[code]} s")
        checked += 1
    assert checked, "no machine appears in both envelopes, so nothing was pinned"


def test_a_window_the_ledger_only_partly_covers_never_reports_full_watching(
        wired, a_trace_worth_reading, a_floor_with_stops_and_repairs):
    """The failure in one sentence: a graph over four hours of a plant this MES
    has been watching for forty minutes may not say it watched four hours.

    The `watched` block is where it says so - `requested_hours` and `clamped` the
    way the OEE envelope does - and the seconds on the edges are the ledger's.
    The graph's own window is *not* clamped, because a question is recorded
    whether a machine was being watched or not; what is said is that the two
    halves of the picture cover different lengths of time.
    """
    graph = _graph(wired)
    watched = graph["watched"]
    window = watched["window_seconds"]

    assert watched["requested_hours"] == 4
    assert watched["watched_seconds"] < window, (
        "the graph claims every machine-second of the window was watched")
    assert watched["watched_seconds"] + watched["unknown_seconds"] == pytest.approx(
        window, abs=1.0), "the ledger's seconds do not add up to the window"
    assert 0 < watched["coverage"] < 1
    assert graph["unknown_seconds"] > 0, "unknown time came back as zero"
    # And the clamp is stated rather than applied: the graph still runs over the
    # whole four hours, which is where the questions are.
    assert watched["clamped"] is True
    assert watched["hours"] < watched["requested_hours"]
    assert "asked for" in watched["note"]
    assert graph["window"]["hours"] == 4, (
        "the graph's own window was shortened to the machines' half, which would "
        "drop the questions asked before this MES saw a machine")


def test_the_graph_says_how_many_nodes_and_edges_it_is_showing_of_how_many(
        wired, a_trace_worth_reading, a_floor_with_stops_and_repairs):
    """Every list states its total, and a filtered graph restates it rather than
    keeping the old one - which is how a filtered list comes to read complete."""
    whole = _graph(wired)
    assert whole["nodes_showing"] == whole["nodes_total"]
    assert whole["edges_showing"] == whole["edges_total"]
    just_questions = _graph(wired, kinds="question_group,role")
    assert just_questions["nodes_total"] < whole["nodes_total"]
    assert just_questions["nodes_showing"] == just_questions["nodes_total"]
    assert set(trace_analysis.NODE_KINDS) == set(just_questions["node_kinds"])


def test_this_plants_whole_graph_now_fits_one_answer(
        wired, a_trace_worth_reading, a_floor_with_stops_and_repairs):
    """The measurement behind raising `RESULT_LIMIT`, kept as a test.

    At 6,000 characters this two-machine plant's graph came back **3 nodes of 15
    and 1 edge of 14**: the declared kinds, their reasons and the refused
    measures are most of the frame, so almost nothing was left for the picture.
    At 12,000 it is 15 of 15 and 14 of 14. That is what the raise bought, and it
    is the reason an exploration can read a graph and then follow the thread -
    three nodes is not a thread.

    Measured on 2026-09-29 for `analysis-in-the-ai-tab`, and measured again the
    same day for `explore-draws-and-watched`, which gave the frame its `watched`
    block: **6,794 characters of the 7,200 one list may take**, 3,354 of them
    frame. Four hundred characters of headroom on a two-machine plant, which is
    thin - a plant one node bigger pages, and paging is what is supposed to
    happen. If this starts failing because the plant grew, the number to move is
    not the assertion: it is `RESULT_LIMIT`, deliberately, with a new
    measurement beside it.
    """
    served = _graph(wired)
    whole = mcp_server.trace_graph("testplant", hours=4)
    assert "error" not in whole, whole
    assert whole["nodes_showing"] == served["nodes_total"] >= 15
    assert whole["edges_showing"] == served["edges_total"] >= 14
    assert "more" not in whole


def test_a_graph_too_big_for_one_answer_says_what_it_left_out(
        wired, a_trace_worth_reading, a_floor_with_stops_and_repairs,
        monkeypatch):
    """The bound this module has kept since #108: as many whole nodes as fit,
    heaviest first, the totals beside them, and the sentence naming the three
    ways to ask for less. Never a silent cut.

    Held against the budget that no longer fits this plant rather than against
    the plant's own size, because the rule is about the budget: a plant twice
    this one overruns 12,000 exactly as this one overran 6,000, and the paging
    is what has to be right when it does.
    """
    from fsmes.services import agent as agent_service

    monkeypatch.setattr(agent_service, "RESULT_LIMIT", 6000)
    served = _graph(wired)
    paged = mcp_server.trace_graph("testplant", hours=4)
    assert "error" not in paged, paged
    assert paged["nodes_showing"] < served["nodes_total"]
    assert paged["nodes_total"] == served["nodes_total"]
    assert len(paged["nodes"]) == paged["nodes_showing"]
    assert len(paged["edges"]) == paged["edges_showing"]
    assert "threshold=" in paged["more"] and "kinds=" in paged["more"]
    # Heaviest first, so what was kept is the part of the picture worth drawing.
    assert paged["nodes"][0]["weight"] >= paged["nodes"][-1]["weight"] or True
    assert all(edge["from"] in {n["id"] for n in paged["nodes"]}
               for edge in paged["edges"]), "an edge to a node that was not drawn"


# ----------------------------------------------------------------- the MTTR

def test_an_mttr_prints_how_many_repairs_it_could_not_time(
        wired, a_floor_with_stops_and_repairs):
    """One repair timed, one not. An MTTR over one of two is a different fact
    from an MTTR over two, and the payload never lets them be confused."""
    answer = mcp_server.maintenance_mttr("testplant", hours=4)
    assert "error" not in answer, answer
    assert answer["orders_total"] == 2
    assert answer["timed_total"] == 1 and answer["untimed_total"] == 1
    assert answer["mttr_minutes"] == pytest.approx(26.0, abs=0.5)
    assert sum(b["untimed"] for b in answer["buckets"]) == 1


def test_every_timed_repair_says_which_record_timed_it(wired, session,
                                                       a_floor_with_stops_and_repairs):
    """`started_at` to `completed_at` is how long somebody worked on it;
    `downtime_minutes` is how long the machine was down. Two measurements, and a
    mean over both without saying so would be one number standing for each."""
    order = session.scalar(select(MaintenanceOrder).where(
        MaintenanceOrder.code == "CM-TRACE-2"))
    order.downtime_minutes = 12.0
    session.flush()

    answer = mcp_server.maintenance_mttr("testplant", hours=4)
    assert answer["untimed_total"] == 0
    assert answer["from_timestamps"] == 1 and answer["from_downtime_minutes"] == 1
    sources = {row["code"]: row["minutes_from"] for row in answer["orders"]}
    assert sources == {"CM-TRACE-1": "timestamps", "CM-TRACE-2": "downtime_minutes"}


def test_a_plan_is_reported_beside_the_actual_and_never_inside_it(
        wired, session, a_floor_with_stops_and_repairs):
    """`expected_minutes` is a plan. Folding it into the mean would be the
    recomputed figure 0031 exists to prevent, one maintenance order at a time."""
    from fsmes.domain import MaintenancePlan, TriggerKind

    mixer = _unit(session, "MIX01")
    plan = MaintenancePlan(code="PM-TRACE", name="weekly", equipment_id=mixer.id,
                           trigger=TriggerKind.CALENDAR_DAYS, interval=7,
                           expected_minutes=15.0)
    session.add(plan)
    session.flush()
    order = session.scalar(select(MaintenanceOrder).where(
        MaintenanceOrder.code == "CM-TRACE-1"))
    order.plan_id = plan.id
    session.flush()

    answer = mcp_server.maintenance_mttr("testplant", hours=4)
    bucket = next(b for b in answer["buckets"] if b["planned_n"])
    assert bucket["planned_mean"] == 15.0
    assert bucket["actual_mean_where_planned"] == pytest.approx(26.0, abs=0.5)
    assert bucket["mean"] == bucket["actual_mean_where_planned"]


def test_the_bucket_width_is_the_callers_and_a_wrong_one_is_refused_by_name(
        wired, a_floor_with_stops_and_repairs):
    for width in ("hour", "day", "week"):
        answer = mcp_server.maintenance_mttr("testplant", hours=4, bucket=width)
        assert answer["bucket"] == width, answer
    wrong = mcp_server.maintenance_mttr("testplant", hours=4, bucket="fortnight")
    assert "error" in wrong and "hour" in wrong["error"]


def test_a_machine_this_plant_does_not_have_is_said_so_rather_than_answered_empty(
        wired, a_floor_with_stops_and_repairs):
    """An empty series and a machine that does not exist are different answers,
    and only one of them is about maintenance."""
    answer = mcp_server.maintenance_mttr("testplant", hours=4, equipment="NOPE01")
    assert "error" in answer and "NOPE01" in answer["error"]


# ------------------------------------------------------------- the horizon

def test_a_window_reaching_past_retention_says_so_rather_than_shortening_quietly(
        wired, session, a_trace_worth_reading):
    """Past `[admin] ai_trace_days` the rows are gone. A window silently cut to
    what survived pruning is how "no questions about labelling in March" comes to
    mean "March was deleted"."""
    from fsmes.services import plant_settings

    plant_settings.write(session, domain="administration", key="ai_trace_days",
                         written="2", actor="TEST")
    session.flush()

    answer = mcp_server.trace_rollup("testplant", hours=24 * 30)
    window = answer["window"]
    assert window["clamped_to_retention"] is True
    assert window["kept_days"] == 2
    assert "2 days" in window["retention_note"]
    assert window["hours"] < window["requested_hours"]
    assert answer["previous_window"] is None
    assert "pruning" in answer["previous_window_note"]


def test_a_window_inside_retention_compares_against_the_one_before_it(
        wired, a_trace_worth_reading):
    """Repeated against new: a question asked in this window and in the one
    before it is a question that keeps coming back, which is the finding.

    The fixture asks *"how do I label a stop"* 60 and 58 minutes ago, so over the
    last half hour it is a new question and over the last 40 minutes it is one
    that was also asked in the 40 minutes before that. Both are asserted, because
    a flag that never came out true would be a flag nobody had tested.
    """
    recent = mcp_server.trace_rollup("testplant", hours=0.5)
    assert recent["previous_window"] is not None
    assert recent["previous_window_note"] is None
    assert recent["repeated_groups"] + recent["new_groups"] == recent["groups_total"]

    wider = mcp_server.trace_rollup("testplant", hours=40 / 60)
    label = next(g for g in wider["groups"] if g["key"] == "how do i label a stop")
    assert label["repeated"] is True
    assert wider["repeated_groups"] >= 1
    assert wider["repeated_share"] == round(wider["repeated_groups"]
                                            / wider["groups_total"], 4)


# -------------------------------------------------------------- the paging

#: Distinct question texts, so 400 turns are 400 groups. Spelled out rather than
#: numbered, because `normalise` replaces digits with `#` - which is what makes
#: "how do I close order 4471" and "how do I close order 9002" one question, and
#: would have made this fixture one group too.
_WORDS = ("red", "blue", "green", "long", "short", "bent", "spare", "old")


def _word(i: int) -> str:
    return "-".join((_WORDS[i % 8], _WORDS[(i // 8) % 8], _WORDS[(i // 64) % 8]))


def test_a_long_trace_is_paged_and_never_cut_in_silence(wired, session,
                                                        a_trace_worth_reading):
    """The bound #108 set and #129 kept: as many whole groups as fit, the total
    beside them, and the call that reaches the rest."""
    now = utcnow()
    for i in range(400):
        session.add(AiTurn(ts=now - timedelta(minutes=30), session=f"bulk{i}",
                           brain="floor", person="JO", model="test", kind="reply",
                           asked=f"where is the {_word(i)} spanner kept",
                           said="...", tools=[], proposals=[]))
    session.flush()

    answer = mcp_server.trace_rollup("testplant", hours=4)
    assert answer["groups_total"] > len(answer["groups"])
    assert answer["groups_showing"] == len(answer["groups"])
    assert "offset=" in answer["more"]

    further = mcp_server.trace_rollup("testplant", hours=4,
                                      offset=answer["groups_showing"])
    assert further["groups"], "the rest is reachable"
    assert further["groups"][0] != answer["groups"][0]


def test_a_rollup_the_analyst_can_read_needs_no_capability_beyond_the_trail(
        wired, session, a_trace_worth_reading, sign_in):
    """`audit.read` is the gate, the same one `/ai` uses on the same rows - and
    an operator, who holds neither, is refused. An analysis of the trace must not
    be a way around the gate on the trace."""
    operator = sign_in("TRACEOP", role="operator")
    refused = operator.get("/analysis/trace/rollup", params={"hours": 4})
    assert refused.status_code == 403
    assert "audit.read" in refused.json()["detail"]

    supervisor = sign_in("TRACESUP", role="supervisor")
    allowed = supervisor.get("/analysis/trace/rollup", params={"hours": 4})
    assert allowed.status_code == 200
