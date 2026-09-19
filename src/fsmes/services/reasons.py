"""The downtime vocabulary's lifecycle: draft, validate, approve, retire, undo.

The rules that make this a controlled list rather than a table somebody edits:

* **A draft changes nothing on the floor.** It is a row with `status = draft`.
  The station screen never offers it and the pareto never groups on it.
* **A draft never goes live by itself.** Somebody holding `process.approve`
  puts it in force, and the row records who and when.
* **Validation happens before approval, not after.** A code is unique,
  topic-safe, and not a term the product already owns - the same
  `protected_terms()` the pack checker reads, so a capability, a state word or
  a KPI name cannot become a downtime reason and quietly mean two things.
* **Retiring a code never relabels what was chosen before**, and may not be
  done blind: a code that labels live intervals leaves the list only when the
  draft says how many intervals carry it.
* **Undo is one move.** Approve the previous revision. Nothing is deleted and
  no history is rewritten.
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import DowntimeReason, DowntimeReasonStatus, EquipmentState
from fsmes.services import Conflict, Invalid, NotFound, audit

#: A code is lowercase, starts with a letter, and holds letters, digits and
#: underscores. It is grouped on, published, and compared across a fleet, so
#: it is spelled the way a topic segment is spelled rather than the way a
#: sentence is.
CODE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")

#: The statuses a revision can be in when it is the one the plant is currently
#: living with. Exactly one revision of a code is in force at a time.
IN_FORCE = (DowntimeReasonStatus.APPROVED, DowntimeReasonStatus.RETIRED)


# --------------------------------------------------------------- reading it


def revisions(session: Session, code: str) -> list[DowntimeReason]:
    return list(session.scalars(
        select(DowntimeReason).where(DowntimeReason.code == code)
        .order_by(DowntimeReason.revision)))


def in_force(session: Session, code: str) -> DowntimeReason | None:
    """The revision the plant is living with: approved, or retired."""
    return session.scalar(
        select(DowntimeReason)
        .where(DowntimeReason.code == code, DowntimeReason.status.in_(IN_FORCE))
        .order_by(DowntimeReason.revision.desc()).limit(1))


def open_draft(session: Session, code: str) -> DowntimeReason | None:
    """The draft waiting on somebody, if there is one. At most one per code."""
    return session.scalar(
        select(DowntimeReason)
        .where(DowntimeReason.code == code,
               DowntimeReason.status == DowntimeReasonStatus.DRAFT)
        .order_by(DowntimeReason.revision.desc()).limit(1))


def approved(session: Session) -> list[DowntimeReason]:
    """The vocabulary in force, in code order.

    A retired code is not here - that is what retiring it did - and its
    intervals keep their labels regardless.
    """
    return list(session.scalars(
        select(DowntimeReason)
        .where(DowntimeReason.status == DowntimeReasonStatus.APPROVED)
        .order_by(DowntimeReason.code)))


def catalog(session: Session) -> dict[str, str]:
    """`{code: sentence}` for the approved vocabulary - the shape the screens
    already read `/triggers/catalog` in.

    Server-owned on purpose. The only two selects in the reason family before
    this one were hardcoded `<option>` blocks duplicating a Python enum in
    HTML *and* in JavaScript, and one of them had already drifted.
    """
    return {row.code: (row.description or row.name) for row in approved(session)}


def names(session: Session) -> dict[str, str]:
    """`{code: name}` for the approved vocabulary - what the button says."""
    return {row.code: row.name for row in approved(session)}


def labels(session: Session) -> dict[str, str]:
    """`{code: name}` for every code the plant has ever put in force, retired
    ones included.

    What the analysis reads. A retired code still labels the intervals it
    labelled, so a pareto that only knew the approved list would print the
    bare code for them - the name they were given when somebody chose them is
    the honest label, and it stops changing the moment the code is retired.
    """
    rows = session.scalars(
        select(DowntimeReason).where(DowntimeReason.status.in_(IN_FORCE))
        .order_by(DowntimeReason.code, DowntimeReason.revision))
    return {row.code: row.name for row in rows}


def drafts(session: Session, limit: int = 50, offset: int = 0
           ) -> tuple[list[DowntimeReason], int]:
    """Every draft waiting on somebody, oldest first, and how many there are.

    Oldest first because the column that matters on the panel is how long a
    draft has waited, and a queue sorted newest-first hides exactly the row a
    person needs to see. The count is of the whole queue, not of the page -
    a deep queue that reads short is the failure the panel exists to fix.
    """
    where = DowntimeReason.status == DowntimeReasonStatus.DRAFT
    total = session.scalar(
        select(func.count()).select_from(DowntimeReason).where(where)) or 0
    rows = list(session.scalars(
        select(DowntimeReason).where(where)
        .order_by(DowntimeReason.created_at, DowntimeReason.code)
        .limit(limit).offset(offset)))
    return rows, total


def intervals_labelled(session: Session, code: str) -> int:
    """How many equipment-state intervals carry this code, ever."""
    return session.scalar(
        select(func.count()).select_from(EquipmentState)
        .where(EquipmentState.reason_code == code)) or 0


# --------------------------------------------------------------- writing it


def _validate_code(session: Session, code: str) -> None:
    from fsmes.pack.check import protected_terms

    if not CODE.match(code or ""):
        raise Invalid(
            f"{code!r} is not a downtime code. A code is two to forty characters, "
            "starts with a lowercase letter, and holds lowercase letters, digits "
            "and underscores - it is grouped on and published, not a sentence.")
    owned = protected_terms().get(code)
    if owned:
        raise Invalid(
            f"{code!r} is already {owned} in this product. A downtime reason that "
            "spells the same word as something the product owns means two things "
            "at once, and a fleet comparing two plants cannot tell which.")


def define(session: Session, *, code: str, name: str, description: str = "",
           retires: bool = False, labels_intervals: int | None = None,
           actor: str = "system") -> DowntimeReason:
    """Draft a reason: a new code, a change to one, or its retirement.

    One verb, because from the plant's side they are one act - *here is what I
    think the list should say next* - and all three wait for the same person.
    An open draft is edited in place: nobody is choosing from it yet.
    """
    _validate_code(session, code)
    if not (name or "").strip():
        raise Invalid(f"{code} needs a name: it is what the operator reads on the button.")

    existing = revisions(session, code)
    live = in_force(session, code)
    if retires:
        if live is None or live.status is not DowntimeReasonStatus.APPROVED:
            raise Invalid(f"{code} is not on the list, so there is nothing to retire.")
        carried = intervals_labelled(session, code)
        if carried and labels_intervals is None:
            raise Invalid(
                f"{code} labels {carried} recorded intervals. Say so in the draft "
                "(`labels_intervals`) before retiring it: a code leaves the list "
                "with somebody having looked at what it already labels, or it "
                "leaves it blind. The intervals keep their label either way.")
        if labels_intervals is None:
            labels_intervals = carried

    draft = open_draft(session, code)
    if draft is not None:
        draft.name, draft.description = name, description
        draft.retires, draft.labels_intervals = retires, labels_intervals
        draft.created_by = actor
        draft.on_behalf_of = getattr(actor, "on_behalf_of", None)
        draft.created_at = utcnow()
        session.flush()
        audit.record(session, actor=actor, action="downtime_reason.draft_edited",
                     entity_type="downtime_reason", entity_id=code,
                     after={"revision": draft.revision, "name": name, "retires": retires})
        return draft

    row = DowntimeReason(
        code=code, revision=(max(r.revision for r in existing) + 1) if existing else 1,
        name=name, description=description, status=DowntimeReasonStatus.DRAFT,
        retires=retires, labels_intervals=labels_intervals, created_by=actor,
        on_behalf_of=getattr(actor, "on_behalf_of", None))
    session.add(row)
    session.flush()
    audit.record(session, actor=actor, action="downtime_reason.drafted",
                 entity_type="downtime_reason", entity_id=code,
                 after={"revision": row.revision, "name": name, "retires": retires})
    return row


def approve(session: Session, code: str, revision: int,
            actor: str = "system") -> DowntimeReason:
    """Put one revision in force, superseding whatever the plant had.

    Any revision, not only a draft: approving the previous one is how a
    vocabulary change is undone, and an undo that had to be re-drafted first
    is an undo nobody reaches for in the ten minutes it matters.
    """
    row = session.scalar(select(DowntimeReason).where(
        DowntimeReason.code == code, DowntimeReason.revision == revision))
    if row is None:
        raise NotFound(f"no downtime reason {code} revision {revision}")

    was = in_force(session, code)
    if was is not None and was.id == row.id:
        raise Conflict(f"{code} revision {revision} is already the one in force.")

    if row.retires:
        carried = intervals_labelled(session, code)
        if carried and row.labels_intervals is None:
            raise Invalid(
                f"{code} labels {carried} recorded intervals and the draft says "
                "nothing about them. It is not retired.")

    if was is not None:
        was.status = DowntimeReasonStatus.SUPERSEDED
    row.status = (DowntimeReasonStatus.RETIRED if row.retires
                  else DowntimeReasonStatus.APPROVED)
    row.approved_by = actor
    row.approved_at = utcnow()
    session.flush()
    audit.record(session, actor=actor, action="downtime_reason.approved",
                 entity_type="downtime_reason", entity_id=code,
                 before={"revision": was.revision, "status": was.status.value} if was else None,
                 after={"revision": revision, "status": row.status.value,
                        "retires": row.retires})
    return row


def out(row: DowntimeReason) -> dict:
    return {
        "code": row.code,
        "revision": row.revision,
        "name": row.name,
        "description": row.description,
        "status": row.status.value,
        "retires": row.retires,
        "labels_intervals": row.labels_intervals,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "on_behalf_of": row.on_behalf_of,
        "approved_by": row.approved_by,
        "approved_at": row.approved_at,
    }
