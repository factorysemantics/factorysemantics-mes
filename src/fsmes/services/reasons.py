"""The downtime vocabulary: what this list is, and what only it knows.

The lifecycle itself - draft, validate, approve, retire, undo, and the rule
that **retiring a code changes what may be chosen next and never what was
chosen before** - lives in `fsmes.services.vocabulary`, where the
non-conformance severities read the same words. It was written here first,
for one table, and moved down when the second vocabulary arrived: two copies
of that rule would have been two copies that drift, and the one that drifts
is the one somebody reads.

What stays here is everything true of the downtime reasons and of no other
list: that what carries a code is a recorded equipment-state interval, that
the machines which have chosen a word are a fact the history already holds,
and that the list's own routes spell `drafts` and `vocabulary`.
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.domain import DowntimeReason, Equipment, EquipmentState
from fsmes.services import vocabulary as lifecycle

#: A code is lowercase, starts with a letter, and holds letters, digits and
#: underscores. It is grouped on, published, and compared across a fleet, so
#: it is spelled the way a topic segment is spelled rather than the way a
#: sentence is.
CODE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")

#: Segments the vocabulary's own routes already spell under
#: `/equipment/downtime-reasons/`. A code that spelled one of these would be
#: unreachable: `/equipment/downtime-reasons/drafts` is the drafts queue, so a
#: reason called `drafts` could never have its history read. Refused at
#: drafting time rather than discovered by a person who cannot open their own
#: reason.
RESERVED = ("drafts", "vocabulary")

#: The statuses a revision can be in when it is the one the plant is currently
#: living with. Exactly one revision of a code is in force at a time.
IN_FORCE = lifecycle.IN_FORCE


def intervals_labelled(session: Session, code: str) -> int:
    """How many equipment-state intervals carry this code, ever."""
    return session.scalar(
        select(func.count()).select_from(EquipmentState)
        .where(EquipmentState.reason_code == code)) or 0


def intervals_by_code(session: Session) -> dict[str, int]:
    """How many recorded intervals carry each code, in one query.

    The screen needs this for every row at once - it is what a person is told
    before they retire a code, rather than after the refusal - and a count per
    code would be one query per reason.
    """
    rows = session.execute(
        select(EquipmentState.reason_code, func.count())
        .where(EquipmentState.reason_code.is_not(None))
        .group_by(EquipmentState.reason_code)).all()
    return {code: count for code, count in rows}


#: What this vocabulary is. Everything generic about it is read from here by
#: `fsmes.services.vocabulary`; what is specific to downtime is the two
#: counters above and the two functions below.
VOCAB = lifecycle.Vocabulary(
    name="downtime_reason",
    noun="downtime reason",
    model=DowntimeReason,
    code=CODE,
    code_rule=(
        "A code is two to forty characters, starts with a lowercase letter, and "
        "holds lowercase letters, digits and underscores - it is grouped on and "
        "published, not a sentence."),
    route="/equipment/downtime-reasons",
    reserved=RESERVED,
    carried_field="labels_intervals",
    counted="recorded interval",
    carried=intervals_labelled,
    carried_by_code=intervals_by_code,
)


# --------------------------------------------------------------- reading it


def revisions(session: Session, code: str) -> list[DowntimeReason]:
    return lifecycle.revisions(session, VOCAB, code)


def in_force(session: Session, code: str) -> DowntimeReason | None:
    """The revision the plant is living with: approved, or retired."""
    return lifecycle.in_force(session, VOCAB, code)


def open_draft(session: Session, code: str) -> DowntimeReason | None:
    """The draft waiting on somebody, if there is one. At most one per code."""
    return lifecycle.open_draft(session, VOCAB, code)


def approved(session: Session) -> list[DowntimeReason]:
    """The vocabulary in force, in code order.

    A retired code is not here - that is what retiring it did - and its
    intervals keep their labels regardless.
    """
    return lifecycle.approved(session, VOCAB)


def catalog(session: Session) -> dict[str, str]:
    """`{code: sentence}` for the approved vocabulary - the shape the screens
    already read `/triggers/catalog` in."""
    return lifecycle.catalog(session, VOCAB)


def names(session: Session) -> dict[str, str]:
    """`{code: name}` for the approved vocabulary - what the button says."""
    return lifecycle.names(session, VOCAB)


def labels(session: Session) -> dict[str, str]:
    """`{code: name}` for every code the plant has ever put in force, retired
    ones included.

    What the analysis reads. A retired code still labels the intervals it
    labelled, so a pareto that only knew the approved list would print the
    bare code for them - the name they were given when somebody chose them is
    the honest label, and it stops changing the moment the code is retired.
    """
    return lifecycle.labels(session, VOCAB)


def drafts(session: Session, limit: int = 50, offset: int = 0
           ) -> tuple[list[DowntimeReason], int]:
    """Every draft waiting on somebody, oldest first, and how many there are."""
    return lifecycle.drafts(session, VOCAB, limit=limit, offset=offset)


def machines_labelling(session: Session, code: str) -> list[tuple[str, int]]:
    """Which machines have recorded a stop under this code, and how many each.

    Most first, then by machine code, so the answer is the same every time it
    is asked. This is what makes a word in the vocabulary point at somewhere
    real: the list itself is plant-wide, but the floor that actually chooses a
    word is a fact the history already holds, and a screen that wants to show
    an approver the effect of a change has to stand somewhere.

    An empty list is an answer, not a gap: a code nobody has ever chosen has
    no machine behind it, and guessing one would be inventing a place.
    """
    rows = session.execute(
        select(Equipment.code, func.count())
        .join(EquipmentState, EquipmentState.equipment_id == Equipment.id)
        .where(EquipmentState.reason_code == code)
        .group_by(Equipment.code)).all()
    return sorted(((machine, count) for machine, count in rows),
                  key=lambda row: (-row[1], row[0]))


def vocabulary(session: Session) -> list[dict]:
    """Every code the plant has ever had, in code order: the revision in force,
    the draft waiting on somebody, and how much history carries the code.

    The catalogue answers *what may an operator choose*, which is the approved
    list and nothing else. This answers *what is the plant's vocabulary*, which
    includes the retired words it still reads in its own history and the drafts
    nobody has signed.
    """
    return lifecycle.vocabulary(session, VOCAB)


# --------------------------------------------------------------- writing it


def define(session: Session, *, code: str, name: str, description: str = "",
           retires: bool = False, labels_intervals: int | None = None,
           actor: str = "system") -> DowntimeReason:
    """Draft a reason: a new code, a change to one, or its retirement.

    One verb, because from the plant's side they are one act - *here is what I
    think the list should say next* - and all three wait for the same person.
    An open draft is edited in place: nobody is choosing from it yet.
    """
    return lifecycle.define(
        session, VOCAB, code=code, name=name, description=description,
        retires=retires, labels_records=labels_intervals, actor=actor)


def approve(session: Session, code: str, revision: int,
            actor: str = "system") -> DowntimeReason:
    """Put one revision in force, superseding whatever the plant had.

    Any revision, not only a draft: approving the previous one is how a
    vocabulary change is undone, and an undo that had to be re-drafted first
    is an undo nobody reaches for in the ten minutes it matters.
    """
    return lifecycle.approve(session, VOCAB, code, revision, actor=actor)


def out(row: DowntimeReason) -> dict:
    return lifecycle.out(VOCAB, row)
