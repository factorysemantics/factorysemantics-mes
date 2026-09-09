"""Gauges, and whether the measurement can be believed.

Nothing else in a quality module matters if this is wrong. A reading from an
out-of-calibration gauge is not a measurement, it is a number - and every
control chart, capability index and non-conformance built on it inherits that.
No open-source MES does gauge management at all, which is part of why this one
exists.
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fsmes.db import Base, utcnow
from fsmes.domain.common import str_enum


class GaugeStatus(enum.StrEnum):
    IN_SERVICE = "in_service"
    # Out of calibration but still on the floor: the dangerous state, and the
    # reason a gauge has a status at all.
    OVERDUE = "overdue"
    OUT_OF_SERVICE = "out_of_service"
    LOST = "lost"


class CalibrationResult(enum.StrEnum):
    PASS = "pass"
    # Found out of tolerance *before* adjustment. This is the one that matters:
    # every measurement since the last calibration is now suspect.
    FAIL_AS_FOUND = "fail_as_found"
    ADJUSTED = "adjusted"


class Gauge(Base):
    __tablename__ = "gauges"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(60), default="general")
    location: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[GaugeStatus] = mapped_column(
        str_enum(GaugeStatus), default=GaugeStatus.IN_SERVICE)
    # Months between calibrations, and when it was last done.
    interval_days: Mapped[int] = mapped_column(default=365)
    last_calibrated: Mapped[date | None] = mapped_column(Date)
    # What it can resolve. A gauge whose resolution is a third of the tolerance
    # cannot judge that tolerance, and the check that says so is below.
    resolution: Mapped[float | None] = mapped_column()

    calibrations: Mapped[list[Calibration]] = relationship(back_populates="gauge")


class Calibration(Base):
    """One calibration event, and what it found."""

    __tablename__ = "calibrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    gauge_id: Mapped[int] = mapped_column(ForeignKey("gauges.id"), index=True)
    performed_on: Mapped[date] = mapped_column(Date)
    result: Mapped[CalibrationResult] = mapped_column(str_enum(CalibrationResult))
    performed_by: Mapped[str] = mapped_column(String(40))
    certificate: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(default=utcnow)

    gauge: Mapped[Gauge] = relationship(back_populates="calibrations")
