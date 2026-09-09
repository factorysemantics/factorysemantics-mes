"""Quality: specs with limits, checks against them, non-conformances when they fail."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fsmes.db import Base, utcnow
from fsmes.domain.common import str_enum
from fsmes.domain.masterdata import Material


class QualitySpec(Base):
    """Acceptable range for one measurable characteristic of a material."""

    __tablename__ = "quality_specs"
    __table_args__ = (UniqueConstraint("material_id", "characteristic"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    characteristic: Mapped[str] = mapped_column(String(80))
    unit: Mapped[str] = mapped_column(String(20), default="")
    min_value: Mapped[float | None]
    max_value: Mapped[float | None]

    material: Mapped[Material] = relationship()


class CheckResult(enum.StrEnum):
    PASS = "pass"
    FAIL = "fail"


class QualityCheck(Base):
    __tablename__ = "quality_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    spec_id: Mapped[int] = mapped_column(ForeignKey("quality_specs.id"))
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_orders.id"), index=True)
    value: Mapped[float]
    result: Mapped[CheckResult] = mapped_column(str_enum(CheckResult))
    checked_by: Mapped[str | None] = mapped_column(String(40))
    # Which instrument took the reading. A measurement whose gauge is
    # later found out of tolerance has to be findable.
    gauge_id: Mapped[int | None] = mapped_column(ForeignKey("gauges.id"), index=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)

    spec: Mapped[QualitySpec] = relationship()


class NcStatus(enum.StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class NonConformance(Base):
    __tablename__ = "non_conformances"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    description: Mapped[str] = mapped_column(String(400))
    severity: Mapped[str] = mapped_column(String(20), default="minor")
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_orders.id"))
    lot_id: Mapped[int | None] = mapped_column(ForeignKey("material_lots.id"))
    status: Mapped[NcStatus] = mapped_column(str_enum(NcStatus), default=NcStatus.OPEN)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    closed_at: Mapped[datetime | None]
