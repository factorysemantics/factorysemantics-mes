"""The plant's non-conformance severities: read them, draft them, put them in
force.

The same six paths the downtime vocabulary has, under `/quality` instead of
`/equipment`, because a second vocabulary that answered in a different shape
would be a second thing to learn rather than the same thing again.

Mounted **before** `fsmes.api.routers.quality` in the module registry. That
router has no single-segment catch-all today, so nothing collides yet - the
ordering is here because the reason it matters on `/equipment` is structural
rather than accidental, and a `/quality/{code}` added next month should find
this already right.

Reading the list needs only `plant.read`: a vocabulary nobody can read is a
vocabulary nobody can choose from. Drafting and approving are separate
powers - writing down what this plant calls a serious finding and putting
that in front of every quality record are different jobs.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from fsmes.api import paging
from fsmes.api.deps import ActorDep, DbDep, ReadDbDep, require
from fsmes.services import severities

router = APIRouter()


class SeverityIn(BaseModel):
    code: str
    name: str
    description: str = ""
    # This draft takes the code off the list rather than changing it.
    retires: bool = False
    # What the drafter says about how many non-conformances carry the code.
    # Required by the service when a retirement would strand live records.
    labels_records: int | None = None


@router.get("/severities")
def catalog(db: ReadDbDep) -> dict:
    """The severities a non-conformance may be raised at, as
    `{code: sentence}`.

    A plant that has approved nothing gets an empty map and a total of zero,
    which is what the write path reads as *there is no vocabulary here yet* -
    and the column goes on taking what it is given, exactly as it does today.
    """
    sentences = severities.catalog(db)
    return {"severities": sentences, "names": severities.names(db),
            "total": len(sentences)}


@router.get("/severities/drafts")
def waiting(
    db: ReadDbDep,
    limit: int = paging.LimitQuery,
    offset: int = paging.OffsetQuery,
) -> dict:
    """Every severity draft waiting on somebody, oldest first, with the whole
    queue's total - not the page's."""
    rows, total = severities.drafts(db, limit=limit, offset=offset)
    return paging.page([severities.out(row) for row in rows], total, limit, offset)


@router.get("/severities/vocabulary")
def vocabulary(db: ReadDbDep) -> dict:
    """The whole vocabulary, in code order - approved, draft and retired
    together, with how many non-conformances each code labels.

    What the Severities screen lists. Separate from the catalogue for the
    same reason the drafts queue is: the catalogue is the list a record may be
    raised from, and offering a draft or a retired word there would put into
    the quality record exactly what the lifecycle exists to keep out of it.

    Each row says whether the product itself writes the code, because that is
    what decides whether it can be retired, and a person deserves to know
    before they try.
    """
    rows = severities.vocabulary(db)
    written = dict(severities.PRODUCT_WRITES)
    for row in rows:
        row["product_writes"] = written.get(row["code"])
    return {"severities": rows, "total": len(rows),
            "product_writes": [code for code, _ in severities.PRODUCT_WRITES]}


@router.get("/severities/{code}")
def history(code: str, db: ReadDbDep) -> dict:
    """Every revision of one severity, superseded and retired ones included.

    Which word was in force in March is a question a plant has to be able to
    answer about its own quality records, exactly as it is about a work
    instruction.
    """
    rows = severities.revisions(db, code)
    live = severities.in_force(db, code)
    return {
        "code": code,
        "revisions": [severities.out(row) for row in rows],
        "total": len(rows),
        "in_force": severities.out(live) if live else None,
        "labels_records": severities.records_labelled(db, code),
    }


@router.post("/severities", status_code=201,
             dependencies=[require("quality.define")])
def define(body: SeverityIn, db: DbDep, actor: ActorDep) -> dict:
    """Draft a severity. It grades nothing until somebody holding
    `quality.approve` puts it in force."""
    return severities.out(severities.define(
        db, code=body.code, name=body.name, description=body.description,
        retires=body.retires, labels_records=body.labels_records, actor=actor))


@router.post("/severities/{code}/approve/{revision}",
             dependencies=[require("quality.approve")])
def approve(code: str, revision: int, db: DbDep, actor: ActorDep) -> dict:
    """Put one revision in force, recorded against the approver by name.

    Undo is this endpoint with the previous revision number: nothing is
    deleted, and the non-conformances already graded keep their grade.
    """
    return severities.out(severities.approve(db, code, revision, actor=actor))
