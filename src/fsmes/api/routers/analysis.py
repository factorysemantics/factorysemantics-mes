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

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from fsmes.api.deps import get_read_db
from fsmes.services import analysis

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
