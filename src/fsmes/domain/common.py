"""Shared column helpers for the domain model."""

import enum
from datetime import date

from sqlalchemy import Date, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column


def str_enum(enum_cls: type[enum.StrEnum]) -> SAEnum:
    """Store StrEnum *values* as plain VARCHAR — portable across SQLite and Postgres."""
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=30,
        values_callable=lambda e: [member.value for member in e],
    )


class ShiftStamped:
    """Two columns that say which shift a row fell in.

    Every table that records something happening on the floor carries these,
    written once when the row is written, from the plant's own clock and the
    shift patterns as they stood. A plant is run and measured by shift, and a
    booking that cannot say which shift made it cannot be put in front of the
    supervisor it belongs to.

    `shift_day` is the plant-local day the shift *started*, so the small hours
    of Saturday still carry Friday's date on a night shift - which is how the
    roster is written. Both columns are null when no pattern covered the
    instant, and null is read as *not attributed* rather than as a shift
    nobody named (house rule 2).
    """

    shift_code: Mapped[str | None] = mapped_column(String(40))
    shift_day: Mapped[date | None] = mapped_column(Date)


class VocabularyStatus(enum.StrEnum):
    """Where one revision of one word in a plant's vocabulary stands.

    Written once and read by every vocabulary, because two lists whose
    statuses were spelled separately would be two lists that drift. The
    downtime reasons were the first; the non-conformance severities are the
    second.

    `retired` is the fourth because a vocabulary shrinks as well as grows,
    and a retired term is not a superseded one: superseded means *another
    revision of this code took over*, retired means *this code is no longer
    offered*. A report that could not tell those apart could not explain why
    a word it still shows is on nobody's screen.
    """

    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    RETIRED = "retired"
