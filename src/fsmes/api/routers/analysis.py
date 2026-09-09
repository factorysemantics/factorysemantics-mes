"""Shift analysis endpoints — the questions an ERP cannot answer.

Read-only, so every endpoint is available to any signed-in role. Windows are
given in hours and clamped server-side, because a client asking for a year of
1 Hz tag history is a mistake, not a request.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from fsmes.api.deps import get_db
from fsmes.services import analysis

router = APIRouter()

_HOURS = Query(8.0, gt=0, le=720, description="Window size in hours (max 30 days).")
_LINE = Query(None, description="Work centre code; omitted means the line with the most machines.")


@router.get("/lines")
def lines(db: Session = Depends(get_db)) -> list[dict]:
    """Every work centre that has machines on it."""
    return analysis.lines(db)


@router.get("/oee")
def oee(line: str | None = _LINE, hours: float = _HOURS, db: Session = Depends(get_db)) -> dict:
    """OEE per station with each loss named in units and seconds."""
    return analysis.oee_breakdown(db, line_code=line, hours=hours)


@router.get("/timeline")
def timeline(
    db: Session = Depends(get_db),
    line: str | None = None,
    hours: float = _HOURS,
    equipment: str | None = Query(None, description="Comma-separated machine codes."),
    limit: int = Query(12, ge=1, le=60, description="How many machines to draw."),
) -> dict:
    """Every state interval per machine - the shift drawn as a Gantt.

    Scoped, because sixty machines is 3 MB of intervals and not a readable
    chart. The response says how many machines exist so the screen can offer
    the rest rather than pretending it showed everything.
    """
    return analysis.state_timeline(
        db, line, hours,
        equipment=[c.strip() for c in equipment.split(",")] if equipment else None,
        limit=limit)


@router.get("/downtime")
def downtime(line: str | None = _LINE, hours: float = _HOURS, db: Session = Depends(get_db)) -> dict:
    """Downtime by reason, worst first. Unlabelled stops are reported as such."""
    return analysis.downtime_pareto(db, line_code=line, hours=hours)


@router.get("/production")
def production(
    line: str | None = _LINE,
    hours: float = _HOURS,
    buckets: int = Query(60, ge=2, le=600),
    db: Session = Depends(get_db),
) -> dict:
    """Good and scrap over time for the whole line."""
    return analysis.production_trend(db, line_code=line, hours=hours, buckets=buckets)


@router.get("/tag/{equipment_code}")
def tag(
    equipment_code: str,
    tag: str | None = Query(None, description="Tag name; omitted means the machine's process value."),
    hours: float = _HOURS,
    buckets: int = Query(240, ge=2, le=2000),
    db: Session = Depends(get_db),
) -> dict:
    """One machine's process value over the window, with min/max per bucket."""
    return analysis.tag_trend(db, equipment_code, tag=tag, hours=hours, buckets=buckets)
