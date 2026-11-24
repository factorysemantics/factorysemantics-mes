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

from sqlalchemy import JSON, Date, ForeignKey, String, Text, UniqueConstraint
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
    # How many days before it falls due this gauge is called *due soon*.
    #
    # On the gauge rather than in the plant's settings because a quarterly
    # calibration wants a fortnight's warning and an annual one wants two
    # months, and one plant owns both. Thirty is the number the gauges screen
    # had invented for itself, at three places in one JavaScript file, so
    # until this column existed the shop floor's definition of *due soon*
    # lived in the browser and the server did not know it.
    warn_days: Mapped[int] = mapped_column(default=30)
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


class StudyState(enum.StrEnum):
    PLANNED = "planned"
    # At least one reading is in and at least one is still to take.
    COLLECTING = "collecting"
    # Every part, appraiser and trial has its reading.
    COMPLETE = "complete"


class GaugeStudy(Base):
    """A gauge study (gage R&R): how much of what a gauge reports is the gauge.

    Crossed: every appraiser measures every part, `trials` times each. The
    readings are the study's own and never a production check - they are the
    same few parts measured again and again on purpose, and charted with the
    plant's output they would look like a process that never moves.
    """

    __tablename__ = "gauge_studies"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    gauge_id: Mapped[int] = mapped_column(ForeignKey("gauges.id"), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    characteristic: Mapped[str] = mapped_column(String(80))
    # The tolerance the study is judged against, copied from the
    # specification when the study was planned: a specification widened
    # next month must not change what this study found.
    lower_spec: Mapped[float | None] = mapped_column()
    upper_spec: Mapped[float | None] = mapped_column()
    unit: Mapped[str] = mapped_column(String(20), default="")
    parts: Mapped[int] = mapped_column()
    trials: Mapped[int] = mapped_column()
    # The appraisers' names in the order they were given, as a list.
    appraisers: Mapped[list] = mapped_column(JSON)
    state: Mapped[StudyState] = mapped_column(str_enum(StudyState),
                                              default=StudyState.PLANNED)
    purpose: Mapped[str | None] = mapped_column(Text)
    planned_by: Mapped[str] = mapped_column(String(40))
    planned_at: Mapped[datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column()

    gauge: Mapped[Gauge] = relationship()
    readings: Mapped[list[StudyReading]] = relationship(
        back_populates="study", order_by="StudyReading.id")


class StudyReading(Base):
    """One reading of a gauge study: this part, by this appraiser, this trial."""

    __tablename__ = "gauge_study_readings"
    __table_args__ = (UniqueConstraint("study_id", "part", "appraiser", "trial",
                                       name="uq_gauge_study_reading"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("gauge_studies.id"), index=True)
    part: Mapped[int] = mapped_column()
    appraiser: Mapped[str] = mapped_column(String(40))
    trial: Mapped[int] = mapped_column()
    value: Mapped[float] = mapped_column()
    recorded_by: Mapped[str] = mapped_column(String(40))
    recorded_at: Mapped[datetime] = mapped_column(default=utcnow)

    study: Mapped[GaugeStudy] = relationship(back_populates="readings")
