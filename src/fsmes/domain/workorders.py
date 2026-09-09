"""Work orders and their operations — the ISA-95 operations schedule made concrete."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fsmes.db import Base, utcnow
from fsmes.domain.common import str_enum
from fsmes.domain.masterdata import Equipment, Material


class OrderStatus(enum.StrEnum):
    PLANNED = "planned"
    RELEASED = "released"
    RUNNING = "running"
    # A concern on the order: work stops, nothing books, the scheduler skips
    # it - but unlike CANCELLED it is expected back. Stored as plain VARCHAR
    # (str_enum, no CHECK constraint), so no migration accompanies this.
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class OperationStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    quantity: Mapped[float]
    status: Mapped[OrderStatus] = mapped_column(str_enum(OrderStatus), default=OrderStatus.PLANNED, index=True)
    priority: Mapped[int] = mapped_column(default=50)  # lower number = more urgent
    due_date: Mapped[datetime | None]
    erp_reference: Mapped[str | None] = mapped_column(String(80))
    # Which line this order runs on, taken from where its route starts. A
    # plant with six lines cannot show a supervisor "the orders" and mean
    # all of them; nullable because an order whose route spans no line - or
    # a plant with one - still has to exist.
    work_center_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment.id"), index=True)

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    released_at: Mapped[datetime | None]
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]

    material: Mapped[Material] = relationship()
    operations: Mapped[list[WorkOrderOperation]] = relationship(
        back_populates="order", order_by="WorkOrderOperation.seq", cascade="all, delete-orphan"
    )

    @property
    def good_qty(self) -> float:
        """Good quantity of the final operation — what the order actually yielded."""
        return self.operations[-1].good_qty if self.operations else 0.0

    @property
    def scrap_qty(self) -> float:
        return sum(op.scrap_qty for op in self.operations)


class WorkOrderOperation(Base):
    """A routing operation copied onto a specific order at creation time,
    so later routing edits never rewrite production history."""

    __tablename__ = "work_order_operations"
    __table_args__ = (UniqueConstraint("work_order_id", "seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"))
    seq: Mapped[int]
    name: Mapped[str] = mapped_column(String(120))
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"))
    status: Mapped[OperationStatus] = mapped_column(str_enum(OperationStatus), default=OperationStatus.PENDING)
    # Copied from the routing at creation, like everything else here: a
    # route re-timed next month must not silently re-plan an order already
    # running to it.
    setup_seconds: Mapped[float | None]
    run_seconds_per_unit: Mapped[float | None]
    labour_seconds_per_unit: Mapped[float | None]

    good_qty: Mapped[float] = mapped_column(default=0.0)
    scrap_qty: Mapped[float] = mapped_column(default=0.0)
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]

    order: Mapped[WorkOrder] = relationship(back_populates="operations")
    equipment: Mapped[Equipment] = relationship()
