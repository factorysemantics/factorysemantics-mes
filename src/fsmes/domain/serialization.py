"""Serialisation: tracing a thing, not a batch.

Order-level genealogy answers "which lot of caps went into this order". A
recall asks "which lot of caps went into *this pack*", and the difference is
the difference between quarantining a pallet and quarantining a week.

Two relationships carry it. A unit is *produced by* an order at a machine, and
a unit may be *contained in* another - bottles in a case, cases on a pallet.
Containment is a tree, so a suspect lot can be walked forward to every pallet
that carries it, and any pallet walked back to everything inside.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fsmes.db import Base, utcnow
from fsmes.domain.common import str_enum
from fsmes.domain.masterdata import Equipment, Material
from fsmes.domain.workorders import WorkOrder


class UnitStatus(enum.StrEnum):
    IN_PROCESS = "in_process"
    GOOD = "good"
    SCRAPPED = "scrapped"
    # Held, not destroyed. A recall quarantines stock; deciding what happens to
    # it afterwards is a separate, human decision.
    QUARANTINED = "quarantined"
    SHIPPED = "shipped"


class SerialUnit(Base):
    """One identified thing: a bottle, a case, a pallet."""

    __tablename__ = "serial_units"
    __table_args__ = (
        Index("ix_serial_order_material", "produced_by_order_id", "material_id"),
        Index("ix_serial_parent", "parent_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # unique alone: a unique constraint is an index, and declaring index=True
    # beside it built a second one - the largest index on the largest table,
    # carried for nothing (found sizing the cutlery plant's day).
    serial: Mapped[str] = mapped_column(String(60), unique=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    status: Mapped[UnitStatus] = mapped_column(
        str_enum(UnitStatus), default=UnitStatus.IN_PROCESS)

    produced_by_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_orders.id"))
    produced_at: Mapped[datetime] = mapped_column(default=utcnow)
    produced_on_id: Mapped[int | None] = mapped_column(ForeignKey("equipment.id"))

    # What this unit is packed into. Null means it is loose, or is itself the
    # outermost package.
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("serial_units.id"))
    # Why it was held, so a quarantine can be explained six months later.
    note: Mapped[str | None] = mapped_column(String(300))

    material: Mapped[Material] = relationship()
    order: Mapped[WorkOrder | None] = relationship()
    equipment: Mapped[Equipment | None] = relationship()
    parent: Mapped[SerialUnit | None] = relationship(
        remote_side=[id], backref="contains")


class UnitInspection(Base):
    """One automated inspection of one identified unit: what a vision station
    measured, whether it passed, and which attributes failed.

    A row per piece, stack or wrap, written by the OPC agent from the
    station's inspection group - never through tag history, where thirty
    million pieces a day would be a hundred and sixty million samples. The
    unit's status (good or scrapped) is the verdict; this row is the evidence.
    """

    __tablename__ = "unit_inspections"
    __table_args__ = (
        Index("ix_unit_inspections_equipment_ts", "equipment_id", "ts"),
        Index("ix_unit_inspections_unit", "unit_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("serial_units.id"))
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"))
    # The station's own event counter, so a gap in the sequence is visible.
    seq: Mapped[int] = mapped_column(BigInteger)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    passed: Mapped[bool] = mapped_column(default=True)
    # Bit i set means attribute i failed; the manifest names the attributes.
    fail_mask: Mapped[int] = mapped_column(default=0)
    values: Mapped[list | None] = mapped_column(JSON)


class SerialSequence(Base):
    """The next number for a serial prefix.

    The first serial numbering counted every unit with the prefix to find the
    next one - a table scan per unit, quadratic over a day, and wrong the
    moment a marker's own serials were mixed in. A counter row is one indexed
    read and one write, whatever the table holds.
    """

    __tablename__ = "serial_sequences"

    prefix: Mapped[str] = mapped_column(String(60), primary_key=True)
    next: Mapped[int] = mapped_column(default=1)


class UnitComponent(Base):
    """A lot that went into one specific unit, at one specific station.

    This is the row that makes a recall precise. Without it the best anyone can
    say is which order a lot reached; with it, exactly which bottles carry it.
    """

    __tablename__ = "unit_components"
    __table_args__ = (Index("ix_unit_component_lot", "lot_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("serial_units.id"), index=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("material_lots.id"), index=True)
    quantity: Mapped[float]
    operation_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_order_operations.id"))
    ts: Mapped[datetime] = mapped_column(default=utcnow)

    unit: Mapped[SerialUnit] = relationship()
