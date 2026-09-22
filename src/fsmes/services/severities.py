"""The non-conformance severity vocabulary: what this list is, and what only
it knows.

The lifecycle is `fsmes.services.vocabulary`, the same one the downtime
reasons read. What is here is the three things that are true of severities
and of no other list.

**What carries a code is a non-conformance.** Not an interval, not a stop: a
record somebody has to work through review and a disposition
(decision [0024](../../docs/decisions/0024-a-nonconformance-has-a-life.md)).
Retiring a severity never relabels one - a hold raised as `major` reads
`major` for the rest of its life and prints `major` on the certificate that
already went to a customer.

**Two codes the product writes itself.** `services/quality.py` opens a minor
non-conformance when a recorded check falls outside its specification, and
`services/spc.py` opens a major one for rule 1 and a minor one for the other
three. A plant may rename both, describe both in its own words, and add as
many of its own beside them as it likes - but it may not *retire* one,
because the refusal would land on a machine raising a quality hold with
nobody watching rather than on the person editing the list. The refusal is
moved to the only moment a person is present.

**Free text until there is a list, a list once there is one.** A plant that
has approved no severity keeps exactly the behaviour it has today: the column
is a `String(20)` and whatever is written is stored. The moment a plant has
one approved severity, a severity written into a new non-conformance must be
a code from it, and **no existing row is touched**. That is the same shape
the downtime vocabulary has on the station screen: where the list does not
yet exist, nothing changes at all.
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.domain import NcSeverity, NonConformance
from fsmes.services import Invalid
from fsmes.services import vocabulary as lifecycle

#: A severity code is spelled the way a downtime code is - lowercase, starting
#: with a letter, letters, digits and underscores - and **twenty characters,
#: not forty**: it is stored on `NonConformance.severity`, which is a
#: `String(20)` on every plant that already exists. A list that could approve
#: a word too long to store would refuse at the one moment it mattered.
CODE = re.compile(r"^[a-z][a-z0-9_]{1,19}$")

#: Segments this list's own routes already spell under `/quality/severities/`.
#: A code spelling one of them could never have its history opened.
RESERVED = ("drafts", "vocabulary")

#: What the product's own code paths write, and where. A plant may rename
#: these and describe them; retiring one is refused, with this sentence.
#:
#: These two are exactly the two words in the source today, which is rule one
#: of the audit: the literal that is shipped becomes the default, unchanged.
#: Nothing invents a third - `critical` and `observation` are words some plants
#: use and this product has never written, and shipping them to be helpful
#: would be inventing a plant.
PRODUCT_WRITES: tuple[tuple[str, str], ...] = (
    ("minor", "a recorded check outside its specification opens one "
              "(`services/quality.py`), and so does an SPC rule other than "
              "rule 1 (`services/spc.py`)"),
    ("major", "SPC rule 1 - a point beyond three sigma - opens one "
              "(`services/spc.py`)"),
)


def records_labelled(session: Session, code: str) -> int:
    """How many non-conformances carry this severity, ever."""
    return session.scalar(
        select(func.count()).select_from(NonConformance)
        .where(NonConformance.severity == code)) or 0


def records_by_code(session: Session) -> dict[str, int]:
    """How many non-conformances carry each severity, in one query.

    The screen needs it for every row at once - it is what a person is told
    before they retire a word, rather than after the refusal.
    """
    rows = session.execute(
        select(NonConformance.severity, func.count())
        .where(NonConformance.severity.is_not(None))
        .group_by(NonConformance.severity)).all()
    return {code: count for code, count in rows}


#: What this vocabulary is.
VOCAB = lifecycle.Vocabulary(
    name="nc_severity",
    noun="non-conformance severity",
    model=NcSeverity,
    code=CODE,
    code_rule=(
        "A code is two to twenty characters, starts with a lowercase letter, and "
        "holds lowercase letters, digits and underscores - it is stored on every "
        "non-conformance and printed on certificates, not read as a sentence."),
    route="/quality/severities",
    reserved=RESERVED,
    carried_field="labels_records",
    counted="non-conformance",
    carried=records_labelled,
    carried_by_code=records_by_code,
    product_writes=PRODUCT_WRITES,
)


# --------------------------------------------------------------- reading it


def revisions(session: Session, code: str) -> list[NcSeverity]:
    return lifecycle.revisions(session, VOCAB, code)


def in_force(session: Session, code: str) -> NcSeverity | None:
    """The revision the plant is living with: approved, or retired."""
    return lifecycle.in_force(session, VOCAB, code)


def open_draft(session: Session, code: str) -> NcSeverity | None:
    """The draft waiting on somebody, if there is one."""
    return lifecycle.open_draft(session, VOCAB, code)


def approved(session: Session) -> list[NcSeverity]:
    """The severities in force, in code order."""
    return lifecycle.approved(session, VOCAB)


def catalog(session: Session) -> dict[str, str]:
    """`{code: sentence}` for the approved severities - the shape every
    server-owned list in this product is read in."""
    return lifecycle.catalog(session, VOCAB)


def names(session: Session) -> dict[str, str]:
    """`{code: name}` for the approved severities."""
    return lifecycle.names(session, VOCAB)


def labels(session: Session) -> dict[str, str]:
    """`{code: name}` for every severity the plant has ever put in force,
    retired ones included.

    What a screen reads to print a word beside a record. A retired severity
    still labels the non-conformances it labelled, and the name it was given
    when somebody chose it is the honest label for them.
    """
    return lifecycle.labels(session, VOCAB)


def drafts(session: Session, limit: int = 50, offset: int = 0
           ) -> tuple[list[NcSeverity], int]:
    """Every severity draft waiting on somebody, oldest first, with the whole
    queue's total."""
    return lifecycle.drafts(session, VOCAB, limit=limit, offset=offset)


def vocabulary(session: Session) -> list[dict]:
    """Every severity code the plant has ever had, in code order."""
    return lifecycle.vocabulary(session, VOCAB)


def has_vocabulary(session: Session) -> bool:
    """Whether this plant has approved any severity at all.

    The one question the write path asks. False is the plant this product
    shipped with, and on it nothing changes: the column takes what it is
    given, exactly as it has since the table was written.
    """
    return session.scalar(
        select(func.count()).select_from(NcSeverity)
        .where(NcSeverity.status == lifecycle.VocabularyStatus.APPROVED)) > 0


def validate(session: Session, severity: str) -> str:
    """The severity a new non-conformance will be written with, or a refusal
    naming the list it was not on.

    Called on the write path and nowhere else. **Existing rows are never read
    and never rewritten** - a non-conformance raised last March under a word
    this plant has since stopped using keeps that word, which is the same rule
    the downtime vocabulary keeps about an interval it labelled.
    """
    if not has_vocabulary(session):
        return severity
    on_the_list = catalog(session)
    if severity in on_the_list:
        return severity
    offered = ", ".join(sorted(on_the_list)) or "nothing"
    raise Invalid(
        f"{severity!r} is not one of this plant's non-conformance severities. "
        f"It has {lifecycle.count(len(on_the_list), 'severity')} on the list: "
        f"{offered}. Records already raised under another word keep it; what is "
        "refused is writing a new one.")


# --------------------------------------------------------------- writing it


def define(session: Session, *, code: str, name: str, description: str = "",
           retires: bool = False, labels_records: int | None = None,
           actor: str = "system") -> NcSeverity:
    """Draft a severity: a new code, a change to one, or its retirement."""
    return lifecycle.define(
        session, VOCAB, code=code, name=name, description=description,
        retires=retires, labels_records=labels_records, actor=actor)


def approve(session: Session, code: str, revision: int,
            actor: str = "system") -> NcSeverity:
    """Put one revision in force. Undo is this with the previous number."""
    return lifecycle.approve(session, VOCAB, code, revision, actor=actor)


def out(row: NcSeverity) -> dict:
    return lifecycle.out(VOCAB, row)
