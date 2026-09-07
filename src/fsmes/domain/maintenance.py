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
    IN_PROGRESS = "in_progress"
    DONE = "done"
    SKIPPED = "skipped"


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

    raised_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column()
    completed_at: Mapped[datetime | None] = mapped_column()
    performed_by: Mapped[str | None] = mapped_column(String(40))
    findings: Mapped[str | None] = mapped_column(Text)
    downtime_minutes: Mapped[float | None] = mapped_column()

    equipment: Mapped[Equipment] = relationship()
    plan: Mapped[MaintenancePlan | None] = relationship()
