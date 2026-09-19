"""The plant's downtime vocabulary: read it, draft it, put it in force.

Mounted at `/equipment`, and mounted **before** `fsmes.api.routers.equipment`
in the module registry on purpose: that router ends with `/{code}`, which
matches any single segment, so a catalogue registered after it would be read
as a machine called `downtime-reasons` and answer 404. The same ordering rule
that file states for its own literal paths, applied across two files.

Reading the list needs only `plant.read` - a vocabulary nobody can read is a
vocabulary nobody can choose from. Drafting and approving are separate powers,
because writing down what the plant's stops are called and putting that in
front of every operator are different jobs.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from fsmes.api import paging
from fsmes.api.deps import ActorDep, DbDep, ReadDbDep, require
from fsmes.services import reasons

router = APIRouter()


class ReasonIn(BaseModel):
    code: str
    name: str
    description: str = ""
    # This draft takes the code off the list rather than changing it.
    retires: bool = False
    # What the drafter says about how much history carries the code. Required
    # by the service when a retirement would strand live intervals.
    labels_intervals: int | None = None


@router.get("/downtime-reasons")
def catalog(db: ReadDbDep) -> dict:
    """The reasons an operator may choose from, as `{code: sentence}`.

    The same shape as `/triggers/catalog`: the server owns the list and the
    screen renders it. A plant that has approved nothing gets an empty map and
    a total of zero, which is what the station screen reads as *there is no
    vocabulary here yet* - and it keeps the text box.
    """
    sentences = reasons.catalog(db)
    return {"reasons": sentences, "names": reasons.names(db), "total": len(sentences)}


@router.get("/downtime-reasons/drafts")
def waiting(
    db: ReadDbDep,
    limit: int = paging.LimitQuery,
    offset: int = paging.OffsetQuery,
) -> dict:
    """Every draft waiting on somebody, oldest first, with the whole queue's
    total - not the page's.

    A separate path from the catalogue rather than `?status=draft` on it,
    because the two answer different questions in different shapes: the
    catalogue is a map the screen renders, this is a list a person works
    through.
    """
    rows, total = reasons.drafts(db, limit=limit, offset=offset)
    return paging.page([reasons.out(row) for row in rows], total, limit, offset)


@router.get("/downtime-reasons/{code}")
def history(code: str, db: ReadDbDep) -> dict:
    """Every revision of one reason, including the superseded and retired
    ones. Which revision was in force in March is a question a plant has to
    be able to answer about its own vocabulary, exactly as it is about a
    work instruction."""
    rows = reasons.revisions(db, code)
    live = reasons.in_force(db, code)
    return {
        "code": code,
        "revisions": [reasons.out(row) for row in rows],
        "total": len(rows),
        "in_force": reasons.out(live) if live else None,
        "labels_intervals": reasons.intervals_labelled(db, code),
    }


@router.post("/downtime-reasons", status_code=201,
             dependencies=[require("process.define")])
def define(body: ReasonIn, db: DbDep, actor: ActorDep) -> dict:
    """Draft a reason. It changes nothing on the floor until somebody
    holding `process.approve` puts it in force."""
    return reasons.out(reasons.define(
        db, code=body.code, name=body.name, description=body.description,
        retires=body.retires, labels_intervals=body.labels_intervals, actor=actor))


@router.post("/downtime-reasons/{code}/approve/{revision}",
             dependencies=[require("process.approve")])
def approve(code: str, revision: int, db: DbDep, actor: ActorDep) -> dict:
    """Put one revision in force, recorded against the approver by name.

    Undo is this endpoint with the previous revision number: nothing is
    deleted, and the intervals already labelled keep their labels.
    """
    return reasons.out(reasons.approve(db, code, revision, actor=actor))
