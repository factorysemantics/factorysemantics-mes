"""Execution records: material lots, consumption (genealogy), production logs."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fsmes.db import Base, utcnow
from fsmes.domain.common import str_enum
from fsmes.domain.masterdata import Material
from fsmes.domain.workorders import WorkOrderOperation


class LotStatus(enum.StrEnum):
    AVAILABLE = "available"
    EXHAUSTED = "exhausted"
    BLOCKED = "blocked"


class MaterialLot(Base):
    """A traceable batch of material. `quantity` is what remains; consumption
    rows plus `produced_by_order_id` give full where-from/where-to genealogy."""

    __tablename__ = "material_lots"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    quantity: Mapped[float]
    original_quantity: Mapped[float]
    status: Mapped[LotStatus] = mapped_column(str_enum(LotStatus), default=LotStatus.AVAILABLE)
    produced_by_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_orders.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    material: Mapped[Material] = relationship()


class LotConsumption(Base):
    __tablename__ = "lot_consumptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), index=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("material_lots.id"), index=True)
    quantity: Mapped[float]
    # Where it went in. "Which lot of caps is in this pallet" needs the
    # station; "which lot of caps is in this order" is a much weaker claim and
    # is what a recall gets stuck with when the operation is not recorded.
    operation_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_order_operations.id"), index=True)
    equipment_id: Mapped[int | None] = mapped_column(ForeignKey("equipment.id"))
    ts: Mapped[datetime] = mapped_column(default=utcnow)

    lot: Mapped[MaterialLot] = relationship()
    operation: Mapped[WorkOrderOperation | None] = relationship()


class ProductionSource(enum.StrEnum):
    MANUAL = "manual"
    OPC = "opc"


class ProductionLog(Base):
    """Every quantity booking, whether typed by an operator or counted by a machine.

    `work_order_id` is nullable, and that is the whole point of this table
    rather than a column on the operation: a machine that counts past its
    order, or between orders, has still made something. Those units are
    recorded here against the equipment with no order - *unassigned
    production* - because the alternative is a warning line, and a unit the
    plant made may not disappear because the MES had nowhere tidy to put it.
    """

    __tablename__ = "production_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_orders.id"), index=True)
    operation_id: Mapped[int | None] = mapped_column(ForeignKey("work_order_operations.id"))
    equipment_id: Mapped[int | None] = mapped_column(ForeignKey("equipment.id"), index=True)
    good_qty: Mapped[float] = mapped_column(default=0.0)
    scrap_qty: Mapped[float] = mapped_column(default=0.0)
    source: Mapped[ProductionSource] = mapped_column(str_enum(ProductionSource), default=ProductionSource.MANUAL)
    ts: Mapped[datetime] = mapped_column(default=utcnow, index=True)
