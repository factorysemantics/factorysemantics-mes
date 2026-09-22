"""The plant's non-conformance severities: how bad, in this plant's own words.

`NonConformance.severity` has been a `String(20)` defaulting to `"minor"`
since the table was written (`fsmes/domain/quality.py`). Nothing validated
it, nothing listed the words it could hold, and the only two values in the
product are the two its own code writes: `services/quality.py` opens a minor
one when a check falls out of specification, and `services/spc.py` opens a
major one for rule 1 and a minor one for the rest. A trigger could write any
string at all, and its catalogue sentence advertised a third word,
`critical`, that nothing in the product has ever written. That is free text
by accident rather than by design, and the audit of 2026-09-21 listed it as
**Q3**.

This is the second vocabulary in this product, after the downtime reasons,
and it is deliberately the same thing again: the same four statuses, the
same draft → approve → supersede → retire lifecycle, the same rule that
**retiring a code changes what may be chosen next and never what was chosen
before.** A non-conformance raised as `major` still reads `major` after the
plant stops offering the word, and the certificate that printed it goes on
printing it.

What the severity vocabulary does *not* do, and it is the difference from
the downtime reasons: it does not decide which readings raise a hold, and it
does not rank. The product writes two codes and the plant may rename them,
describe them, and add its own beside them. A plant that wants a third word
between `minor` and `major` writes one; ordering a triage queue by it is the
plant's own reading of its own list, and inventing a numeric rank here would
be asserting that one plant's `major` is another plant's `major`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fsmes.db import Base, utcnow
from fsmes.domain.common import VocabularyStatus, str_enum

#: The statuses, shared with the downtime vocabulary rather than spelled
#: again. See `fsmes.domain.common.VocabularyStatus`.
NcSeverityStatus = VocabularyStatus


class NcSeverity(Base):
    """One revision of one severity.

    The row is the revision, not the word: `code` identifies the severity,
    `revision` counts up, and the word in force is the highest-numbered
    revision that was approved. Shaped exactly like `downtime_reasons`, which
    is shaped exactly like `documents`, because a list the whole plant's
    quality record is written in is a controlled thing.
    """

    __tablename__ = "nc_severities"
    __table_args__ = (
        UniqueConstraint("code", "revision", name="uq_nc_severity_revision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Short, lowercase and topic-safe, for the same reason a downtime code is:
    # this value is stored on every non-conformance, printed on a pallet
    # certificate, and compared between plants in a fleet view.
    #
    # Twenty characters, not forty: `NonConformance.severity` is a
    # `String(20)` on every plant that already exists, and a vocabulary that
    # could approve a word too long to store would be a vocabulary that
    # refused at the one moment it mattered - when a machine raised a hold.
    code: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(default=1)
    # What a person reads beside the record. The sentence is what the screen
    # offers as help and what tells two severities apart.
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[NcSeverityStatus] = mapped_column(
        str_enum(NcSeverityStatus), default=NcSeverityStatus.DRAFT)

    # This revision takes the code off the list rather than changing it.
    retires: Mapped[bool] = mapped_column(Boolean, default=False)
    # What the person retiring it said about how many non-conformances carry
    # the code. Null on a revision that is not a retirement; a number on one
    # that is, because a word may not leave the list without somebody having
    # looked at what it already labels.
    labels_records: Mapped[int | None] = mapped_column()

    created_by: Mapped[str] = mapped_column(String(40), default="system")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    approved_by: Mapped[str | None] = mapped_column(String(40))
    approved_at: Mapped[datetime | None] = mapped_column()
    # Who the drafter was acting for, when an agent drafted on somebody's
    # behalf. Repeated here as well as on the audit row so the panel showing
    # a waiting draft can say whose it is without a join.
    on_behalf_of: Mapped[str | None] = mapped_column(String(40))
