"""Keeping the line running: preventive plans, and the work they raise.

A plant does not maintain machines on dates. It maintains them on *use* - a
filler that ran two shifts owes its 8-hour check sooner than one that sat
idle, and a plan written in calendar days quietly over-maintains the idle
machine and under-maintains the busy one. So a plan is due on runtime, on a
counter, or on the calendar, and the plant tracks which.

The other thing a real plant needs and a toy skips: maintenance and downtime
are the same event seen twice. A machine stopped for a planned service is not
a breakdown, and an MES that cannot tell them apart reports availability that
means nothing - the same mistake as counting a changeover as downtime.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fsmes.db import Base, utcnow
from fsmes.domain.common import str_enum
from fsmes.domain.masterdata import Equipment


class TriggerKind(enum.StrEnum):
    """What makes a plan come due."""

    RUNTIME_HOURS = "runtime_hours"   # hours the machine actually ran
    CALENDAR_DAYS = "calendar_days"   # elapsed days, use or no use
    PRODUCED_QTY = "produced_qty"     # units it has made


class MaintenanceKind(enum.StrEnum):
    PREVENTIVE = "preventive"         # raised by a plan, before it breaks
    CORRECTIVE = "corrective"         # raised after it broke


class MaintenanceStatus(enum.StrEnum):
    DUE = "due"
    # Somebody has it. Between `due` and `in_progress` on purpose: a plant whose
    # only two states were those could not tell "nobody has this" from "somebody
    # has it and has not walked over yet", and those are the two facts a
    # maintenance supervisor spends the shift on.
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    SKIPPED = "skipped"


#: What a maintenance order is worth interrupting the day for. A number so it
#: sorts, and names beside it so a rule row and a screen read the same way.
PRIORITY_SAFETY = 1
PRIORITY_PRODUCTION_CRITICAL = 2
PRIORITY_ROUTINE = 3

#: What an order with no priority is *treated* as. Routine, because a plant
#: upgrading an old database has plans that never said, and quietly promoting
#: them to safety work would push the real safety work down the list. The column
#: stays null: the assumption is read here, never written into the row.
DEFAULT_PRIORITY = PRIORITY_ROUTINE

#: The statuses that mean somebody is holding this order and has not finished.
#: One list, read by the dispatcher (who is busy), by `raise_due` (don't raise a
#: second one) and by the backlog, so the three cannot drift apart.
OPEN_STATUSES = (
    MaintenanceStatus.DUE,
    MaintenanceStatus.ASSIGNED,
    MaintenanceStatus.IN_PROGRESS,
)


class MaintenancePlan(Base):
    """A recurring task on one machine, due on use rather than on a date."""

    __tablename__ = "maintenance_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"), index=True)
    trigger: Mapped[TriggerKind] = mapped_column(str_enum(TriggerKind))
    # Every `interval` of whatever the trigger counts.
    interval: Mapped[float]
    instructions: Mapped[str | None] = mapped_column(Text)
    # The work instruction that says how, if there is one. Documents already
    # anchor to equipment, so a plan points at a code rather than duplicating
    # the procedure.
    document_code: Mapped[str | None] = mapped_column(String(60))
    # How long the machine is expected to be down for it, so a planner can see
    # the cost of the plan and not only its benefit.
    expected_minutes: Mapped[float] = mapped_column(default=30.0)
    active: Mapped[bool] = mapped_column(default=True)

    # Which trade this job needs, and what it is worth interrupting the day
    # for. Both nullable, because a plant that upgraded from a release before
    # this has plans that never said - and a plan with no skill on it is work
    # anybody on shift can take, which is the honest reading rather than an
    # invented trade. Copied onto each order at raise, so changing the plan
    # never rewrites history.
    skill_code: Mapped[str | None] = mapped_column(String(40))
    priority: Mapped[int | None] = mapped_column()

    # Where the counter stood when this plan was last satisfied. Nullable
    # until the first service: a plan on a machine nobody has serviced yet is
    # due from the moment it is written, which is correct.
    last_done_at: Mapped[datetime | None] = mapped_column()
    last_done_runtime_hours: Mapped[float | None] = mapped_column()
    last_done_qty: Mapped[float | None] = mapped_column()

    equipment: Mapped[Equipment] = relationship()


class MaintenanceOrder(Base):
    """One occurrence of maintenance: raised, worked, closed."""

    __tablename__ = "maintenance_orders"
    __table_args__ = (Index("ix_maint_equipment_status", "equipment_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"), index=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("maintenance_plans.id"))
    kind: Mapped[MaintenanceKind] = mapped_column(str_enum(MaintenanceKind))
    status: Mapped[MaintenanceStatus] = mapped_column(
        str_enum(MaintenanceStatus), default=MaintenanceStatus.DUE)
    summary: Mapped[str] = mapped_column(String(200))
    # Why it came due, in the plant's own terms: "ran 212.4 h against a 200 h
    # plan". A due date with no reason is a due date nobody trusts.
    reason: Mapped[str | None] = mapped_column(String(200))

    # What this job needs and what it is worth, copied from the plan at raise.
    # A corrective order says its own: the thing broke, and what broke says
    # which trade and how badly it matters.
    skill_code: Mapped[str | None] = mapped_column(String(40))
    priority: Mapped[int | None] = mapped_column(index=True)

    # Who it was given to, when, and by what. `assigned_by` is a rule's code
    # when the rules gave it out and a person's code when a person did - and
    # the difference is load-bearing: a hand assignment is never overwritten by
    # the rules, because a supervisor who reaches in and is overruled by the
    # machine stops reaching in.
    assigned_to: Mapped[str | None] = mapped_column(String(40), index=True)
    assigned_at: Mapped[datetime | None] = mapped_column()
    assigned_by: Mapped[str | None] = mapped_column(String(40))
    # When it is meant to happen. Now, for work handed out as it comes due; the
    # start of a shift, for a plan that asks for a window.
    scheduled_for: Mapped[datetime | None] = mapped_column()
    # Why nobody has it, as of the last time dispatch looked. Null means it has
    # not been through dispatch - a different fact from "nobody could take it",
    # and the supervisor's page shows them differently.
    unassigned_reason: Mapped[str | None] = mapped_column(String(40))

    raised_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column()
    completed_at: Mapped[datetime | None] = mapped_column()
    performed_by: Mapped[str | None] = mapped_column(String(40))
    findings: Mapped[str | None] = mapped_column(Text)
    downtime_minutes: Mapped[float | None] = mapped_column()

    equipment: Mapped[Equipment] = relationship()
    plan: Mapped[MaintenancePlan | None] = relationship()
