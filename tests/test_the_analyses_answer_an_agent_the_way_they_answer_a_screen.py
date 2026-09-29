"""The four analyses, read through the agent's tools instead of the screen's.

The promise these pin is one sentence: **what the tool hands an agent is what
the route hands a screen.** Not a summary of it, not a tidied version of it -
the same object, key for key, including the parts a model might prefer were
simpler: `coverage` and the `coverage_note` that withholds a figure, the whole
ledger behind it, `unknown_seconds` that is neither downtime nor zero,
`labelled_by` that says who named a stop, and every total.

That is the point of doing it this way. An agent asked "what was OEE this
shift" either reads the number this MES committed to or computes one of its
own, and a second arithmetic reachable only through a model is how the screens
and the assistant come to disagree - which decisions 0031 and 0033 exist to
prevent. A test that compared "roughly the same figures" would let that happen
one field at a time, so these compare the whole payload.

The rest is the part a pass-through still has to get right: a window nobody
named is the plant's own and not eight hours, and an answer too big for one
tool result bounds itself and says so rather than being cut by the loop.
"""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from fsmes import mcp_server
from fsmes.db import utcnow
from fsmes.domain import (
    Equipment,
    EquipmentState,
    EquipmentStateName,
    TagValue,
)
from fsmes.services import auth, plant_settings


@pytest.fixture()
def wired(make_client, session, monkeypatch):
    """The MCP tools pointed at the in-process app, signed in as AGENT.

    The same arrangement `test_mcp_product.py` uses: the tools only ever reach a
    plant through its HTTP API as an account, and that is not bypassed here, so
    every call below goes through the real role gate.
    """
    auth.create_user(session, code=mcp_server.AGENT_USER, name="Plant Agent",
                     password=mcp_server.AGENT_PASSWORD, role="agent")
    session.flush()
    # A client per account the tools can sign in as, each with its own cookies.
    # The one handed back is AGENT's - the account these tool calls are made as,
    # so the route this file reads beside them is read as the same person.
    clients = mcp_server.wire_clients("testplant", make_client)
    monkeypatch.setattr(mcp_server, "_clients", clients)
    client = clients[mcp_server.client_key("testplant", mcp_server.AGENT_USER)]
    monkeypatch.setattr(mcp_server, "_registry",
                        lambda: {"testplant": {"api_port": 0, "label": "Test"}})
    return client


def _unit(session, code: str) -> Equipment:
    return session.scalar(select(Equipment).where(Equipment.code == code))


def _state(session, code, state, *, minutes_ago, minutes, reason=None, source=None):
    session.add(EquipmentState(
        equipment_id=_unit(session, code).id, state=state, reason=reason,
        reason_source=source,
        started_at=utcnow() - timedelta(minutes=minutes_ago),
        ended_at=utcnow() - timedelta(minutes=minutes_ago - minutes)))
    session.flush()


def _samples(session, code, tag, *, count, minutes):
    """`count` readings rising over the last `minutes`, every one of them
    strictly inside that window.

    Strictly inside on purpose: a reading placed exactly `minutes` ago falls out
    of a window opened a few milliseconds later, and a test that then counted
    readings would be counting the time it took to make the request.
    """
    unit = _unit(session, code)
    now = utcnow()
    for i in range(count):
        session.add(TagValue(
            equipment_id=unit.id, tag=f"{code}.{tag}", value_num=50.0 + i * 0.1,
            ts=now - timedelta(minutes=minutes * (1 - (i + 1) / (count + 1)))))
    session.flush()


@pytest.fixture()
def a_shift_worth_of_history(session):
    """A line that ran, stopped twice for named reasons, and published a tag.

    One stop is labelled by this MES and one by a supplier, because
    `labelled_by` separating the two is one of the fields an agent has to
    inherit rather than be told about.
    """
    _state(session, "MIX01", EquipmentStateName.RUNNING, minutes_ago=90, minutes=60)
    _state(session, "MIX01", EquipmentStateName.DOWN, minutes_ago=30, minutes=12,
           reason="infeed jam")
    _state(session, "PACK01", EquipmentStateName.RUNNING, minutes_ago=90, minutes=80)
    _state(session, "PACK01", EquipmentStateName.DOWN, minutes_ago=10, minutes=5,
           reason="film splice", source="historian")
    _samples(session, "MIX01", "Temperature", count=40, minutes=90)


# ------------------------------------------------- the payload, key for key

#: Each tool, the route it stands in front of, and the arguments to ask both
#: with. The tool's own envelope keys are compared out: `plant` is which plant
#: was asked, which a route serving one plant has no reason to say.
CASES = (
    ("oee_breakdown", "/analysis/oee",
     {"line": "LINE1", "hours": 2}, {"line": "LINE1", "hours": 2}),
    ("state_timeline", "/analysis/timeline",
     {"line": "LINE1", "hours": 2}, {"line": "LINE1", "hours": 2}),
    ("downtime_pareto", "/analysis/downtime",
     {"line": "LINE1", "hours": 2}, {"line": "LINE1", "hours": 2}),
    ("tag_trend", "/analysis/tag/MIX01",
     {"machine": "MIX01", "hours": 2, "buckets": 20}, {"hours": 2, "buckets": 20}),
)

ENVELOPE = {"plant"}


@pytest.fixture()
def one_instant(monkeypatch):
    """The clock pinned for the length of a comparison.

    Every one of these windows ends *now*, so the same question asked twice
    twenty milliseconds apart answers with two different `window.end` - which
    would make a key-for-key comparison a measurement of the pause between two
    requests rather than of the payload. Pinned here and nowhere else: the
    clamping to when this MES started watching is what the other tests are
    about, and that has to keep moving.
    """
    from fsmes.services import analysis as service

    instant = utcnow()
    monkeypatch.setattr(service, "utcnow", lambda: instant)
    return instant


@pytest.mark.parametrize(("tool", "route", "args", "query"),
                         CASES, ids=[c[0] for c in CASES])
def test_the_tool_hands_over_exactly_what_the_route_hands_the_screen(
        wired, a_shift_worth_of_history, one_instant, tool, route, args, query):
    """Key for key, value for value, on a plant with a shift of history behind
    it. A tool that reshaped one field would be a second answer to the same
    question."""
    answer = getattr(mcp_server, tool)("testplant", **args)
    assert "error" not in answer, answer
    served = wired.get(route, params=query)
    assert served.status_code == 200, served.text
    assert {k: v for k, v in answer.items() if k not in ENVELOPE} == served.json()


def test_the_coverage_that_governs_a_figure_arrives_with_it(wired, a_shift_worth_of_history):
    """Decisions 0030, 0031 and 0033 are in the payload, not in a sentence
    somebody has to remember to write into a prompt: availability is a share of
    what was watched, the ledger says what that was, and unknown seconds are
    named rather than priced as downtime."""
    oee = mcp_server.oee_breakdown("testplant", line="LINE1", hours=2)
    mixer = next(s for s in oee["stations"] if s["code"] == "MIX01")
    for field in ("coverage", "coverage_floor", "coverage_note", "ledger",
                  "unknown_seconds", "observed_seconds", "performance_note",
                  "performance_ratio", "counts_outrun_run_time"):
        assert field in mixer, field
    assert mixer["ledger"], "the account behind the coverage is the evidence"
    for field in ("coverage", "unknown_seconds", "machines_total", "stations_rated",
                  "not_observed_seconds", "stations_withheld"):
        assert field in oee, field
    assert oee["window"]["requested_hours"] == 2


#: What each payload says about coverage, read off the product on 2026-09-28
#: rather than promised for all four. They differ, and the tools' docstrings say
#: so per tool - this is what keeps those sentences true. A route that grows a
#: coverage figure later should fail here, be added to the list, and have its
#: tool's docstring corrected in the same change.
COVERAGE = {
    # The one whose numbers are rates, and rates are what coverage governs.
    "oee_breakdown": {"top": {"coverage", "coverage_floor", "unknown_seconds",
                              "observed_seconds"},
                      "each": {"coverage", "coverage_floor", "coverage_note",
                               "ledger", "unknown_seconds", "unknown_share",
                               "observed_seconds"}},
    # How blind the window was, and no ratio, no ledger.
    "downtime_pareto": {"top": {"unknown_seconds", "unknown_share"}, "each": set()},
    # What the MES recorded, and nothing about what share of the window it was
    # watching while recording it.
    "state_timeline": {"top": set(), "each": set()},
    "tag_trend": {"top": set(), "each": set()},
}

LISTS = {"oee_breakdown": "stations", "downtime_pareto": "reasons",
         "state_timeline": "machines", "tag_trend": "points"}

#: Every field any of the four uses to say how much of its window was watched.
COVERAGE_FIELDS = {"coverage", "coverage_floor", "coverage_note", "ledger",
                   "unknown_seconds", "unknown_share", "observed_seconds"}


@pytest.mark.parametrize("tool", sorted(COVERAGE), ids=sorted(COVERAGE))
def test_only_the_analyses_that_measure_coverage_report_one_and_none_is_invented(
        wired, a_shift_worth_of_history, tool):
    """The handoff asked for the payload the screens get, and the screens do not
    all get a coverage figure: OEE carries the ledger, the pareto carries how
    blind the window was, and the Gantt and the trend carry neither.

    Both halves are pinned. A tool that quietly supplied a figure its route does
    not serve would be inventing coverage, which is worse than not having it -
    and a route that starts serving one should be noticed here rather than
    silently going unsaid to an agent. PR #128's chart kit draws
    `data-coverage=absent` for the two with none, off the same payloads.
    """
    args = {"machine": "MIX01"} if tool == "tag_trend" else {"line": "LINE1"}
    answer = getattr(mcp_server, tool)("testplant", hours=2, **args)
    assert "error" not in answer, answer
    want = COVERAGE[tool]
    assert COVERAGE_FIELDS & set(answer) == want["top"]
    items = answer.get(LISTS[tool]) or []
    assert items, f"{tool} answered with an empty {LISTS[tool]} - nothing was measured"
    assert COVERAGE_FIELDS & set(items[0]) == want["each"]


def test_a_stop_says_who_named_it_and_an_unlabelled_one_says_it_is_unlabelled(
        wired, a_shift_worth_of_history):
    """House rule 3 reaches the agent as data. `labelled_by` separates this
    MES's own labels from another system's, and the totals account for every
    second in the window."""
    pareto = mcp_server.downtime_pareto("testplant", line="LINE1", hours=2)
    sources = {who for bucket in pareto["reasons"] for who in bucket["labelled_by"]}
    assert sources == {"here", "historian"}
    for field in ("total_seconds", "unlabelled_share", "from_the_list_seconds",
                  "typed_seconds", "vocabulary_total", "unknown_seconds",
                  "unknown_share", "machines_total"):
        assert field in pareto, field
    accounted = pareto["typed_seconds"] + pareto["from_the_list_seconds"]
    assert accounted == pytest.approx(pareto["total_seconds"], abs=0.2)


def test_a_gantt_says_how_many_machines_the_line_has_and_not_only_what_it_drew(
        wired, a_shift_worth_of_history):
    gantt = mcp_server.state_timeline("testplant", line="LINE1", hours=2)
    assert gantt["machines_shown"] == len(gantt["machines"]) == 2
    assert gantt["machines_total"] == 2
    assert sorted(gantt["machines_available"]) == ["MIX01", "PACK01"]


# ---------------------------------------------------------------- the window

@pytest.mark.parametrize("tool", ["oee_breakdown", "state_timeline", "downtime_pareto"])
def test_a_window_nobody_named_is_this_plants_own_and_not_eight_hours(
        wired, session, a_shift_worth_of_history, tool):
    """The tools these replace declared `hours=8.0` and `hours=1.0`. The routes
    declare no default on purpose: left out, the window is `[process]
    default_report_hours` as the plant has it at the moment of the request, so a
    twelve-hour plant is asked about a twelve-hour shift. A tool with a number
    in its own signature would have quietly overruled that."""
    plant_settings.write(session, domain="engineering", key="default_report_hours",
                         written="11", actor="ADMIN")
    session.flush()
    answer = getattr(mcp_server, tool)("testplant", line="LINE1")
    assert answer["window"]["requested_hours"] == pytest.approx(11.0, abs=0.01)


def test_a_named_shift_is_asked_for_instead_of_a_span_and_not_beside_it(
        wired, a_shift_worth_of_history):
    """A request that names a shift gets that shift. Sending `hours` as well
    would describe a request the route ignores, so it is left out; a shift this
    plant has no pattern for is one sentence of refusal, never a quiet fall-back
    to eight hours."""
    answer = mcp_server.downtime_pareto("testplant", line="LINE1", hours=2, shift="current")
    assert "error" in answer and "shift" in answer["error"].lower()


# --------------------------------------------------------------- no writing

def test_no_tool_the_analysis_module_registers_can_change_anything():
    """A write tool is one that takes `dry_run` - the definition
    `services/agent.py` uses to decide what needs a person's confirmation, and
    the one `assist_coverage.py` derives independently. The analysis module is
    asserted against it rather than read for it: an analysis agent holds every
    read tool and no write tool, and this is the check that keeps that true of
    every tool the module registers.

    Written out rather than counted, so a tool joining this module is a line in
    this test - which is where somebody notices that the thing they added takes
    a `dry_run` after all."""
    import asyncio

    from fsmes.mcp import analysis

    ours = set(analysis.register(_Collector(), lambda *a, **k: {}))
    assert ours == {"oee_breakdown", "state_timeline", "downtime_pareto", "tag_trend",
                    "trace_rollup", "trace_graph", "maintenance_mttr"}
    listed = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
    for name in ours:
        assert name in listed, name
        props = (listed[name].input_schema or {}).get("properties") or {}
        assert "dry_run" not in props, f"{name} is a read and must take no dry_run"


def test_every_analysis_tool_is_offered_to_anybody_who_may_read_the_plant():
    """No new capability. `plant.read` is the gate on every read in this
    product, and a read gated on more would be a read an operator could not
    make of their own line."""
    from fsmes.services import agent

    offered = {t["name"] for t in agent.catalogue({"plant.read"})}
    assert {"oee_breakdown", "state_timeline", "downtime_pareto",
            "tag_trend"} <= offered


class _Collector:
    """Just enough of the MCP server to collect what `register` declares."""

    def tool(self):
        return lambda func: func


# -------------------------------------------------- more than one result holds

def _budget_of(monkeypatch, characters: int) -> None:
    """Shrink the loop's own result limit, which is where the tools read their
    budget from - rather than shrinking the budget separately and measuring
    something the product does not run on."""
    from fsmes.services import agent

    monkeypatch.setattr(agent, "RESULT_LIMIT", characters)


def test_a_list_too_long_for_one_result_holds_the_tail_back_and_says_how_to_reach_it(
        wired, a_shift_worth_of_history, monkeypatch):
    """Never a silent cut. As many whole stations as fit, how many of how many,
    and the call that reaches the rest - what `plant_settings` has done since
    #108, for the same reason: a list cut by the transport is a list that lies.
    """
    _budget_of(monkeypatch, 1200)
    answer = mcp_server.oee_breakdown("testplant", line="LINE1", hours=2)
    assert answer["stations_showing"] == len(answer["stations"]) == 1
    assert "2 stations" in answer["more"] and "offset=1" in answer["more"]
    # The rollup is the line's and stays true of the whole line.
    assert answer["machines_total"] == 2

    rest = mcp_server.oee_breakdown("testplant", line="LINE1", hours=2, offset=1)
    assert [s["code"] for s in rest["stations"]] == ["PACK01"]
    assert "offset=2" in rest["more"]


def test_a_pareto_holds_the_smallest_reasons_back_worst_first(
        wired, a_shift_worth_of_history, monkeypatch):
    """A pareto is already sorted by what it cost, so the honest tail to drop is
    the cheap end - and the last bucket shown carries the cumulative share that
    says how much of the window these account for."""
    _budget_of(monkeypatch, 700)
    answer = mcp_server.downtime_pareto("testplant", line="LINE1", hours=2)
    assert answer["reasons_total"] == 2
    assert answer["reasons_showing"] == len(answer["reasons"]) == 1
    assert answer["reasons"][0]["reason"] == "infeed jam"   # the longer stop
    assert answer["reasons"][0]["cumulative"] is not None
    assert "of 2 reasons" in answer["more"]


def test_a_gantt_too_wide_for_one_result_asks_the_plant_for_fewer_machines(
        wired, a_shift_worth_of_history, monkeypatch):
    """Not by dropping machines out of the answer: the payload's own
    `machines_shown` would then describe a list that is no longer there. The
    tool measures what came back, asks the plant again for the number that fits,
    and says what was left out and how to name it."""
    _budget_of(monkeypatch, 1000)
    answer = mcp_server.state_timeline("testplant", line="LINE1", hours=2)
    assert answer["machines_shown"] == len(answer["machines"]) == 1
    assert answer["machines_total"] == 2
    assert "1 of 2 machines" in answer["more"]
    assert "equipment=" in answer["more"]


def test_a_trend_over_more_samples_than_fit_asks_the_plant_for_wider_buckets(
        wired, session, monkeypatch):
    """And never for every nth point. Thinning a bucketed series drops the
    excursions `min` and `max` per bucket exist to keep: an excursion inside a
    discarded bucket would be gone from the answer altogether. Wider buckets are
    the plant's own arithmetic over the whole window, which is what the screen
    does with fewer pixels."""
    _samples(session, "MIX01", "Temperature", count=400, minutes=60)
    answer = mcp_server.tag_trend("testplant", machine="MIX01", hours=1, buckets=400)
    assert answer["buckets_requested"] == 400
    assert answer["buckets"] < 400
    assert answer["points_showing"] == len(answer["points"]) <= answer["buckets"]
    assert "nothing was dropped" in answer["more"]
    # Still the plant's own arithmetic: every point is a mean with its own
    # min and max, and the whole window is still covered.
    assert all({"mean", "min", "max", "n"} <= set(point) for point in answer["points"])
    assert sum(point["n"] for point in answer["points"]) == 400
    assert len(json.dumps(answer, default=str)) <= 6000


def test_a_trend_that_fits_is_left_exactly_as_the_plant_answered(wired, session):
    """The bounding is not a shape every answer takes. A window that fits comes
    back with no `buckets`, no `more` and nothing to explain - which is what
    makes the key-for-key comparison above a comparison of the real thing."""
    _samples(session, "MIX01", "Temperature", count=20, minutes=60)
    answer = mcp_server.tag_trend("testplant", machine="MIX01", hours=1, buckets=20)
    assert "more" not in answer and "buckets" not in answer
    assert set(answer) == {"plant", "equipment", "tag", "window", "points"}
