"""Shift analysis endpoints — the questions an ERP cannot answer.

Read-only, so every endpoint is available to any signed-in role.

Two ways to say which window. `hours=` is a trailing span, clamped server-side
because a client asking for a year of 1 Hz tag history is a mistake, not a
request. `shift=` is the plant's own wall-clock window — `current`, `previous`,
or a day and a code such as `2026-09-14/NIGHT` — because a shift is the unit a
plant is run and measured in, and a supervisor asking "how did my shift go"
is asking about a boundary, not about the last eight hours. A request that
names a shift gets that shift; `hours` is ignored rather than averaged in.

A shift that names nothing here — the plant is between shifts, the code is not
a pattern, the pattern does not run that day — is a 400 with one sentence, not
a quiet fall-back to eight hours. A screen that said "night shift" over the
last eight hours would be worse than an error.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from fsmes.api.deps import UserDep, get_read_db, require
from fsmes.services import analysis
from fsmes.services import trace_analysis as analysis_trace

router = APIRouter()

#: Window size, and **no default of its own**. Left out, the service reads this
#: plant's `[process] default_report_hours` at the moment of the request, which
#: is what makes it editable on Engineering's Configuration page with no
#: restart. The eight hours that used to be declared here was the product's
#: assumption that a shift is eight hours long, and a twelve-hour plant reading
#: this schema was being told its own default was somebody else's.
#:
#: The ceiling stays: thirty days is the most any window control in this
#: product will draw, and `fsmes pack check` refuses a plant default above it
#: so the two cannot disagree.
_HOURS = Query(None, gt=0, le=720,
               description="Window size in hours (max 30 days). Left out, this "
                           "plant's own default reporting window.")
_LINE = Query(None, description="Work centre code; omitted means the line with the most machines.")
_SHIFT = Query(
    None,
    description="Window by shift instead of hours: `current`, `previous`, "
                "or `YYYY-MM-DD/CODE` on the plant's own clock. Overrides `hours`.",
)


@router.get("/lines")
def lines(db: Session = Depends(get_read_db)) -> list[dict]:
    """Every work centre that has machines on it."""
    return analysis.lines(db)


@router.get("/shifts")
def shifts(
    days: int = Query(7, ge=1, le=90, description="How far back to list shifts."),
    db: Session = Depends(get_read_db),
) -> dict:
    """The shifts a screen can offer, which one is running, and on whose clock."""
    return analysis.shifts(db, days=days)


@router.get("/oee")
def oee(line: str | None = _LINE, hours: float | None = _HOURS, shift: str | None = _SHIFT,
        db: Session = Depends(get_read_db)) -> dict:
    """OEE per station with each loss named in units and seconds."""
    return analysis.oee_breakdown(db, line_code=line, hours=hours, shift=shift)


@router.get("/timeline")
def timeline(
    db: Session = Depends(get_read_db),
    line: str | None = _LINE,
    hours: float | None = _HOURS,
    shift: str | None = _SHIFT,
    equipment: str | None = Query(None, description="Comma-separated machine codes."),
    # No default here either: left out, it is this plant's own screenful.
    limit: int | None = Query(None, ge=1, le=60,
                              description="How many machines to draw. Left out, "
                                          "this plant's own screenful."),
) -> dict:
    """Every state interval per machine - the shift drawn as a Gantt.

    Scoped, because sixty machines is 3 MB of intervals and not a readable
    chart. The response says how many machines exist so the screen can offer
    the rest rather than pretending it showed everything.
    """
    return analysis.state_timeline(
        db, line, hours,
        equipment=[c.strip() for c in equipment.split(",")] if equipment else None,
        limit=limit, shift=shift)


@router.get("/downtime")
def downtime(line: str | None = _LINE, hours: float | None = _HOURS, shift: str | None = _SHIFT,
             db: Session = Depends(get_read_db)) -> dict:
    """Downtime by reason, worst first. Unlabelled stops are reported as such."""
    return analysis.downtime_pareto(db, line_code=line, hours=hours, shift=shift)


@router.get("/production")
def production(
    line: str | None = _LINE,
    hours: float | None = _HOURS,
    shift: str | None = _SHIFT,
    buckets: int = Query(60, ge=2, le=600),
    db: Session = Depends(get_read_db),
) -> dict:
    """Good and scrap over time for the whole line."""
    return analysis.production_trend(db, line_code=line, hours=hours, buckets=buckets, shift=shift)


@router.get("/tag/{equipment_code}")
def tag(
    equipment_code: str,
    tag: str | None = Query(None, description="Tag name; omitted means the machine's process value."),
    hours: float | None = _HOURS,
    shift: str | None = _SHIFT,
    buckets: int = Query(240, ge=2, le=2000),
    db: Session = Depends(get_read_db),
) -> dict:
    """One machine's process value over the window, with min/max per bucket."""
    return analysis.tag_trend(db, equipment_code, tag=tag, hours=hours, buckets=buckets, shift=shift)


# --------------------------------------------------- the trace, the graph, MTTR
#
# Three reads that answer the design page's worked example
# (`docs/design/deep-analysis.md` §1) from the records this plant already has.
# They are here rather than under `/ai` because they are analyses - the plant's
# own arithmetic, served as an envelope a screen and an agent read the same way
# (decision 0023) - and because the tab that will draw them is an analysis tab.
#
# Two gates, not one. `plant.read` is the gate on every read in this product.
# The two that read `ai_turns` carry `audit.read` as well, which is the gate
# `GET /ai` already uses on the same rows: an analysis of the trace must not be
# a way around the gate on the trace. And naming a person carries a third,
# `people.analyse`, which no shipped role holds - decision 0039 clause 3.

_TRACE_HOURS = Query(
    None, gt=0, le=8760,
    description="Window size in hours. Left out, this plant's own default "
                "reporting window. Clamped to `[admin] ai_trace_days`, and the "
                "answer says when it was.",
)

#: What a request that names a person is refused with when the plant has not
#: granted the capability for it. It names the capability, says who holds it -
#: nobody, on a plant that has not defined a role for it - and says what the
#: answer would have been instead, because a refusal that leaves a person with
#: no next step is half an answer.
def _people_refusal(db) -> str:
    from fsmes.services import auth, capabilities

    return (
        f"naming a person in an analysis needs the 'people.analyse' capability "
        f"(decision 0039). {capabilities.who_holds('people.analyse', auth.role_bundles(db))}. "
        f"Without it this answer is grouped by role, by workcenter or by shift and "
        f"never by account, which is the question a plant usually means: which work "
        f"is hard, not who is slow."
    )


def _may_name_people(db, user: dict) -> bool:
    from fsmes.services import auth

    role = auth.current_role(db, user)
    return bool(role and auth.can(db, role, "people.analyse"))


@router.get("/trace/rollup", dependencies=[require("audit.read")])
def trace_rollup(
    user: UserDep,
    db: Session = Depends(get_read_db),
    hours: float | None = _TRACE_HOURS,
    shift: str | None = _SHIFT,
    by: str = Query("role", description="role, workcenter or shift. Never person."),
    screen: str | None = Query(None, description="Only questions asked from this "
                                                 "screen, as a route path. Turns "
                                                 "that record none are counted, "
                                                 "not matched."),
    person: str | None = Query(None, description="One account's turns. Needs "
                                                 "`people.analyse`."),
    name_people: bool = Query(False, description="Break every group down by account "
                                                 "as well. Needs `people.analyse`."),
) -> dict:
    """What this plant's people asked its assistant, grouped by question.

    Grouped by role, by workcenter or by shift - never by person unless the
    caller asks for that and holds `people.analyse`.
    """
    if (person or name_people) and not _may_name_people(db, user):
        raise HTTPException(403, _people_refusal(db))
    try:
        answer = analysis_trace.trace_rollup(db, hours=hours, shift=shift, by=by,
                                            screen=screen, person=person,
                                            name_people=name_people)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if person or name_people:
        _record_naming(user, person=person, by=by)
    return answer


def _record_naming(user: dict, *, person: str | None, by: str) -> None:
    """The audit row every per-person answer writes - 0039 clause 4.

    In a write session of its own, because the read above runs on the
    transaction that may not write, and because the row must be written whether
    or not the answer was empty: *that somebody asked* is the record, and an
    analysis that found nothing about a person was still an analysis about them.

    Written after the answer is computed and never instead of it, but a failure
    here is not swallowed: the whole point of the row is that the person named
    can find out, and an answer that quietly failed to record itself is the one
    thing this clause exists to prevent.
    """
    from fsmes.api import deps
    from fsmes.services import audit

    with deps.short_write() as write:
        audit.record(write, actor=user["sub"], action="analysis.person_named",
                     entity_type="personnel", entity_id=person or "*",
                     after={"by": by, "named": person, "all_accounts": person is None})


@router.get("/trace/graph", dependencies=[require("audit.read")])
def trace_graph(
    db: Session = Depends(get_read_db),
    hours: float | None = _TRACE_HOURS,
    shift: str | None = _SHIFT,
    threshold: float = Query(1, ge=0, description="Hide edges lighter than this."),
    kinds: str | None = Query(None, description="Comma-separated node kinds to keep."),
    limit: int | None = Query(None, ge=1, le=2000,
                              description="How many nodes to draw, heaviest first."),
) -> dict:
    """The plant's questions and its stops as one graph of recorded facts.

    Every node kind is declared, empty ones included; every edge is a record;
    the centralities are refused by name.
    """
    try:
        return analysis_trace.trace_graph(
            db, hours=hours, shift=shift, threshold=threshold,
            kinds=[k.strip() for k in kinds.split(",")] if kinds else None,
            limit=limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/maintenance/mttr", dependencies=[require("plant.read")])
def maintenance_mttr(
    db: Session = Depends(get_read_db),
    hours: float | None = _TRACE_HOURS,
    equipment: str | None = Query(None, description="Comma-separated machine codes."),
    kind: str | None = Query(None, description="preventive or corrective."),
    bucket: str = Query("day", description="hour, day or week."),
) -> dict:
    """Repair time over time, with the count of repairs nobody timed beside it."""
    try:
        return analysis_trace.maintenance_mttr(db, hours=hours, equipment=equipment,
                                               kind=kind, bucket=bucket)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
