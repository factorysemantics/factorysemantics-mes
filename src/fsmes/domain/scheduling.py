"""A plan a supervisor can defend.

Releasing orders by hand and hoping is not scheduling. Finite capacity means
one machine does one thing at a time, work fits inside the shifts the plant
actually runs, and the promise a schedule makes about a date accounts for the
maintenance that machine already owes.

The schedule is *advisory*, deliberately. It says when work should run; the
floor books what happened. An MES that refuses production because it disagrees
with a plan is an MES people route around.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fsmes.db import Base, utcnow
from fsmes.domain.common import str_enum
from fsmes.domain.masterdata import Equipment
from fsmes.domain.workorders import WorkOrder, WorkOrderOperation


class SlotKind(enum.StrEnum):
    PRODUCTION = "production"
    MAINTENANCE = "maintenance"


class ScheduledSlot(Base):
    """One block of time booked on one machine.

    Maintenance takes slots too. A schedule that plans production straight
    through a service the machine already owes is not a plan, it is a
    prediction that something will go wrong.
    """

    __tablename__ = "scheduled_slots"
    __table_args__ = (
        Index("ix_slot_equipment_start", "equipment_id", "planned_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"), index=True)
    kind: Mapped[SlotKind] = mapped_column(str_enum(SlotKind), default=SlotKind.PRODUCTION)

    work_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_orders.id"))
    operation_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_order_operations.id"))
    maintenance_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("maintenance_orders.id"))

    planned_start: Mapped[datetime] = mapped_column(index=True)
    planned_end: Mapped[datetime] = mapped_column()
    # Working minutes the slot needs, kept alongside the times because the two
    # differ whenever the block spans a break and the difference is the whole
    # point of scheduling against a calendar.
    minutes: Mapped[float]
    sequence: Mapped[int] = mapped_column(default=0)
    planned_at: Mapped[datetime] = mapped_column(default=utcnow)
    note: Mapped[str | None] = mapped_column(String(200))

    equipment: Mapped[Equipment] = relationship()
    work_order: Mapped[WorkOrder | None] = relationship()
    operation: Mapped[WorkOrderOperation | None] = relationship()
