"""The plant's downtime vocabulary: the reasons an operator may choose from.

Until this table existed, the question "why is it down" was a text box. The
pareto grouped on whatever came back, so *jam*, *Jam*, *jam at infeed* and
*infed jam* were four bars, each too small to act on, and the real top reason
on the line was invisible because it was spelled four ways. The operator did
nothing wrong: the screen asked an open question and got an open answer.

A vocabulary is the answer, and a vocabulary needs an author. This is the
fourth thing in this product to carry the draft → approved lifecycle, after
work instructions, triggers and setpoint adjustments, and for the same stated
reason: a list the whole plant's downtime is measured against is a controlled
thing, so one person writes it and another puts it in force.

The row is the revision, not the term. `code` identifies the reason,
`revision` counts up, and the term in force is the highest-numbered revision
that was approved. Retiring works the same way - a revision that retires the
code - which is what makes the rule below cheap to keep:

    **Retiring a code changes what may be chosen next. It never changes what
    was chosen before.** An interval labelled `jam_infeed` keeps that label
    when `jam_infeed` leaves the list, and the pareto keeps showing it.

Undo is therefore one move and deletes nothing: approve the previous revision.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fsmes.db import Base, utcnow
from fsmes.domain.common import VocabularyStatus, str_enum

#: Where one revision of one reason stands. The four statuses are shared with
#: every other vocabulary this plant authors rather than spelled again here:
#: `draft`, `approved`, `superseded` and `retired` mean the same thing about a
#: downtime reason as they do about a non-conformance severity, and two
#: separate spellings of one idea are two spellings that drift apart.
DowntimeReasonStatus = VocabularyStatus


class DowntimeReason(Base):
    """One revision of one downtime reason."""

    __tablename__ = "downtime_reasons"
    __table_args__ = (
        UniqueConstraint("code", "revision", name="uq_downtime_reason_revision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Short, lowercase and topic-safe: this value is what the analysis groups
    # on, and it is the half of a stop label that a fleet compares between
    # plants.
    code: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(default=1)
    # What the operator reads on the button. The sentence is what the screen
    # offers as help and what an agent reads to tell two reasons apart.
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[DowntimeReasonStatus] = mapped_column(
        str_enum(DowntimeReasonStatus), default=DowntimeReasonStatus.DRAFT)

    # This revision takes the code off the list rather than changing it.
    retires: Mapped[bool] = mapped_column(Boolean, default=False)
    # What the person retiring it said about how much history carries the
    # code. Null on a revision that is not a retirement; a number on one that
    # is, because a code may not leave the list without somebody having looked
    # at what it already labels.
    labels_intervals: Mapped[int | None] = mapped_column()

    created_by: Mapped[str] = mapped_column(String(40), default="system")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    approved_by: Mapped[str | None] = mapped_column(String(40))
    approved_at: Mapped[datetime | None] = mapped_column()
    # Who the drafter was acting for, when an agent drafted on somebody's
    # behalf. The audit row carries this too; it is repeated here so the panel
    # that shows a waiting draft can say whose it is without a join.
    on_behalf_of: Mapped[str | None] = mapped_column(String(40))
