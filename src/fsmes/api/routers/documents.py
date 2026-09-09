"""Work instruction endpoints.

Reading an approved instruction needs only plant.read - an instruction nobody
can read is not an instruction. Writing and approving are separate powers,
because drafting a procedure and putting it in force are different jobs.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from fsmes.api.deps import ActorDep, DbDep, require
from fsmes.services import documents

router = APIRouter()


class DocumentIn(BaseModel):
    code: str
    title: str
    body: str = ""
    anchors: dict[str, str] | None = None
    # A recorded walkthrough: kind="walkthrough", steps against real controls,
    # and the capability a person needs to follow it.
    kind: str = "instruction"
    steps: list[dict] | None = None
    needs: str | None = None


class ReviseIn(BaseModel):
    title: str | None = None
    body: str | None = None
    anchors: dict[str, str] | None = None
    steps: list[dict] | None = None
    needs: str | None = None


def _out(doc) -> dict:
    return {
        "code": doc.code,
        "revision": doc.revision,
        "title": doc.title,
        "body": doc.body,
        "status": doc.status.value,
        "kind": doc.kind,
        "steps": doc.steps_list() if doc.kind == "walkthrough" else None,
        "needs": doc.needs,
        "anchors": doc.anchors(),
        "created_by": doc.created_by,
        "created_at": doc.created_at,
        "approved_by": doc.approved_by,
        "approved_at": doc.approved_at,
        "drafted_by_model": doc.drafted_by_model,
    }


@router.get("")
def catalogue(
    db: DbDep,
    q: str | None = Query(None, description="Match a document code or title."),
    in_force: bool | None = Query(
        None, description="Only documents with an approved revision (true) or without (false)."),
) -> list[dict]:
    """Every instruction, showing which revision is in force - or the ones
    that match."""
    rows = documents.catalogue(db)
    if q:
        needle = q.lower()
        rows = [d for d in rows if needle in f"{d['code']} {d['title']}".lower()]
    if in_force is not None:
        rows = [d for d in rows if bool(d.get("approved_revision")) == in_force]
    return rows


@router.get("/for")
def for_context(
    db: DbDep,
    material: str | None = None,
    characteristic: str | None = None,
    operation: str | None = None,
    equipment: str | None = None,
) -> list[dict]:
    """Approved instructions relevant to what someone is doing right now.

    This is what puts the right procedure beside the form that does the work,
    rather than in a folder nobody opens.
    """
    return [_out(d) for d in documents.for_anchor(
        db, material=material, characteristic=characteristic,
        operation=operation, equipment=equipment)]


@router.get("/{code}")
def read(code: str, db: DbDep, revision: int | None = None) -> dict:
    if revision is not None:
        for doc in documents.revisions(db, code):
            if doc.revision == revision:
                return _out(doc)
        return {"error": f"no {code} revision {revision}"}
    doc = documents.current(db, code) or documents.draft(db, code)
    if doc is None:
        return {"error": f"no document {code}"}
    return _out(doc)


@router.get("/{code}/revisions")
def history(code: str, db: DbDep) -> list[dict]:
    """Every revision, including superseded ones.

    An auditor asking what the instruction said last March is asking this.
    """
    return [_out(d) for d in documents.revisions(db, code)]


@router.post("", status_code=201, dependencies=[require("documents.write")])
def create(body: DocumentIn, db: DbDep, actor: ActorDep) -> dict:
    return _out(documents.create(db, code=body.code, title=body.title,
                                 body=body.body, anchors=body.anchors, actor=actor,
                                 kind=body.kind, steps=body.steps, needs=body.needs))


@router.post("/{code}/revise", dependencies=[require("documents.write")])
def revise(code: str, body: ReviseIn, db: DbDep, actor: ActorDep) -> dict:
    return _out(documents.revise(db, code, title=body.title, body=body.body,
                                 anchors=body.anchors, steps=body.steps, needs=body.needs,
                                 actor=actor))


@router.post("/{code}/approve/{revision}", dependencies=[require("documents.approve")])
def approve(code: str, revision: int, db: DbDep, actor: ActorDep) -> dict:
    """Put a revision in force. Recorded against the approver by name."""
    return _out(documents.approve(db, code, revision, actor=actor))


@router.post("/{code}/withdraw", dependencies=[require("documents.approve")])
def withdraw(code: str, db: DbDep, actor: ActorDep) -> dict:
    return _out(documents.withdraw(db, code, actor=actor))
