"""Equipment state history — the raw material for availability and downtime analysis."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fsmes.db import Base, utcnow
from fsmes.domain.common import str_enum
from fsmes.domain.masterdata import Equipment


class EquipmentStateName(enum.StrEnum):
    RUNNING = "running"
    IDLE = "idle"
    DOWN = "down"
    SETUP = "setup"


class EquipmentState(Base):
    """One contiguous stretch in a state. The open interval (ended_at IS NULL)
    is the equipment's current state."""

    __tablename__ = "equipment_states"
    __table_args__ = (
        Index("ix_equipment_states_eq_started", "equipment_id", "started_at"),
        # "Which intervals are open" and "which overlap the last eight hours"
        # both ask about ended_at. Without this, each was a scan of every
        # interval the plant ever recorded - 1.4 million after a day of a
        # 108-station plant, three quarters of a second per Floor refresh.
        Index("ix_equipment_states_ended", "ended_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"))
    state: Mapped[EquipmentStateName] = mapped_column(str_enum(EquipmentStateName))
    reason: Mapped[str | None] = mapped_column(String(120))
    # Who named the stop. The interval is always this MES's own observation;
    # the label on it may have been supplied by another system, and a pareto
    # that cannot tell the two apart is a pareto nobody can audit.
    reason_source: Mapped[str | None] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    ended_at: Mapped[datetime | None]

    equipment: Mapped[Equipment] = relationship()
