"""The maintenance crew: what people can do, who is on shift, and the rules
that hand work to them.

A plant does not maintain machines with a pool of interchangeable people. It
maintains them with electricians, mechanics, pipefitters and welders, and the
difference is the whole job: a filler's electrical fault handed to a mechanic
is a shift lost and a supervisor who stops trusting the list. So a skill is a
first class thing, a person holds skills, and a maintenance order *needs* one.

Three tables carry that, and a fourth carries the supervisor's judgment:

* `skills` — the trades this plant recognises. Config, not code: a plant that
  employs pipefitters separately from mechanics adds the row and changes
  nothing else (house rule 4).
* `personnel_skills` — who holds which, and how well.
* `roster` — who is on which shift. A row with no day is a *standing*
  assignment: this person is on this shift whenever it runs. A row with a day
  is that day only, and overrides the standing one — which is how an absence,
  a training day or a cover shift is written down without rewriting the
  roster.
* `dispatch_rules` — one row is one sentence a supervisor would say out loud:
  *"filler electrical work goes to an electrician on this shift, least loaded
  first."* Rules are tried in order and the first that finds a free person
  wins. Rules, not a solver: a supervisor who cannot read why the work went
  where it went will not trust the dispatcher, and the day a rule cannot say
  what they mean is the day to reach for arithmetic.

WHAT IS NOT HERE. No work-order history, no spares catalogue, no labour
costing. The MES knows this plant's people, its shifts and its machines, so it
can dispatch; a CMMS the size of a CMMS is a different product and stays one.
"""

from __future__ import annotations

import enum
from datetime import date

from sqlalchemy import Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fsmes.db import Base
from fsmes.domain.common import str_enum
from fsmes.domain.masterdata import Person

# How well somebody holds a skill. A number so it sorts, and three rungs
# because a plant that needs more adds them in its own pack rather than
# waiting for this file to change.
LEVEL_TRAINEE = 1      # can do it watched
LEVEL_COMPETENT = 2    # can do it alone — the default, and what dispatch needs
LEVEL_EXPERT = 3       # can teach it and sign it off

#: The lowest level the dispatcher will hand work to unwatched. A trainee is on
#: the roster and holds the skill; they are not who the plant sends on their
#: own, and a dispatcher that sent them would be the reason nobody trusts it.
DISPATCHABLE_LEVEL = LEVEL_COMPETENT


class DispatchStrategy(enum.StrEnum):
    """How a rule chooses between the people it is allowed to choose from.

    All three are sentences, not objective functions. That is the point: a
    supervisor reads the rule row and knows what will happen.
    """

    #: Fewest open orders already on them. The default, and what a supervisor
    #: means by "whoever is free".
    LEAST_LOADED = "least_loaded"
    #: The one whose home station is nearest the machine — same work centre
    #: first, then the same line. A plant whose lines are a walk apart saves
    #: the walk.
    NEAREST = "nearest"
    #: Take turns. Used where the work is interchangeable and the plant wants
    #: it spread evenly rather than piled on whoever is quickest.
    ROUND_ROBIN = "round_robin"


class UnassignedReason(enum.StrEnum):
    """Why an order that came due went to nobody.

    Written on the order rather than worked out again later, because the
    supervisor's question is *"why has nobody got this?"* and the answer is a
    fact about the moment dispatch ran, not about now. Unknown is not zero
    (house rule 2): an order with no reason on it has not been through
    dispatch, which is a different fact from "nobody could take it".
    """

    NO_RULE = "no_rule"                                  # no rule covers it
    NOBODY_ON_SHIFT_WITH_SKILL = "nobody_on_shift_with_skill"
    ALL_BUSY = "all_busy"                                # they are all on jobs


class Skill(Base):
    """A trade this plant recognises.

    Three ship with the product — electrician, mechanic, general — and a pack
    adds whatever a plant actually employs. The code is what a plan, an order
    and a rule all point at, so renaming the name never breaks a rule.
    """

    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(400))


class PersonnelSkill(Base):
    """One person holds one skill, at one level."""

    __tablename__ = "personnel_skills"
    __table_args__ = (
        UniqueConstraint("personnel_id", "skill_code", name="uq_personnel_skill"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    personnel_id: Mapped[int] = mapped_column(ForeignKey("personnel.id"), index=True)
    skill_code: Mapped[str] = mapped_column(String(40), index=True)
    level: Mapped[int] = mapped_column(default=LEVEL_COMPETENT)

    person: Mapped[Person] = relationship()


class RosterEntry(Base):
    """Who is on a shift.

    `shift_day` null is a **standing** assignment: on this shift every time it
    runs. A dated row is that day only and wins over the standing one, so an
    absence is `available = false` on one day rather than a deletion and a
    re-entry. The dispatcher reads both and says which it used.

    There is no shift *table* in this product — a shift is a pattern plus a
    date (`services.calendar`) — so this points at a pattern's code and the
    plant-local day the shift started, exactly as `ShiftStamped` does.
    """

    __tablename__ = "roster"
    __table_args__ = (
        UniqueConstraint("personnel_id", "shift_code", "shift_day", name="uq_roster_slot"),
        Index("ix_roster_shift", "shift_code", "shift_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    personnel_id: Mapped[int] = mapped_column(ForeignKey("personnel.id"), index=True)
    shift_code: Mapped[str] = mapped_column(String(40))
    shift_day: Mapped[date | None] = mapped_column(Date)
    available: Mapped[bool] = mapped_column(default=True)
    # Absent, training, on another line. Said out loud, because a roster that
    # only says "no" tells a supervisor nothing they can act on.
    reason: Mapped[str | None] = mapped_column(String(160))

    person: Mapped[Person] = relationship()

    @property
    def standing(self) -> bool:
        return self.shift_day is None


class DispatchRule(Base):
    """One sentence the supervisor wrote down.

    Read it left to right: *work on `equipment_code` (or anything under it)
    needing `skill_code`, at priority `priority_at_least` or worse, goes to
    somebody on this shift by `strategy`.* Any of the three filters left null
    means "don't care", so the plant-wide default rule is one row with three
    nulls.

    `sequence` and not `order`: `order` is a SQL keyword, and in a plant that
    already has maintenance *orders* the word was going to be read wrong.
    """

    __tablename__ = "dispatch_rules"
    __table_args__ = (Index("ix_dispatch_rules_active_sequence", "active", "sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    # Who wrote it. A rule with nobody's name on it is a rule nobody owns, and
    # the audit row that names the rule can then say who to ask.
    supervisor_code: Mapped[str | None] = mapped_column(String(40))
    # An area, a line, a work centre or one machine — the rule matches that
    # node and everything under it. Null is the whole plant.
    equipment_code: Mapped[str | None] = mapped_column(String(40))
    skill_code: Mapped[str | None] = mapped_column(String(40))
    # 1 safety, 2 production-critical, 3 routine: a rule written for safety
    # work sets 1 and never catches a routine filter change.
    priority_at_least: Mapped[int | None] = mapped_column()
    strategy: Mapped[DispatchStrategy] = mapped_column(
        str_enum(DispatchStrategy), default=DispatchStrategy.LEAST_LOADED)
    active: Mapped[bool] = mapped_column(default=True)
    sequence: Mapped[int] = mapped_column(default=100)
