"""The lifecycle every vocabulary this plant authors has: draft, validate,
approve, retire, undo.

Written for the downtime reasons and then asked to take a second list — the
non-conformance severities. That is the test decision
[0035](../../docs/decisions/0035-configuration-is-authored-by-roles-and-selected-by-operators.md)
set for itself: *"if the loop is right, the second vocabulary is a small
change."* Most of it was: the catalogue shape, the drafts queue, the
pending-approvals panel, the review and the walk all took a second kind
without being touched. The part that was not is this file. Draft, approve,
retire and undo were one concrete implementation over one table, so the
second vocabulary would have been the same three hundred lines with two
nouns changed — and two copies of *"retiring never relabels what was chosen
before"* are two copies that drift, one of which somebody will fix.

So the rules live here once, over any vocabulary that names itself in a
`Vocabulary`:

* **A draft changes nothing.** It is a row with `status = draft`. Nothing on
  the floor offers it and nothing groups on it.
* **A draft never goes live by itself.** Somebody holding the approving
  capability puts it in force, and the row records who and when.
* **Validation happens before approval, not after.** A code is unique,
  topic-safe, and not a term the product already owns — the same
  `protected_terms()` the pack checker reads, so a capability, a state word
  or a KPI name cannot become one of the plant's own words and quietly mean
  two things.
* **Retiring a code never relabels what was chosen before**, and may not be
  done blind: a code that labels live records leaves the list only when the
  draft says how many records carry it.
* **Undo is one move.** Approve the previous revision. Nothing is deleted and
  no history is rewritten.

What is *not* here is anything either vocabulary knows that the other does
not: which machines have recorded a downtime reason, which screen a severity
is read on, what the product's own code paths write. Those stay in
`fsmes.services.reasons` and `fsmes.services.severities`, which are what the
routers and the review call.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain.common import VocabularyStatus
from fsmes.services import Conflict, Invalid, NotFound, audit

#: The statuses a revision can be in when it is the one the plant is
#: currently living with. Exactly one revision of a code is in force at a
#: time: approved, or retired.
IN_FORCE = (VocabularyStatus.APPROVED, VocabularyStatus.RETIRED)


@dataclass(frozen=True)
class Vocabulary:
    """One list a plant authors, and everything that is true of that list and
    of no other.

    A second vocabulary is one of these plus the two questions only it can
    answer: what carries its codes, and what the product itself writes.
    """

    name: str
    """The kind name: `downtime_reason`, `nc_severity`. It is what the audit
    actions are prefixed with and what the approvals panel calls it."""

    noun: str
    """What it is in a sentence a person reads: *downtime reason*."""

    model: type
    """The ORM class. One revision of one code per row."""

    code: re.Pattern[str]
    """What a code may be spelled like. Different lists have different
    lengths to live within, so each states its own."""

    code_rule: str
    """The same rule in the words a person reading a refusal needs."""

    route: str
    """The path its own revisions hang under, e.g.
    `/equipment/downtime-reasons`. Used only to explain why a reserved
    segment is refused."""

    reserved: tuple[str, ...] = ()
    """Segments this vocabulary's own routes already spell under `route`. A
    code spelling one of them would be unreachable — `/drafts` is the drafts
    queue, so a word called `drafts` could never have its history read.
    Refused at drafting time rather than discovered by a person who cannot
    open their own word."""

    carried_field: str = "labels_records"
    """The column holding what the drafter said about how much history
    carries the code. Named on each table for what it actually counts."""

    counted: str = "record"
    """The singular noun for what carries a code: *recorded interval*,
    *non-conformance*. The server owns the plural too — a panel that says
    "1 non-conformances" is a panel somebody stops trusting about the numbers
    that matter."""

    carried: Callable[[Session, str], int] = lambda session, code: 0
    """How many records carry one code, ever."""

    carried_by_code: Callable[[Session], dict[str, int]] = lambda session: {}
    """The same count for every code at once. The screen needs it per row,
    and a count per code would be one query per word."""

    product_writes: tuple[tuple[str, str], ...] = ()
    """Codes the product's own code paths write, each with the sentence
    saying where. A plant may rename and describe these; it may not retire
    one, because the refusal would land on a machine raising a hold rather
    than on the person who made the list. See `_product_writes`."""


def count(number: int, noun: str) -> str:
    """`1 non-conformance`, `2 non-conformances`."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


# --------------------------------------------------------------- reading it


def revisions(session: Session, vocab: Vocabulary, code: str) -> list[Any]:
    return list(session.scalars(
        select(vocab.model).where(vocab.model.code == code)
        .order_by(vocab.model.revision)))


def in_force(session: Session, vocab: Vocabulary, code: str) -> Any | None:
    """The revision the plant is living with: approved, or retired."""
    return session.scalar(
        select(vocab.model)
        .where(vocab.model.code == code, vocab.model.status.in_(IN_FORCE))
        .order_by(vocab.model.revision.desc()).limit(1))


def open_draft(session: Session, vocab: Vocabulary, code: str) -> Any | None:
    """The draft waiting on somebody, if there is one. At most one per code."""
    return session.scalar(
        select(vocab.model)
        .where(vocab.model.code == code,
               vocab.model.status == VocabularyStatus.DRAFT)
        .order_by(vocab.model.revision.desc()).limit(1))


def approved(session: Session, vocab: Vocabulary) -> list[Any]:
    """The list in force, in code order.

    A retired code is not here - that is what retiring it did - and the
    records it labelled keep their labels regardless.
    """
    return list(session.scalars(
        select(vocab.model)
        .where(vocab.model.status == VocabularyStatus.APPROVED)
        .order_by(vocab.model.code)))


def catalog(session: Session, vocab: Vocabulary) -> dict[str, str]:
    """`{code: sentence}` for the approved list - the shape the screens
    already read `/triggers/catalog` in.

    Server-owned on purpose. The only selects in this product's reason family
    before the first vocabulary existed were hardcoded `<option>` blocks
    duplicating a Python enum in HTML *and* in JavaScript, and one of them had
    already drifted.
    """
    return {row.code: (row.description or row.name)
            for row in approved(session, vocab)}


def names(session: Session, vocab: Vocabulary) -> dict[str, str]:
    """`{code: name}` for the approved list - what the screen shows."""
    return {row.code: row.name for row in approved(session, vocab)}


def labels(session: Session, vocab: Vocabulary) -> dict[str, str]:
    """`{code: name}` for every code the plant has ever put in force, retired
    ones included.

    What a report reads. A retired code still labels what it labelled, so a
    screen that only knew the approved list would print the bare code for
    those - the name they were given when somebody chose them is the honest
    label, and it stops changing the moment the code is retired.
    """
    rows = session.scalars(
        select(vocab.model).where(vocab.model.status.in_(IN_FORCE))
        .order_by(vocab.model.code, vocab.model.revision))
    return {row.code: row.name for row in rows}


def drafts(session: Session, vocab: Vocabulary, limit: int = 50, offset: int = 0
           ) -> tuple[list[Any], int]:
    """Every draft waiting on somebody, oldest first, and how many there are.

    Oldest first because the column that matters on the panel is how long a
    draft has waited, and a queue sorted newest-first hides exactly the row a
    person needs to see. The count is of the whole queue, not of the page -
    a deep queue that reads short is the failure the panel exists to fix.
    """
    where = vocab.model.status == VocabularyStatus.DRAFT
    total = session.scalar(
        select(func.count()).select_from(vocab.model).where(where)) or 0
    rows = list(session.scalars(
        select(vocab.model).where(where)
        .order_by(vocab.model.created_at, vocab.model.code)
        .limit(limit).offset(offset)))
    return rows, total


def vocabulary(session: Session, vocab: Vocabulary) -> list[dict]:
    """Every code the plant has ever had, in code order: the revision in
    force, the draft waiting on somebody, and how much history carries it.

    The catalogue answers *what may be chosen*, which is the approved list
    and nothing else. This answers *what is the plant's vocabulary*, which
    includes the retired words it still reads in its own history and the
    drafts nobody has signed - the three states an authoring screen has to
    show at once, and the reason a screen reading the catalogue alone would
    show a person a list their own draft was missing from.
    """
    everything = list(session.scalars(
        select(vocab.model).order_by(vocab.model.code, vocab.model.revision)))
    carried = vocab.carried_by_code(session)

    by_code: dict[str, list[Any]] = {}
    for row in everything:
        by_code.setdefault(row.code, []).append(row)

    out_rows = []
    for code, rows in by_code.items():
        live = next((r for r in reversed(rows) if r.status in IN_FORCE), None)
        draft = next((r for r in reversed(rows)
                      if r.status is VocabularyStatus.DRAFT), None)
        out_rows.append({
            "code": code,
            "in_force": out(vocab, live) if live else None,
            "draft": out(vocab, draft) if draft else None,
            # Never None: a code nothing carries carries nothing, which is a
            # measurement, not an unknown. Named for what it counts, which is
            # why the key is the table's own - `labels_intervals` for the
            # downtime reasons, `labels_records` for the severities.
            vocab.carried_field: carried.get(code, 0),
            "revisions": len(rows),
        })
    return out_rows


# --------------------------------------------------------------- writing it


def _validate_code(vocab: Vocabulary, code: str) -> None:
    from fsmes.pack.check import protected_terms

    if not vocab.code.match(code or ""):
        raise Invalid(f"{code!r} is not a {vocab.noun} code. {vocab.code_rule}")
    if code in vocab.reserved:
        raise Invalid(
            f"{code!r} is one of this list's own addresses "
            f"({vocab.route}/{code}), so a {vocab.noun} spelled that way could "
            "never be opened again. Any other word is free.")
    owned = protected_terms().get(code)
    if owned:
        raise Invalid(
            f"{code!r} is already {owned} in this product. A {vocab.noun} that "
            "spells the same word as something the product owns means two "
            "things at once, and a fleet comparing two plants cannot tell "
            "which.")


def _product_writes(vocab: Vocabulary, code: str) -> str | None:
    """The sentence saying that the product itself writes this code, or None.

    A plant owns its own words, and this is the one edge of that: the
    product has code paths that open a record with a particular severity, and
    a list those paths cannot write to would refuse at the worst possible
    moment - a machine raising a quality hold, with nobody watching. So the
    refusal is moved to the only place a person is present: the moment
    somebody drafts the retirement.

    It is a refusal to *retire*, never to rename. The code is the key; the
    name and the sentence beside it are the plant's, and always were.
    """
    return dict(vocab.product_writes).get(code)


def define(session: Session, vocab: Vocabulary, *, code: str, name: str,
           description: str = "", retires: bool = False,
           labels_records: int | None = None,
           actor: str = "system") -> Any:
    """Draft a word: a new code, a change to one, or its retirement.

    One verb, because from the plant's side they are one act - *here is what
    I think the list should say next* - and all three wait for the same
    person. An open draft is edited in place: nobody is choosing from it yet.
    """
    _validate_code(vocab, code)
    if not (name or "").strip():
        raise Invalid(f"{code} needs a name: it is what a person reads beside the record.")

    existing = revisions(session, vocab, code)
    live = in_force(session, vocab, code)
    if retires:
        if live is None or live.status is not VocabularyStatus.APPROVED:
            raise Invalid(f"{code} is not on the list, so there is nothing to retire.")
        written = _product_writes(vocab, code)
        if written:
            raise Invalid(
                f"{code} is one of the codes this product writes itself: {written}. "
                "Retiring it would leave that code path with a word the plant no "
                "longer has, and it would find out at the moment it opened a "
                "record rather than here. Rename it and describe it in your own "
                "words instead - the code is the key, the name is yours.")
        carried = vocab.carried(session, code)
        if carried and labels_records is None:
            raise Invalid(
                f"{code} labels {count(carried, vocab.counted)}. Say so in the "
                "draft (`labels_records`) before retiring it: a code leaves the "
                "list with somebody having looked at what it already labels, or "
                "it leaves it blind. The records keep their label either way.")
        if labels_records is None:
            labels_records = carried

    draft = open_draft(session, vocab, code)
    if draft is not None:
        draft.name, draft.description = name, description
        draft.retires = retires
        setattr(draft, vocab.carried_field, labels_records)
        draft.created_by = actor
        draft.on_behalf_of = getattr(actor, "on_behalf_of", None)
        draft.created_at = utcnow()
        session.flush()
        audit.record(session, actor=actor, action=f"{vocab.name}.draft_edited",
                     entity_type=vocab.name, entity_id=code,
                     after={"revision": draft.revision, "name": name, "retires": retires})
        return draft

    row = vocab.model(
        code=code, revision=(max(r.revision for r in existing) + 1) if existing else 1,
        name=name, description=description, status=VocabularyStatus.DRAFT,
        retires=retires, created_by=actor,
        on_behalf_of=getattr(actor, "on_behalf_of", None),
        **{vocab.carried_field: labels_records})
    session.add(row)
    session.flush()
    audit.record(session, actor=actor, action=f"{vocab.name}.drafted",
                 entity_type=vocab.name, entity_id=code,
                 after={"revision": row.revision, "name": name, "retires": retires})
    return row


def approve(session: Session, vocab: Vocabulary, code: str, revision: int,
            actor: str = "system") -> Any:
    """Put one revision in force, superseding whatever the plant had.

    Any revision, not only a draft: approving the previous one is how a
    vocabulary change is undone, and an undo that had to be re-drafted first
    is an undo nobody reaches for in the ten minutes it matters.
    """
    row = session.scalar(select(vocab.model).where(
        vocab.model.code == code, vocab.model.revision == revision))
    if row is None:
        raise NotFound(f"no {vocab.noun} {code} revision {revision}")

    was = in_force(session, vocab, code)
    if was is not None and was.id == row.id:
        raise Conflict(f"{code} revision {revision} is already the one in force.")

    if row.retires:
        carried = vocab.carried(session, code)
        if carried and getattr(row, vocab.carried_field) is None:
            raise Invalid(
                f"{code} labels {count(carried, vocab.counted)} and the draft says "
                "nothing about them. It is not retired.")

    if was is not None:
        was.status = VocabularyStatus.SUPERSEDED
    row.status = (VocabularyStatus.RETIRED if row.retires
                  else VocabularyStatus.APPROVED)
    row.approved_by = actor
    row.approved_at = utcnow()
    session.flush()
    audit.record(session, actor=actor, action=f"{vocab.name}.approved",
                 entity_type=vocab.name, entity_id=code,
                 before={"revision": was.revision, "status": was.status.value} if was else None,
                 after={"revision": revision, "status": row.status.value,
                        "retires": row.retires})
    return row


def out(vocab: Vocabulary, row: Any) -> dict:
    """One revision, as the API says it.

    The count of what carries the code goes out under the name the table
    gives it, because a screen that read `labels_records` for one list and
    `labels_intervals` for the other would be two screens.
    """
    return {
        "code": row.code,
        "revision": row.revision,
        "name": row.name,
        "description": row.description,
        "status": row.status.value,
        "retires": row.retires,
        vocab.carried_field: getattr(row, vocab.carried_field),
        "created_by": row.created_by,
        "created_at": row.created_at,
        "on_behalf_of": row.on_behalf_of,
        "approved_by": row.approved_by,
        "approved_at": row.approved_at,
    }
