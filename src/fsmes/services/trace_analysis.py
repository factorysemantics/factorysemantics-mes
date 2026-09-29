"""The plant's own arithmetic over its AI trace, its stops and its repairs.

Decision [0039](../../../docs/decisions/0039-an-analysis-is-recorded-code-that-can-only-read.md)
and [the design page](../../../docs/design/deep-analysis.md) §1 work one
management question end to end - *"what is the biggest problem for our
operators?"* - and this module is the arithmetic behind three of its steps.
Three functions, each computed here and served by a route under `/analysis/`
so a screen and an agent read the same envelope (decision 0023), and read by
the agent's tools as pass-throughs exactly as the four analyses are
(`fsmes/mcp/analysis.py`).

**Counts before names.** 0039 clause 2: every rollup over people is grouped by
role, by workcenter or by shift, never by person, unless the caller explicitly
asks - `person=` or `name_people=True`. Clause 3 gates that ask on a capability
of its own, `people.analyse`, which no shipped role holds; the gate lives on the
route, because a capability is a thing the API checks and a service has no
session to check it with. What lives here is the *default*, which is the half of
the clause that has to be true however the function is reached.

**Nothing here is a rate.** These are counts of records, durations between two
recorded instants, and seconds the plant already measured. So every envelope
carries `coverage: "absent"` - the third value `kit.js` has for exactly this
case - rather than a percentage of a window nobody watched. Where an edge is
labelled in seconds it carries how much of that machine's window was watched
beside it, or it carries `null`; a number of seconds presented as though the
machine had been watched the whole time is the failure decision 0033 exists to
prevent, one graph edge at a time.

**Every list states its total.** House rule: `groups_total`, `nodes_total`,
`edges_total`, `orders_total`, and the unattributed and untimed counts that say
what the totals could not account for.

## What this module cannot say, and says so instead

Three silences, each with the milestone that closes it, named in the envelope
rather than left for a reader to notice:

* **No question records the screen it was asked from.** `web/assist.js` posts
  `screen` and `api/routers/assist.py` declares it; nothing reads it, so
  `ai_turns` has no column for it. `screen=` therefore matches nothing, and says
  so with `D1` named, rather than returning every turn as though the filter had
  been applied.
* **No person has a workcenter.** `personnel` is `code`, `name`, `role` and a
  password hash. `by="workcenter"` returns its groups with an empty breakdown
  and every turn unattributed, and names `personnel.home_equipment_id` and D1.
* **Nothing links a question to a stop.** There is no causal edge here and no
  timestamp correlation that would be honest, so the graph draws question
  clusters and downtime clusters and never an edge between them. The design
  page calls that the single most important honesty rule on it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta
from itertools import pairwise

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import (
    AiTurn,
    Equipment,
    EquipmentLevel,
    EquipmentState,
    EquipmentStateName,
    MaintenanceOrder,
    Person,
)
from fsmes.services import calendar as calendar_service
from fsmes.services import connection as connection_service
from fsmes.services import plant_settings

#: How a question's text is reduced before turns are grouped by it. Stated in
#: every envelope as `normalisation`, because grouping text is a judgment and a
#: group count reported without the rule behind it is a fact about nothing.
NORMALISATION = (
    "lowercased; every run of digits replaced with #; everything that is not a "
    "letter, a digit or a space removed; runs of whitespace collapsed to one. "
    "Nothing is stemmed and no words are dropped, so two questions group "
    "together only when the words a person typed are the same words in the "
    "same order."
)

#: The ways a rollup over people may be grouped. Never by person: that is
#: 0039 clause 2, and it is this tuple rather than a sentence in a docstring.
GROUPINGS = ("role", "workcenter", "shift")

#: Bucket widths a repair-time series may be drawn on.
BUCKETS: dict[str, timedelta] = {
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(days=7),
}

#: The node kinds the graph declares. Every one is present in every answer,
#: empty ones included: a graph that omitted `screen` and `workcenter` would
#: read as a complete picture of a plant where those questions came from
#: nowhere (design page §7).
#:
#: `labelling_source` is not in the page's own list of node kinds and is here
#: anyway, because the page's edge table draws `labelled_by` *to* one. It names
#: systems and `"here"`, never people - `reason_source` is which system named a
#: stop, and a person -> reason edge does not exist in this product and must not
#: be drawn as though it did.
NODE_KINDS = ("role", "workcenter", "question_group", "screen", "machine",
              "downtime_reason", "labelling_source", "maintenance_order", "shift",
              "unattributed", "unlabelled")

#: The edge kinds, and for the two with no source at all the sentence that says
#: so. An edge nobody observed is not an edge; an edge kind nobody *can* observe
#: yet is declared empty with its reason, which is the same rule applied to a
#: kind of thing rather than to a count.
EDGE_KINDS: dict[str, str | None] = {
    "asked": None,
    "followed_by": None,
    "asked_from": (
        "no turn records the screen it was asked from: the browser sends it and "
        "nothing reads it, so `ai_turns` has no column for it (milestone D1)."
    ),
    "visited": (
        "nothing here records a page visit or a dwell time - no table, no "
        "beacon, no log this product keeps - so there is no source and no "
        "proxy for it (milestone D9)."
    ),
    "stopped_with": None,
    "labelled_by": None,
    "repaired_by": None,
    "fell_in": None,
}

#: The three measures this module refuses to compute, each with why. A
#: centrality score over an edge set that is *whatever happens to be recorded*
#: is a number with no meaning, and it would be the most convincing wrong thing
#: in the product - the graph's version of the recomputed rate decisions 0031
#: and 0033 exist to prevent.
REFUSED_MEASURES: dict[str, str] = {
    "betweenness": (
        "refused: a shortest path here runs over whatever this plant happens to "
        "have recorded, so one more instrumented machine moves every score."
    ),
    "pagerank": (
        "refused: these edges are the records that exist, not a sample, so a "
        "rank over them reads as importance and measures instrumentation."
    ),
    "eigenvector": (
        "refused: PageRank's objection, worse on a graph this sparse."
    ),
}

_WORD = re.compile(r"[^a-z0-9 ]+")
_DIGITS = re.compile(r"\d+")
_SPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """One question's text, reduced to the key turns are grouped on.

    The rule is `NORMALISATION`, written out once and reported in every
    envelope that uses it. Deliberately shallow: a reader can look at a group
    and see why its turns are in it, which is what makes a group count
    something they can check rather than something they have to believe.
    """
    lowered = _DIGITS.sub("#", text.strip().lower())
    return _SPACE.sub(" ", _WORD.sub(" ", lowered)).strip()


# ----------------------------------------------------------------- the window

def trace_days(db: Session) -> float:
    """How far back this plant keeps its AI trace, in days."""
    return float(plant_settings.setting(db, "admin", "ai_trace_days"))


def _trace_window(db: Session, hours: float | None,
                  shift: str | None) -> tuple[datetime, datetime, dict]:
    """The window an analysis of the trace runs over, and what it says about it.

    Clamped to `[admin] ai_trace_days`, because past the horizon the rows are
    gone - and the clamp is *said*, not applied quietly. A window silently
    shortened to what survived pruning is how "we had no questions about
    labelling in March" comes to mean "March was deleted".
    """
    the_shift = calendar_service.resolve_shift(db, shift) if shift else None
    hours = calendar_service.default_report_hours(db) if hours is None else float(hours)
    now = utcnow()
    if the_shift is not None:
        start, end = the_shift.starts_at, min(the_shift.ends_at, now)
        asked = max(0.0, (end - start).total_seconds() / 3600)
    else:
        end = now
        start = end - timedelta(hours=hours)
        asked = hours

    kept_days = trace_days(db)
    horizon = (now - timedelta(days=kept_days)) if kept_days > 0 else None
    clamped = horizon is not None and start < horizon
    if clamped:
        start = horizon
    start = min(start, end)
    realised = max(0.0, (end - start).total_seconds() / 3600)

    window = {
        "hours": round(realised, 4),
        "requested_hours": round(asked, 4),
        "start": start,
        "end": end,
        "clamped": round(realised, 4) < asked - 0.001,
        "kept_days": kept_days,
        "clamped_to_retention": bool(clamped),
    }
    if clamped:
        window["retention_note"] = (
            f"this window reached back past the {kept_days:g} days this plant keeps "
            f"a trace for (`[admin] ai_trace_days`), so it starts where the trace "
            f"does. What is missing is not absent from the plant's history: it was "
            f"pruned, and a shorter answer is the honest one."
        )
    if the_shift is not None:
        window["shift"] = the_shift.as_json() | {"in_progress": the_shift.ends_at > now}
    return start, end, window


def _previous(start: datetime, end: datetime, kept_days: float) -> tuple[datetime, datetime, str | None]:
    """The window of the same length immediately before this one, and the
    sentence to say instead when it falls outside retention."""
    length = end - start
    previous_start, previous_end = start - length, start
    if kept_days > 0 and previous_start < utcnow() - timedelta(days=kept_days):
        return previous_start, previous_end, (
            f"the window of the same length before this one starts before the "
            f"{kept_days:g} days this plant keeps a trace for, so there is nothing "
            f"to compare against - not nothing that happened, nothing that survived "
            f"pruning."
        )
    return previous_start, previous_end, None


# ------------------------------------------------------------------- rollup

def _turns(db: Session, start: datetime, end: datetime) -> list[AiTurn]:
    return list(db.scalars(
        select(AiTurn).where(AiTurn.ts >= start, AiTurn.ts < end)
        .order_by(AiTurn.ts.asc(), AiTurn.id.asc())))


def _roles(db: Session) -> dict[str, str]:
    """Account code to role, for every account this plant has."""
    return {p.code: p.role for p in db.scalars(select(Person))}


def _attribute(turn: AiTurn, by: str, roles: dict[str, str],
               shifts: dict[int, str | None]) -> str | None:
    """Which group of `by` this turn belongs to, or None for *not attributed*.

    None is a real answer and is counted rather than dropped (house rule: the
    unlabelled are reported as unlabelled). A turn with no person on it, a
    person this plant no longer has, an instant no shift pattern covers, and
    every turn at all when `by="workcenter"` - each of them lands here.
    """
    if by == "role":
        return roles.get(turn.person) if turn.person else None
    if by == "shift":
        return shifts.get(turn.id)
    # `workcenter`: a person has no equipment link in this product at all.
    return None


def trace_rollup(db: Session, hours: float | None = None, shift: str | None = None,
                 by: str = "role", screen: str | None = None,
                 person: str | None = None, name_people: bool = False) -> dict:
    """What this plant's people asked its assistant, grouped by question.

    The answer to *"are they asking the same questions or different ones"*
    (design page §1 step 2), which is the one step of the worked example that
    needs no new data at all: `ai_turns.asked` holds what the person typed, and
    the grouping is done read-side by `normalise` - nothing is stored twice.

    **Grouped by role, by workcenter or by shift; never by person.** 0039
    clause 2. `person=` narrows to one account and `name_people=True` breaks the
    groups down by account, and both are refused by the route unless the caller
    holds `people.analyse`, which no shipped role does. What this function
    guarantees is the default: with neither argument, no key in any breakdown is
    an account code.

    Every group states its turns, its distinct sessions, its breakdown, when it
    was first and last seen, and whether the same question was asked in the
    window of the same length before this one. The envelope states the totals,
    how many turns it could not attribute, how many groups have exactly one turn
    in them - the once-only questions, which are the interesting half - and how
    many turns carried no words at all, because pressing a button is a turn
    nobody typed.

    `coverage` is `"absent"`: these are counts of records, and a coverage
    percentage over them would be a claim about a window nobody watched.
    """
    if by not in GROUPINGS:
        raise ValueError(f"group by one of {', '.join(GROUPINGS)} - never by person "
                         f"(decision 0039). {by!r} is not one of them.")

    start, end, window = _trace_window(db, hours, shift)
    kept_days = window["kept_days"]
    notes: list[str] = []

    turns = _turns(db, start, end)
    if person:
        turns = [t for t in turns if t.person == person]
    if screen is not None:
        # The column does not exist, so no turn can match. Returning everything
        # would be reporting an unfiltered answer as a filtered one.
        turns = []
        notes.append(EDGE_KINDS["asked_from"])

    roles = _roles(db)
    shifts: dict[int, str | None] = {}
    if by == "shift":
        for turn in turns:
            found = calendar_service.shift_for(db, turn.ts)
            shifts[turn.id] = found.code if found else None
    if by == "workcenter":
        notes.append(
            "no person at this plant has a workcenter: `personnel` holds a code, a "
            "name and a role and nothing that points at equipment, so every turn "
            "below is unattributed by workcenter. The nearest thing that exists is "
            "the station one browser last chose, which lives in that browser and is "
            "not a fact about a person. Milestone D1 (`analysis-data-columns`) adds "
            "a nullable `personnel.home_equipment_id`, and this breakdown fills for "
            "every person a plant sets one on."
        )

    previous_start, previous_end, previous_note = _previous(start, end, kept_days)
    previous_keys: set[str] = set()
    if previous_note is None:
        previous_keys = {normalise(t.asked) for t in _turns(db, previous_start, previous_end)
                         if t.asked.strip()}

    groups: dict[str, dict] = {}
    unattributed = wordless = 0
    sessions: set[str] = set()
    people: set[str] = set()
    for turn in turns:
        sessions.add(turn.session)
        if turn.person:
            people.add(turn.person)
        if not turn.asked.strip():
            wordless += 1
            continue
        key = normalise(turn.asked)
        group = groups.setdefault(key, {
            "key": key, "asked": turn.asked, "turns": 0, "sessions": set(),
            "by": defaultdict(int), "unattributed": 0,
            "first_seen": turn.ts, "last_seen": turn.ts,
            "repeated": key in previous_keys,
        })
        group["turns"] += 1
        group["sessions"].add(turn.session)
        group["first_seen"] = min(group["first_seen"], turn.ts)
        group["last_seen"] = max(group["last_seen"], turn.ts)
        where = _attribute(turn, by, roles, shifts)
        if where is None:
            group["unattributed"] += 1
            unattributed += 1
        else:
            group["by"][where] += 1
        if name_people or person:
            group.setdefault("people", defaultdict(int))
            group["people"][turn.person or ""] += 1

    ordered = sorted(groups.values(), key=lambda g: (-g["turns"], g["key"]))
    out_groups = []
    for group in ordered:
        row = {"key": group["key"], "asked": group["asked"], "turns": group["turns"],
               "sessions": len(group["sessions"]), "by": dict(group["by"]),
               "unattributed": group["unattributed"],
               "first_seen": group["first_seen"], "last_seen": group["last_seen"],
               "repeated": group["repeated"]}
        if "people" in group:
            row["people"] = dict(group["people"])
        out_groups.append(row)

    repeated = sum(1 for g in out_groups if g["repeated"])
    turns_total = sum(g["turns"] for g in out_groups)
    # How many accounts could have asked anything at all, so "6 of the 11
    # accounts that can sign in" is a sentence the reader can build.
    accounts = db.scalar(select(func.count()).select_from(Person)
                         .where(Person.password_hash.isnot(None))) or 0

    answer = {
        "window": window,
        "by": by,
        "normalisation": NORMALISATION,
        "groups": out_groups,
        "groups_total": len(out_groups),
        "groups_showing": len(out_groups),
        "groups_of_one": sum(1 for g in out_groups if g["turns"] == 1),
        "turns_total": turns_total,
        "wordless_turns": wordless,
        "sessions_total": len(sessions),
        "people_total": len(people),
        "accounts_that_can_sign_in": accounts,
        # What the groupings could not attribute. Never folded into a group and
        # never dropped: three turns with no person is a hole with a number on
        # it, not three turns fewer.
        "unattributed_turns": unattributed,
        "repeated_groups": repeated,
        "new_groups": len(out_groups) - repeated,
        "repeated_share": round(repeated / len(out_groups), 4) if out_groups else None,
        "previous_window": {"start": previous_start, "end": previous_end}
                           if previous_note is None else None,
        "previous_window_note": previous_note,
        "named_people": bool(name_people or person),
        "person": person,
        # The rule, in the payload rather than in a prompt - so an agent reading
        # this answer inherits it the way it inherits a coverage figure, and a
        # reader who wanted a ranking learns what would have to be granted for
        # one rather than being told "no".
        "naming_note": (
            f"grouped by {by} and never by account: naming a person needs the "
            f"'people.analyse' capability (decision 0039), which no shipped role "
            f"holds, and every answer that names one writes an audit row that "
            f"person can find."
            if not (name_people or person) else
            "this answer names people, so it wrote an audit row "
            "('analysis.person_named') the person named can find."
        ),
        "coverage": "absent",
        "coverage_note": (
            "these are counts of records, not a share of a window anybody "
            "watched, so there is no coverage figure to give and none is invented."
        ),
    }
    if notes:
        answer["note"] = " ".join(notes)
    return answer


# -------------------------------------------------------------------- graph

def _machines(db: Session) -> list[Equipment]:
    return list(db.scalars(
        select(Equipment).where(Equipment.level == EquipmentLevel.WORK_UNIT)
        .order_by(Equipment.code)))


def _overlap(state: EquipmentState, start: datetime, end: datetime) -> float:
    lo = max(state.started_at, start)
    hi = min(state.ended_at or end, end)
    return max(0.0, (hi - lo).total_seconds())


def _graph_facts(db: Session, start: datetime, end: datetime) -> dict:
    """Every node and edge the records support, before any threshold is applied.

    Built kind by kind off the tables that own each fact, and nothing is drawn
    that no row stands behind. In particular there is **no edge between a
    question and a stop**: nothing in this product links the two, and putting
    them next to each other because the times are close would be inventing the
    link a plant manager most wants to read.
    """
    nodes: dict[tuple[str, str], dict] = {}
    edges: dict[tuple[str, str, str], dict] = {}

    def node(kind: str, key: str, label: str, weight: float = 0.0) -> str:
        node_id = f"{kind}:{key}"
        row = nodes.setdefault(node_id, {"id": node_id, "kind": kind, "label": label,
                                         "weight": 0.0})
        row["weight"] = round(row["weight"] + weight, 1)
        return node_id

    def edge(source: str, target: str, kind: str, weight: float,
             watched: float | None = None) -> None:
        row = edges.setdefault((source, target, kind),
                               {"from": source, "to": target, "kind": kind,
                                "weight": 0.0, "watched_seconds": watched})
        row["weight"] = round(row["weight"] + weight, 1)

    # --- the trace: role -> question group, and question group -> question group
    roles = _roles(db)
    turns = _turns(db, start, end)
    by_session: dict[str, list[AiTurn]] = defaultdict(list)
    for turn in turns:
        if not turn.asked.strip():
            continue
        key = normalise(turn.asked)
        question = node("question_group", key, turn.asked, 1)
        role = roles.get(turn.person) if turn.person else None
        if role is None:
            source = node("unattributed", "turns", "turns with no person on them", 1)
        else:
            source = node("role", role, role, 1)
        edge(source, question, "asked", 1)
        by_session[turn.session].append(turn)

    for rows in by_session.values():
        for first, second in pairwise(rows):
            one, two = normalise(first.asked), normalise(second.asked)
            if one == two:
                continue
            edge(f"question_group:{one}", f"question_group:{two}", "followed_by", 1)

    # --- the floor: machine -> reason, reason -> who named it, machine -> shift
    machines = _machines(db)
    by_id = {m.id: m for m in machines}
    ids = list(by_id)
    unknown = connection_service.unknown_seconds(db, ids, start, end)
    window_seconds = max(0.0, (end - start).total_seconds())
    watched = {mid: round(max(0.0, window_seconds - unknown.get(mid, 0.0)), 1) for mid in ids}

    states = db.scalars(
        select(EquipmentState).where(
            EquipmentState.equipment_id.in_(ids),
            EquipmentState.state == EquipmentStateName.DOWN,
            EquipmentState.started_at < end,
            (EquipmentState.ended_at.is_(None)) | (EquipmentState.ended_at > start),
        )) if ids else []

    shift_rows: dict[tuple[int, str], float] = defaultdict(float)
    for state in states:
        seconds = _overlap(state, start, end)
        if seconds <= 0:
            continue
        unit = by_id[state.equipment_id]
        machine = node("machine", unit.code, unit.code, seconds)
        if state.reason_code or state.reason:
            label = state.reason_code or state.reason
            reason = node("downtime_reason", label, label, seconds)
        else:
            reason = node("unlabelled", "downtime", "stops nobody named", seconds)
        edge(machine, reason, "stopped_with", seconds, watched.get(unit.id))
        if state.reason_code or state.reason:
            # Who *named* the stop: a system, or `here` when this MES did. Never
            # a person - `reason_source` is a system name, and a person -> reason
            # edge does not exist in this product.
            who = state.reason_source or "here"
            edge(reason, node("labelling_source", who, who, seconds),
                 "labelled_by", seconds, watched.get(unit.id))
        if state.shift_code:
            shift_rows[(unit.id, state.shift_code)] += 1

    for (unit_id, code), rows in shift_rows.items():
        edge(f"machine:{by_id[unit_id].code}", node("shift", code, code, rows),
             "fell_in", rows)

    # --- maintenance: machine -> order, weighted in minutes where it was timed
    for order in db.scalars(select(MaintenanceOrder).order_by(MaintenanceOrder.code)):
        minutes, _ = _repair_minutes(order)
        stamp = order.completed_at or order.started_at or order.raised_at
        if not (start <= stamp < end):
            continue
        unit = by_id.get(order.equipment_id)
        if unit is None:
            continue
        machine = node("machine", unit.code, unit.code, 0.0)
        job = node("maintenance_order", order.code, order.code, minutes or 0.0)
        edge(machine, job, "repaired_by", minutes or 0.0, None)

    return {"nodes": nodes, "edges": edges, "watched": watched,
            "unknown_seconds": round(sum(unknown.values()), 1),
            "unknown_share": (round(sum(unknown.values()) / (window_seconds * len(ids)), 4)
                              if window_seconds > 0 and ids else None),
            "machines_total": len(machines)}


def _components(nodes: dict, edges: list[dict]) -> list[set[str]]:
    """Connected components, over the edges that survived the threshold.

    Plain union-find over dictionaries: no graph library, because `networkx` is
    2.1 MB of wheel a plant PC would carry for one call, and because the four
    measures this module offers are degree, weight, size and difference, none
    of which needs one.
    """
    parent: dict[str, str] = {node_id: node_id for node_id in nodes}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for row in edges:
        if row["from"] in parent and row["to"] in parent:
            a, b = find(row["from"]), find(row["to"])
            if a != b:
                parent[a] = b

    groups: dict[str, set[str]] = defaultdict(set)
    for node_id in nodes:
        groups[find(node_id)].add(node_id)
    return sorted(groups.values(), key=lambda g: (-len(g), sorted(g)[0]))


def trace_graph(db: Session, hours: float | None = None, shift: str | None = None,
                threshold: float = 1, kinds: list[str] | None = None,
                limit: int | None = None) -> dict:
    """The plant's questions and its stops as one graph of recorded facts.

    Design page §7. Nodes are roles, question groups, machines, downtime
    reasons, who named them, maintenance orders and shifts, plus two that exist
    to make holes visible - `unattributed` for turns with no person and
    `unlabelled` for stops nobody named - each carrying its degree, so the hole
    is a thing on the picture with a number on it rather than a tidy graph that
    happens to be three turns short.

    **Every node kind is in every answer, empty ones included.** `screen` and
    `workcenter` are empty on every plant today and are declared anyway, with
    the reason: a graph that omitted them would read as a complete picture of a
    plant where those questions came from nowhere.

    **Every edge is a recorded fact.** `asked_from` and `visited` have no source
    in this product and are returned as empty edge kinds carrying the sentence
    that says why, not left out. There is no edge between a question and a stop
    at all: nothing links the two, and drawing one because the timestamps are
    close would be inventing the causation.

    The measures are size, what the biggest component touches and what it does
    not, what changed against the window of the same length before this one, and
    degree and weight. **Betweenness, PageRank and eigenvector centrality are
    refused**, by name, with the reason - on a graph whose edge set is whatever
    this plant happens to have recorded, a centrality score is the most
    convincing wrong number the product could offer.

    `threshold` hides edges lighter than it and is stated back, with the number
    of components, because a shape that depends on a choice states the choice.
    `kinds` keeps only those node kinds (and the edges between them); every kind
    is still declared, so a filtered graph says what it is not showing.
    """
    start, end, window = _trace_window(db, hours, shift)
    facts = _graph_facts(db, start, end)

    wanted = set(kinds) if kinds else None
    if wanted:
        unknown_kinds = sorted(wanted - set(NODE_KINDS))
        if unknown_kinds:
            raise ValueError(
                f"no such node kind: {', '.join(unknown_kinds)}. "
                f"This graph's kinds are {', '.join(NODE_KINDS)}.")

    nodes = {nid: row for nid, row in facts["nodes"].items()
             if wanted is None or row["kind"] in wanted}
    edges = [row for row in facts["edges"].values()
             if row["weight"] >= threshold and row["from"] in nodes and row["to"] in nodes]

    degree: dict[str, int] = defaultdict(int)
    for row in edges:
        degree[row["from"]] += 1
        degree[row["to"]] += 1
    for nid, row in nodes.items():
        row["degree"] = degree.get(nid, 0)

    nodes_total, edges_total = len(nodes), len(edges)
    kept = dict(nodes)
    if limit is not None and limit < nodes_total:
        # Heaviest first, and the edges of a node that went are gone with it -
        # so `nodes_showing` is true of the list beside it rather than of a
        # list that is no longer there.
        keep = sorted(nodes.values(), key=lambda r: (-r["weight"], -r["degree"], r["id"]))[:limit]
        kept = {row["id"]: row for row in keep}
        edges = [row for row in edges if row["from"] in kept and row["to"] in kept]

    components = _components(kept, edges)
    biggest = components[0] if components else set()
    touched = sorted({kept[nid]["kind"] for nid in biggest})
    missing = [kind for kind in NODE_KINDS if kind not in touched]

    previous_start, previous_end, previous_note = _previous(start, end, window["kept_days"])
    changed: dict | None = None
    if previous_note is None:
        before = _graph_facts(db, previous_start, previous_end)
        before_nodes = set(before["nodes"])
        now_nodes = set(facts["nodes"])
        changed = {
            "window": {"start": previous_start, "end": previous_end},
            "nodes_then": len(before_nodes), "nodes_now": len(now_nodes),
            "nodes_appeared": len(now_nodes - before_nodes),
            "nodes_gone": len(before_nodes - now_nodes),
            "edges_then": len(before["edges"]), "edges_now": len(facts["edges"]),
        }

    empty_edge_kinds = {kind: reason for kind, reason in EDGE_KINDS.items() if reason}
    drawn = {kind: 0 for kind in EDGE_KINDS}
    for row in edges:
        drawn[row["kind"]] += 1

    return {
        "window": window,
        "threshold": threshold,
        "kinds_requested": sorted(wanted) if wanted else None,
        "node_kinds": {kind: sum(1 for r in kept.values() if r["kind"] == kind)
                       for kind in NODE_KINDS},
        "edge_kinds": drawn,
        "empty_edge_kinds": empty_edge_kinds,
        "empty_node_kinds": {
            "screen": EDGE_KINDS["asked_from"],
            "workcenter": (
                "no person here has a workcenter: `personnel` is a code, a name and "
                "a role, and nothing points at equipment (milestone D1)."
            ),
        },
        "nodes": sorted(kept.values(), key=lambda r: (r["kind"], -r["weight"], r["id"])),
        "edges": sorted(edges, key=lambda r: (r["kind"], -r["weight"], r["from"], r["to"])),
        "nodes_showing": len(kept), "nodes_total": nodes_total,
        "edges_showing": len(edges), "edges_total": edges_total,
        "measures": {
            "components": len(components),
            "threshold": threshold,
            "biggest_component_nodes": len(biggest),
            "biggest_component_weight": round(sum(kept[n]["weight"] for n in biggest), 1),
            "biggest_component_touches": touched,
            "biggest_component_does_not_touch": missing,
            "absence_note": (
                "what the biggest cluster does not touch is a finding, not a gap in "
                "the drawing - and nothing here links a question to a stop at all."
            ),
            "changed_since_previous_window": changed,
            "previous_window_note": previous_note,
            "refused": REFUSED_MEASURES,
        },
        "unknown_seconds": facts["unknown_seconds"],
        "unknown_share": facts["unknown_share"],
        "machines_total": facts["machines_total"],
        "coverage": "absent",
        "coverage_note": (
            "a graph of records is not a rate over a watched window. An edge "
            "weighted in seconds carries that machine's watched seconds, or null."
        ),
    }


# --------------------------------------------------------------------- MTTR

def _repair_minutes(order: MaintenanceOrder) -> tuple[float | None, str | None]:
    """How long this repair took, and which record says so.

    Two sources and they are not the same claim. `started_at` to `completed_at`
    is the time somebody was working on it; `downtime_minutes` is how long the
    machine was down for it, which a plant records when nobody stamped the work.
    Every figure says which it came from, because averaging the two silently
    would be one number standing for two different measurements.
    """
    if order.started_at and order.completed_at and order.completed_at >= order.started_at:
        return round((order.completed_at - order.started_at).total_seconds() / 60, 2), "timestamps"
    if order.downtime_minutes is not None:
        return round(float(order.downtime_minutes), 2), "downtime_minutes"
    return None, None


def _bucket_start(moment: datetime, width: str) -> datetime:
    day = datetime(moment.year, moment.month, moment.day)
    if width == "hour":
        return day + timedelta(hours=moment.hour)
    if width == "week":
        return day - timedelta(days=day.weekday())
    return day


def maintenance_mttr(db: Session, hours: float | None = None,
                     equipment: str | None = None, kind: str | None = None,
                     bucket: str = "day") -> dict:
    """Repair time over time, with the count of repairs nobody timed beside it.

    Design page §1 step 7: *"graph maintenance repair time over time"* is
    answerable today, **with a count beside it**. `maintenance_orders` carries
    `raised_at`, `started_at`, `completed_at`, `performed_by` and
    `downtime_minutes`, and every one but `raised_at` is nullable - so an MTTR
    over 6 of 19 orders is a different fact from an MTTR over 19, and this
    function never reports the first as though it were the second.

    Each bucket states `n`, `mean`, `min`, `max`, `untimed`, and which record
    each timed order's minutes came from. Where a preventive plan exists, its
    `expected_minutes` is the plan against which the actual is reported - never
    folded into the mean, because a plan and a measurement are two different
    things and 0031 is about not letting the second become the first.

    An order falls in a bucket by when it completed, or by when it was started,
    or by when it was raised - the most definite stamp it has, said per order as
    `stamped_by`. `hours` left out is this plant's own default reporting window.
    """
    if bucket not in BUCKETS:
        raise ValueError(f"bucket by one of {', '.join(BUCKETS)}; {bucket!r} is not one.")

    hours = calendar_service.default_report_hours(db) if hours is None else float(hours)
    end = utcnow()
    start = end - timedelta(hours=hours)

    units = {u.id: u for u in _machines(db)}
    wanted = [c.strip() for c in equipment.split(",")] if equipment else None
    if wanted:
        unknown_codes = sorted(set(wanted) - {u.code for u in units.values()})
        if unknown_codes:
            raise ValueError(f"no such machine: {', '.join(unknown_codes)}")

    orders = list(db.scalars(select(MaintenanceOrder).order_by(MaintenanceOrder.code)))
    rows: list[dict] = []
    for order in orders:
        unit = units.get(order.equipment_id)
        if unit is None or (wanted and unit.code not in wanted):
            continue
        if kind and str(order.kind) != kind:
            continue
        if order.completed_at is not None:
            stamp, stamped_by = order.completed_at, "completed_at"
        elif order.started_at is not None:
            stamp, stamped_by = order.started_at, "started_at"
        else:
            stamp, stamped_by = order.raised_at, "raised_at"
        if not (start <= stamp < end):
            continue
        minutes, source = _repair_minutes(order)
        rows.append({"code": order.code, "machine": unit.code, "kind": str(order.kind),
                     "status": str(order.status), "at": stamp, "stamped_by": stamped_by,
                     "minutes": minutes, "minutes_from": source,
                     "expected_minutes": (float(order.plan.expected_minutes)
                                          if order.plan is not None else None)})

    buckets: dict[datetime, dict] = {}
    for row in rows:
        key = _bucket_start(row["at"], bucket)
        slot = buckets.setdefault(key, {"t": key, "n": 0, "untimed": 0, "minutes": [],
                                        "from_timestamps": 0, "from_downtime_minutes": 0,
                                        "planned": [], "actual_against_plan": []})
        if row["minutes"] is None:
            slot["untimed"] += 1
            continue
        slot["n"] += 1
        slot["minutes"].append(row["minutes"])
        slot[f"from_{row['minutes_from']}"] += 1
        if row["expected_minutes"] is not None:
            slot["planned"].append(row["expected_minutes"])
            slot["actual_against_plan"].append(row["minutes"])

    series = []
    for key in sorted(buckets):
        slot = buckets[key]
        minutes = slot.pop("minutes")
        planned = slot.pop("planned")
        against = slot.pop("actual_against_plan")
        series.append({
            **slot,
            "mean": round(sum(minutes) / len(minutes), 2) if minutes else None,
            "min": round(min(minutes), 2) if minutes else None,
            "max": round(max(minutes), 2) if minutes else None,
            # The plan, beside the actual, over the orders that have one - and
            # `planned_n` says how many that was, because a plan over two of
            # eleven orders is not this bucket's plan.
            "planned_n": len(planned),
            "planned_mean": round(sum(planned) / len(planned), 2) if planned else None,
            "actual_mean_where_planned": (round(sum(against) / len(against), 2)
                                          if against else None),
        })

    timed = [r["minutes"] for r in rows if r["minutes"] is not None]
    return {
        "window": {"hours": round(hours, 4), "requested_hours": round(hours, 4),
                   "start": start, "end": end, "clamped": False},
        "bucket": bucket,
        "equipment": wanted,
        "kind": kind,
        "buckets": series,
        "buckets_total": len(series),
        "buckets_showing": len(series),
        "orders": rows,
        "orders_total": len(rows),
        "orders_showing": len(rows),
        "timed_total": len(timed),
        # The number that makes the mean readable. An MTTR with no count of the
        # repairs it could not measure is the failure this whole function exists
        # to avoid.
        "untimed_total": len(rows) - len(timed),
        "mttr_minutes": round(sum(timed) / len(timed), 2) if timed else None,
        "from_timestamps": sum(1 for r in rows if r["minutes_from"] == "timestamps"),
        "from_downtime_minutes": sum(1 for r in rows
                                     if r["minutes_from"] == "downtime_minutes"),
        "machines_total": len(units),
        "coverage": "absent",
        "coverage_note": (
            "durations between two recorded instants, not a share of a watched "
            "window: `untimed_total` is what this answer could not measure, and it "
            "is the figure to read beside the mean."
        ),
    }
