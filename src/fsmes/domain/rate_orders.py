"""A standing rate order: the mix of parts a continuous plant is asked for.

Every other order in this product is a quantity by a date: make 500 of these
by Friday, and it closes when they are made. A continuous plant is not asked
for a quantity. It is asked for a *rate* - sixty reinforced plates a minute,
forty of them iron plates, twenty rods - for as long as it runs, and the
question about it is never "is it done" but "am I making the mix I asked for".
A work order with a very large quantity and no due date would answer that
question badly and close by accident one day.

So a rate order is its own thing: a name, and per item a target rate in units
a minute. It has no quantity, no due date and no status, and it never closes;
the plant changes it when it wants a different mix. The share of the mix
follows from the rates and is never stored - one definition per number.

A plant holds at most one (`services.rate_orders` enforces it). A plant with
none has no target, which is not a target of zero.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fsmes.db import Base, utcnow


class RateOrder(Base):
    __tablename__ = "rate_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(40))

    lines: Mapped[list[RateOrderLine]] = relationship(
        back_populates="order", cascade="all, delete-orphan",
        order_by="RateOrderLine.id")


class RateOrderLine(Base):
    """One item of a rate order and the rate it is wanted at, units a minute.
    `item` is the plant's own name for the part - on a Satisfactory plant, the
    game's (`Iron Plate`), so it matches what the reader books."""

    __tablename__ = "rate_order_lines"
    __table_args__ = (UniqueConstraint("rate_order_id", "item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rate_order_id: Mapped[int] = mapped_column(ForeignKey("rate_orders.id"), index=True)
    item: Mapped[str] = mapped_column(String(80))
    per_minute: Mapped[float]

    order: Mapped[RateOrder] = relationship(back_populates="lines")
