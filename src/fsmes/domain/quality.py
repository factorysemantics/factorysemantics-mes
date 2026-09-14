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
    # Which system supplied the reading, when it did not come from here.
    source_system: Mapped[str | None] = mapped_column(String(80))
    # The verdict that system sent with it, if it sent one. `result` above is
    # always this MES's own verdict, from this MES's spec. The two disagreeing
    # is a finding, not an error, and losing theirs would hide it.
    supplied_result: Mapped[CheckResult | None] = mapped_column(str_enum(CheckResult))
    ts: Mapped[datetime] = mapped_column(default=utcnow)

    spec: Mapped[QualitySpec] = relationship()


class NcStatus(enum.StrEnum):
    """Where a non-conformance has got to.

    A plant does not go from "something is wrong" to "nothing is wrong" in one
    click. Somebody picks it up, somebody decides what happens to the material,
    and only then is it closed. Each of those is a different person on a
    different day, and the record has to say which.
    """

    OPEN = "open"
    UNDER_REVIEW = "under_review"
    DISPOSITIONED = "dispositioned"
    CLOSED = "closed"


class NcDisposition(enum.StrEnum):
    """What happens to the material. These four are what a plant actually does.

    `use_as_is` is a concession: the material is out of spec and is used
    anyway, on somebody's name. That is exactly why the reason is required.
    """

    USE_AS_IS = "use_as_is"
    REWORK = "rework"
    SCRAP = "scrap"
    RETURN = "return"


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

    # Who, at each step. A step nobody took is null rather than the system's
    # name: "system" on a disposition would be a lie about who decided.
    raised_by: Mapped[str | None] = mapped_column(String(40))
    reviewed_by: Mapped[str | None] = mapped_column(String(40))
    reviewed_at: Mapped[datetime | None]
    disposition: Mapped[NcDisposition | None] = mapped_column(str_enum(NcDisposition))
    disposition_reason: Mapped[str | None] = mapped_column(String(400))
    disposition_by: Mapped[str | None] = mapped_column(String(40))
    disposition_at: Mapped[datetime | None]
    closed_by: Mapped[str | None] = mapped_column(String(40))

    def history(self) -> list[dict]:
        """The steps this non-conformance has actually been through.

        Built from the columns, so it can never disagree with the state. A
        row whose who is unknown says `null`, not a guess - these rows were
        written before the MES recorded who, and an invented name on a
        quality record is worse than an honest gap.
        """
        steps: list[dict] = [
            {"step": "opened", "by": self.raised_by, "at": self.created_at, "detail": self.description},
        ]
        if self.reviewed_at or self.reviewed_by:
            steps.append({"step": "under_review", "by": self.reviewed_by, "at": self.reviewed_at,
                          "detail": None})
        if self.disposition is not None:
            steps.append({"step": "dispositioned", "by": self.disposition_by, "at": self.disposition_at,
                          "detail": f"{self.disposition.value}: {self.disposition_reason}"
                                    if self.disposition_reason else self.disposition.value})
        if self.closed_at:
            steps.append({"step": "closed", "by": self.closed_by, "at": self.closed_at, "detail": None})
        return steps
