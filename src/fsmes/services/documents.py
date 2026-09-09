"""The document lifecycle: draft, approve, revise, withdraw.

The rule that makes these controlled rather than merely stored: an approved
revision is never edited. Revising creates the next revision as a draft and
leaves the approved one in force until the new one is approved, so the floor
never loses its instruction mid-change and "what did it say in March" stays
answerable.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import Document, DocumentStatus
from fsmes.services import Conflict, Invalid, NotFound, audit

ANCHOR_FIELDS = ("material", "characteristic", "operation", "equipment")


def _anchor_kwargs(anchors: dict | None) -> dict:
    anchors = anchors or {}
    unknown = sorted(set(anchors) - set(ANCHOR_FIELDS))
    if unknown:
        raise Invalid(f"unknown anchor(s): {', '.join(unknown)}. "
                      f"Expected any of {', '.join(ANCHOR_FIELDS)}.")
    return {f"anchor_{k}": anchors.get(k) for k in ANCHOR_FIELDS}


def revisions(session: Session, code: str) -> list[Document]:
    return list(session.scalars(
        select(Document).where(Document.code == code).order_by(Document.revision)))


def current(session: Session, code: str) -> Document | None:
    """The revision in force: the highest-numbered approved one."""
    return session.scalar(
        select(Document)
        .where(Document.code == code, Document.status == DocumentStatus.APPROVED)
        .order_by(Document.revision.desc())
        .limit(1))


def draft(session: Session, code: str) -> Document | None:
    """The open draft, if there is one. At most one per document."""
    return session.scalar(
        select(Document)
        .where(Document.code == code, Document.status == DocumentStatus.DRAFT)
        .order_by(Document.revision.desc())
        .limit(1))


def _walk_kwargs(kind: str, steps: list[dict] | None, needs: str | None) -> dict:
    """The walkthrough columns, validated - or the instruction's empty ones."""
    from fsmes.services import walkthroughs
    if kind == "walkthrough":
        return {"kind": kind, "steps": walkthroughs.dumps(walkthroughs.validate_steps(steps)),
                "needs": walkthroughs.validate_needs(needs)}
    if kind != "instruction":
        raise Invalid(f"unknown document kind {kind!r}: instruction or walkthrough")
    if steps:
        raise Invalid("only a walkthrough has steps")
    return {"kind": "instruction", "steps": None, "needs": None}


def create(session: Session, *, code: str, title: str, body: str,
           anchors: dict | None = None, actor: str = "system",
           drafted_by_model: str | None = None, kind: str = "instruction",
           steps: list[dict] | None = None, needs: str | None = None) -> Document:
    if revisions(session, code):
        raise Conflict(f"document {code} already exists — revise it instead")

    doc = Document(code=code, revision=1, title=title, body=body,
                   status=DocumentStatus.DRAFT, created_by=actor,
                   drafted_by_model=drafted_by_model, **_anchor_kwargs(anchors),
                   **_walk_kwargs(kind, steps, needs))
    session.add(doc)
    session.flush()
    audit.record(session, actor=actor, action="document.drafted",
                 entity_type="document", entity_id=code,
                 after={"revision": 1, "title": title,
                        "drafted_by_model": drafted_by_model})
    return doc


def revise(session: Session, code: str, *, title: str | None = None,
           body: str | None = None, anchors: dict | None = None,
           steps: list[dict] | None = None, needs: str | None = None,
           actor: str = "system") -> Document:
    """Open the next revision as a draft, copying the one in force.

    An approved revision is never edited in place. That is the whole point of
    calling these controlled: the version somebody signed stays exactly as
    they signed it.
    """
    existing = revisions(session, code)
    if not existing:
        raise NotFound(f"no document {code}")

    open_draft = draft(session, code)
    if open_draft is not None:
        # Editing an unapproved draft is fine - nobody is following it yet.
        if title is not None:
            open_draft.title = title
        if body is not None:
            open_draft.body = body
        if anchors is not None:
            for field, value in _anchor_kwargs(anchors).items():
                setattr(open_draft, field, value)
        if steps is not None or needs is not None:
            walk = _walk_kwargs(open_draft.kind, steps if steps is not None else open_draft.steps_list(),
                                needs if needs is not None else open_draft.needs)
            open_draft.steps, open_draft.needs = walk["steps"], walk["needs"]
        session.flush()
        audit.record(session, actor=actor, action="document.draft_edited",
                     entity_type="document", entity_id=code,
                     after={"revision": open_draft.revision})
        return open_draft

    base = current(session, code) or existing[-1]
    doc = Document(
        code=code, revision=max(d.revision for d in existing) + 1,
        title=title if title is not None else base.title,
        body=body if body is not None else base.body,
        status=DocumentStatus.DRAFT, created_by=actor,
        **(_anchor_kwargs(anchors) if anchors is not None
           else {f"anchor_{k}": getattr(base, f"anchor_{k}") for k in ANCHOR_FIELDS}),
        **_walk_kwargs(base.kind, steps if steps is not None else base.steps_list(),
                       needs if needs is not None else base.needs),
    )
    session.add(doc)
    session.flush()
    audit.record(session, actor=actor, action="document.revised",
                 entity_type="document", entity_id=code,
                 before={"revision": base.revision}, after={"revision": doc.revision})
    return doc


def approve(session: Session, code: str, revision: int, actor: str = "system") -> Document:
    """Put a revision in force, superseding whatever it replaces."""
    doc = session.scalar(select(Document).where(
        Document.code == code, Document.revision == revision))
    if doc is None:
        raise NotFound(f"no document {code} revision {revision}")
    if doc.status != DocumentStatus.DRAFT:
        raise Invalid(f"{code} rev {revision} is {doc.status.value}, not a draft")

    superseded = current(session, code)
    if superseded is not None:
        superseded.status = DocumentStatus.SUPERSEDED

    doc.status = DocumentStatus.APPROVED
    doc.approved_by = actor
    doc.approved_at = utcnow()
    session.flush()
    audit.record(session, actor=actor, action="document.approved",
                 entity_type="document", entity_id=code,
                 before={"superseded_revision": superseded.revision if superseded else None},
                 after={"revision": revision})
    return doc


def withdraw(session: Session, code: str, actor: str = "system") -> Document:
    """Take the current instruction out of force without deleting anything."""
    doc = current(session, code)
    if doc is None:
        raise NotFound(f"no approved revision of {code}")
    doc.status = DocumentStatus.WITHDRAWN
    session.flush()
    audit.record(session, actor=actor, action="document.withdrawn",
                 entity_type="document", entity_id=code,
                 before={"revision": doc.revision})
    return doc


def approved(session: Session) -> list[Document]:
    """Every instruction currently in force.

    Distinct from for_anchor() with no arguments, which answers a narrower
    question - "what applies when nothing in particular is being done" - and
    correctly returns only the un-anchored, general instructions.
    """
    latest: dict[str, Document] = {}
    for doc in session.scalars(
        select(Document).where(Document.status == DocumentStatus.APPROVED)
                        .order_by(Document.code, Document.revision)
    ):
        latest[doc.code] = doc
    return list(latest.values())


def approved_walkthroughs(session: Session) -> list[Document]:
    """Every recorded walkthrough in force."""
    return [d for d in approved(session) if d.kind == "walkthrough"]


def for_anchor(session: Session, **anchors: str | None) -> list[Document]:
    """Approved instructions relevant to what someone is doing right now.

    Matching is deliberately generous: an instruction anchored only to a
    material is relevant to every characteristic of it, so a document matches
    when every anchor it *declares* agrees with what was asked. An instruction
    with no anchors at all is general and always relevant.
    """
    asked = {k: v for k, v in anchors.items() if v}
    out = []
    for doc in session.scalars(
        select(Document).where(Document.status == DocumentStatus.APPROVED)
                        .order_by(Document.code)
    ):
        declared = doc.anchors()
        if all(asked.get(field) == value for field, value in declared.items()):
            out.append(doc)
    # Most specific first: an instruction about this exact characteristic
    # should outrank the general one about the material.
    out.sort(key=lambda d: len(d.anchors()), reverse=True)
    return out


def catalogue(session: Session, include_drafts: bool = True) -> list[dict]:
    """Every document, one entry each, showing what is in force."""
    codes = [c for c in session.scalars(select(Document.code).distinct().order_by(Document.code))
             if not c.startswith("COA-")]  # certificates have their own screen
    entries = []
    for code in codes:
        in_force = current(session, code)
        open_draft = draft(session, code)
        if in_force is None and open_draft is not None and not include_drafts:
            continue
        head = in_force or open_draft
        entries.append({
            "code": code,
            "title": head.title,
            "kind": head.kind,
            "needs": head.needs,
            "anchors": head.anchors(),
            "approved_revision": in_force.revision if in_force else None,
            "approved_by": in_force.approved_by if in_force else None,
            "approved_at": in_force.approved_at if in_force else None,
            "draft_revision": open_draft.revision if open_draft else None,
            "drafted_by_model": head.drafted_by_model,
            "revisions": len(revisions(session, code)),
        })
    return entries
