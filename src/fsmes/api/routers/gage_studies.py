"""Gauge studies (gage R&R): plan one, record its readings, read its verdict.

The arithmetic and every rule are in `fsmes.services.gage_rr`; this file
parses and calls.
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from fsmes.api.deps import ActorDep, DbDep, require
from fsmes.services import gage_rr

router = APIRouter()


class StudyIn(BaseModel):
    gauge: str
    material: str
    characteristic: str
    appraisers: list[str]
    parts: int = 10
    trials: int = 3
    purpose: str | None = None
    code: str | None = None


class ReadingIn(BaseModel):
    part: int
    appraiser: str
    trial: int
    value: float


class ReadingsIn(BaseModel):
    readings: list[ReadingIn]


@router.get("/gage-studies")
def studies(
    db: DbDep,
    material: str | None = Query(None, description="Only this material's studies."),
    characteristic: str | None = Query(None, description="Only this characteristic's."),
    gauge: str | None = Query(None, description="Only studies of this gauge."),
    state: str | None = Query(None, description="planned, collecting or complete."),
) -> dict:
    """Every gauge study, newest first, with the total."""
    return gage_rr.studies(db, material=material, characteristic=characteristic,
                           gauge=gauge, state=state)


@router.get("/gage-studies/gauge-or-process")
def gauge_or_process(db: DbDep, material: str, characteristic: str) -> dict:
    """Is it the gauge or the process? The newest complete study beside the
    control chart's capability, in one answer."""
    return gage_rr.gauge_or_process(db, material, characteristic)


@router.post("/gage-studies", status_code=201, dependencies=[require("quality.study")])
def plan(body: StudyIn, db: DbDep, actor: ActorDep) -> dict:
    """Plan a crossed gauge study. The tolerance is copied from the spec."""
    return gage_rr.plan(db, gauge=body.gauge, material=body.material,
                        characteristic=body.characteristic, parts=body.parts,
                        appraisers=body.appraisers, trials=body.trials,
                        purpose=body.purpose, code=body.code, actor=actor)


@router.get("/gage-studies/{code}")
def study(code: str, db: DbDep) -> dict:
    """One study: its plan, every reading, and the analysis - or why not."""
    return gage_rr.result(db, code)


@router.post("/gage-studies/{code}/readings", dependencies=[require("quality.record")])
def readings(code: str, body: ReadingsIn, db: DbDep, actor: ActorDep) -> dict:
    """Record study readings. One already taken is refused, never overwritten."""
    return gage_rr.record(db, code, readings=[r.model_dump() for r in body.readings],
                          actor=actor)
