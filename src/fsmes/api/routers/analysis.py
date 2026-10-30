"""Shift analysis endpoints — the questions an ERP cannot answer.

Read-only, so every endpoint is available to any signed-in role.

Three ways to say which window. `hours=` is a trailing span, clamped
server-side because a client asking for a year of 1 Hz tag history is a
mistake, not a request. `shift=` is the plant's own wall-clock window —
`current`, `previous`, or a day and a code such as `2026-09-14/NIGHT` —
because a shift is the unit a plant is run and measured in, and a supervisor
asking "how did my shift go" is asking about a boundary, not about the last
eight hours. A request that names a shift gets that shift; `hours` is ignored
rather than averaged in.

`start=` and `end=` name the two ends of a window, and that window sits
wherever the caller put it — almost always in the past. Both of the others end
at this second, because both answer *how is it going*; a shift that finished
at two o'clock cannot be asked about with either, and *why did this happen*
is a question about a stretch that is over. The machinery has been in
`services.analysis` since the control-chart panel (`_around`, 2026-10-05) and
is exposed here as of 2026-10-11.

Timestamps are ISO 8601. One with an offset is converted; one without is read
as UTC, which is this system's single timestamp convention. Both ends or
neither — a half-named window is refused rather than guessed at — and never
together with `shift=`, because then two parameters name the window and the
answer would have to pick one quietly.

A window that names nothing here — the plant is between shifts, the code is
not a pattern, the pattern does not run that day, the end falls before the
start — is a 400 with one sentence, not a quiet fall-back to eight hours. A
screen that said "night shift" over the last eight hours would be worse than
an error.
"""

from datetime import UTC, datetime

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

#: The third kind of window: the two ends of a stretch of the past. Both or
#: neither, and never beside `shift=` — see `_between`, which is the only
#: place that rule is written down.
_START = Query(None, description="Window start, ISO 8601. Needs `end` too. An "
                                 "offset is converted; none is read as UTC.")
_END = Query(None, description="Window end, ISO 8601. Needs `start` too.")


def _utc(value: datetime) -> datetime:
    """Naive UTC, which is the only timestamp convention this system has.

    A browser sending the window a person typed into two `datetime-local`
    boxes sends it with an offset, because the person typed their own wall
    clock; a script sending UTC sends none. Both are accepted and both become
    the same thing here, rather than one of them being compared against stored
    timestamps in the wrong hour.
    """
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _between(start: datetime | None, end: datetime | None,
             shift: str | None) -> tuple[datetime, datetime] | None:
    """`start=`/`end=` as a window, or None when the caller named neither.

    Three refusals, each one sentence, because each of the three is a request
    this read cannot answer rather than one it can answer approximately:

    - half a window is not a window. "From six o'clock" with no end could mean
      *until now* or *until the end of the shift*, and picking one would be
      this product guessing at the question.
    - `start=`/`end=` and `shift=` both name a window. Whichever one lost
      would be dropped silently, and an unknown query parameter already
      disappears without trace in HTTP — two that mean the same thing must not.
    - an end before its start is nobody's window.

    The far end is not clamped to now: a window running into the future is the
    caller's to ask for, and the envelope's `window` block says how much of it
    the MES actually holds.
    """
    if start is None and end is None:
        return None
    if start is None or end is None:
        raise HTTPException(400, "a window needs both of its ends: give start and "
                                 "end together, or neither")
    if shift:
        raise HTTPException(400, "start and end name a window and so does shift — "
                                 "ask for one of them, not both")
    first, last = _utc(start), _utc(end)
    if last <= first:
        raise HTTPException(400, "the end of a window has to come after its start")
    return first, last


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


@router.get("/energy")
def energy(hours: float | None = _HOURS, shift: str | None = _SHIFT,
           equipment: str | None = Query(None, description="One machine's energy line."),
           db: Session = Depends(get_read_db)) -> dict:
    """What the plant drew against what its circuits can give, and the energy
    one part cost against its recipe's ideal. `energy` is null on a plant with
    no power readings. The same block rides on `/analysis/oee`."""
    return analysis.energy(db, hours=hours, shift=shift, equipment_code=equipment)


@router.get("/timeline")
def timeline(
    db: Session = Depends(get_read_db),
    line: str | None = _LINE,
    hours: float | None = _HOURS,
    shift: str | None = _SHIFT,
    start: datetime | None = _START,
    end: datetime | None = _END,
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
        limit=limit, shift=shift, window=_between(start, end, shift))


#: The stretch of one machine's timeline a reader clicked. A window and not an
#: id: what is drawn is clipped to the window on screen and may be several
#: records folded together where they were too brief to draw, so an id would
#: address one of them and the panel would be about something other than the
#: block. Both ends are required - there is no sensible default for "which
#: block", and a missing end that quietly meant *now* would answer about a
#: different stretch than the one that was clicked.
_BLOCK_START = Query(..., description="Start of the block, as it was drawn.")
_BLOCK_END = Query(..., description="End of the block, as it was drawn.")
_BEFORE = Query(None, gt=0, le=240,
                description="Minutes of the machine's own tags drawn before the "
                            "block. Left out, this panel's default.")
_AFTER = Query(None, gt=0, le=240,
               description="Minutes of the machine's own tags drawn after the "
                           "block. Left out, this panel's default.")
_QUALITY_MINUTES = Query(None, gt=0, le=1440,
                         description="How long after the block quality readings are "
                                     "looked for. Left out, this panel's default.")


@router.get("/timeline/block/{equipment_code}")
def timeline_block(
    equipment_code: str,
    user: UserDep,
    start: datetime = _BLOCK_START,
    end: datetime = _BLOCK_END,
    before_minutes: float | None = _BEFORE,
    after_minutes: float | None = _AFTER,
    quality_minutes: float | None = _QUALITY_MINUTES,
    db: Session = Depends(get_read_db),
) -> dict:
    """One block of a machine's state timeline, with every record behind it.

    What it was and how long; the reason the floor gave it and who named it,
    or that nobody has; the order either side and any maintenance order
    covering it, so a planned stop reads as planned; the machine's own tags
    across it; what quality saw after it; and how much of the stretch anybody
    was watching.

    Read-only, like every analysis. Whether the person asking may *name* an
    unlabelled stop from the panel is answered here as well, because a panel
    that offered the control and then met a 403 would be the product keeping a
    rule it had not told anybody about: the answer carries whether they may
    and, where they may not, who does.
    """
    from fsmes.services import auth, state_block

    role = auth.current_role(db, user)
    return state_block.dossier(
        db, equipment_code, start, end,
        **{name: value for name, value in (
            ("before_minutes", before_minutes),
            ("after_minutes", after_minutes),
            ("quality_minutes", quality_minutes)) if value is not None},
        may_label=bool(role and auth.can(db, role, state_block.LABEL_CAPABILITY)),
        roles=auth.role_bundles(db))


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
    start: datetime | None = _START,
    end: datetime | None = _END,
    buckets: int = Query(240, ge=2, le=2000),
    db: Session = Depends(get_read_db),
) -> dict:
    """One machine's process value over the window, with min/max per bucket."""
    return analysis.tag_trend(db, equipment_code, tag=tag, hours=hours, buckets=buckets,
                              shift=shift, window=_between(start, end, shift))


# ------------------------------------------- several tags, and one point of them
#
# The trend graph's own two reads. `/tag/{code}` above draws one tag; these two
# draw several of one machine's tags on one window and then answer about one
# second of it, which is the walk the product's own answer key says a person
# cannot make today (`labs/.../line.json` -> `_chain`, link 3).
#
# Both are plant-computed reads served as envelopes (decision 0023), and
# neither recomputes anything a read above computes: the bucketing is
# `tag_trend`'s, the states are `/analysis/timeline`'s, and the blocks about a
# machine and a window are the same code the control-chart panel reads
# (`services.moment`).

# `tag`, repeated, and not `tags`: the single-tag read next door is
# `?tag=Temperature`, and one screen asking for two of them should not have to
# spell the parameter differently to do it.
_TAGS = Query(None, alias="tag",
              description="Tag names, repeatable. Omitted means the machine's "
                          "own process value.")


@router.get("/trend/{equipment_code}")
def trend(
    equipment_code: str,
    tags: list[str] | None = _TAGS,
    hours: float | None = _HOURS,
    shift: str | None = _SHIFT,
    start: datetime | None = _START,
    end: datetime | None = _END,
    db: Session = Depends(get_read_db),
) -> dict:
    """Several of one machine's tags over one window, each with its own scale.

    The window is settled once against the machine and handed to every tag, so
    two signals wired up on different days still come back on one axis — which
    is the only way a cause and the effect twenty minutes behind it can be
    seen at all. Series are grouped into scales by unit, because degrees and
    bar on one axis is a picture of the units and not of the plant.

    Says what it drew and what it did not: the buckets nothing arrived in, the
    share of the window each tag spoke in, the machine's watched share, and
    any tag asked for that this machine has never published.
    """
    from fsmes.services import trend as trend_service

    return trend_service.series(db, equipment_code, tags=tags, hours=hours,
                                shift=shift, window=_between(start, end, shift))


@router.get("/trend/{equipment_code}/point")
def trend_point(
    equipment_code: str,
    at: datetime = Query(..., description="The instant clicked, ISO 8601. An "
                                          "offset is converted; none is read "
                                          "as UTC."),
    tag: str = Query(..., description="The tag whose point was clicked."),
    hours: float | None = _HOURS,
    shift: str | None = _SHIFT,
    start: datetime | None = _START,
    end: datetime | None = _END,
    bucket_seconds: float | None = Query(
        None, gt=0, le=86400,
        description="How wide the bucket the point stands for is, from the "
                    "chart that drew it. Left out, only the nearest stored "
                    "sample is reported and no bucket."),
    db: Session = Depends(get_read_db),
) -> dict:
    """What else was happening — the records behind one point of a trend.

    Six blocks, which are the six questions a person in front of a moving
    number asks in order: what the reading was and when; what the machine was
    doing at that second, with the reason and who labelled it; the job that was
    open on it; every maintenance order open on it across the window, with its
    state and why it is in that state; what the lab measured in the window and
    what the control chart said at the time; and what every other tag said at
    that instant.

    The window is the chart's own, not one invented around the point: a panel
    that quietly narrowed to ten minutes either side would answer a different
    question from the one the reader asked, and the maintenance order raised at
    the start of a shift is exactly the answer it would then miss.

    No model, and nothing recomputed.
    """
    from fsmes.services import trend as trend_service

    return trend_service.dossier(
        db, equipment_code, tag=tag, at=_utc(at), hours=hours, shift=shift,
        window=_between(start, end, shift), bucket_seconds=bucket_seconds)


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
