"""When the plant is running.

Everything that promises a date needs this. A due date computed as "now plus
six hours" is a lie on a plant that stops at ten, and a schedule that ignores
the weekend promises Monday's work on Saturday. So the calendar is a first
class thing: shift patterns, the days they run, and the exceptions - a
shutdown week, a bank holiday, an overtime Saturday - that every real plant
has and no demo models.
"""

from __future__ import annotations

import enum
from datetime import date, time

from sqlalchemy import Date, ForeignKey, String, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fsmes.db import Base
from fsmes.domain.common import str_enum
from fsmes.domain.masterdata import Equipment


class ExceptionKind(enum.StrEnum):
    NON_WORKING = "non_working"   # a holiday or a shutdown: the plant is dark
    WORKING = "working"           # an overtime day that is normally dark


class ShiftPattern(Base):
    """One shift, and the days of the week it runs.

    `days` is a seven character mask, Monday first: "1111100" is weekdays.
    A string rather than a table of days because a pattern is read on every
    scheduling decision and this makes that one column, and because reading
    "1111100" tells you what it means without a join.
    """

    __tablename__ = "shift_patterns"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    starts: Mapped[time] = mapped_column(Time)
    ends: Mapped[time] = mapped_column(Time)
    days: Mapped[str] = mapped_column(String(7), default="1111100")
    active: Mapped[bool] = mapped_column(default=True)
    # A shift may belong to one line, or to the whole site when null.
    equipment_id: Mapped[int | None] = mapped_column(ForeignKey("equipment.id"))

    equipment: Mapped[Equipment | None] = relationship()

    @property
    def crosses_midnight(self) -> bool:
        """A night shift ends on the following day, and every calculation that
        forgets this loses eight hours."""
        return self.ends <= self.starts


class CalendarException(Base):
    """A day that does not follow the pattern.

    Both directions matter. A shutdown week has to remove capacity a schedule
    would otherwise promise, and an overtime Saturday has to add capacity a
    planner is counting on.
    """

    __tablename__ = "calendar_exceptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[ExceptionKind] = mapped_column(str_enum(ExceptionKind))
    reason: Mapped[str] = mapped_column(String(160))
    equipment_id: Mapped[int | None] = mapped_column(ForeignKey("equipment.id"))

    equipment: Mapped[Equipment | None] = relationship()
