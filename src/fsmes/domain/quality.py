"""Quality: specs with limits, checks against them, non-conformances when they fail."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fsmes.db import Base, utcnow
from fsmes.domain.common import ShiftStamped, str_enum
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


class QualityCheck(ShiftStamped, Base):
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
    # The station that took the reading, when a station took it. A person
    # with a gauge does not record one, and null there means *not recorded*,
    # not "no machine": deriving a station from the order's route would name
    # a machine nobody stood at.
    equipment_id: Mapped[int | None] = mapped_column(ForeignKey("equipment.id"), index=True)
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


class NonConformance(ShiftStamped, Base):
    __tablename__ = "non_conformances"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    description: Mapped[str] = mapped_column(String(400))
    severity: Mapped[str] = mapped_column(String(20), default="minor")
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_orders.id"))
    lot_id: Mapped[int | None] = mapped_column(ForeignKey("material_lots.id"))
    status: Mapped[NcStatus] = mapped_column(str_enum(NcStatus), default=NcStatus.OPEN)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # `shift_code`/`shift_day` (ShiftStamped) are the shift it was *raised*
    # in. A non-conformance is reviewed, dispositioned and closed on other
    # days by other people; those steps carry their own timestamps above and
    # no shift of their own, because the shift that matters for a per-shift
    # quality report is the one the problem was found on.
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
    # What the MES saw, when the MES raised this itself. A record a machine
    # opened has to carry the reason it opened it, or a supervisor is being
    # asked to trust an assertion. Null when a person raised it: they wrote
    # the description, and inventing evidence for them would be worse.
    evidence: Mapped[dict | None] = mapped_column(JSON)

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


class SpcSignal(Base):
    """One Western Electric rule firing on one characteristic, once.

    A control chart that computes its rules on demand tells whoever happens
    to open the screen. A signal recorded here happened whether or not
    anybody looked, and carries what it was looking at when it fired.

    `window_key` is the identity of the readings the rule judged - the first
    and last check id of its window - so re-running the rules over the same
    stored readings finds this row and raises nothing a second time. That is
    what makes evaluating on every write safe.
    """

    __tablename__ = "spc_signals"
    __table_args__ = (UniqueConstraint("spec_id", "rule", "window_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    spec_id: Mapped[int] = mapped_column(ForeignKey("quality_specs.id"), index=True)
    rule: Mapped[int]
    window_key: Mapped[str] = mapped_column(String(60))
    # The reading the rule fired on, and the station that took it when one did.
    check_id: Mapped[int | None] = mapped_column(ForeignKey("quality_checks.id"))
    value: Mapped[float]
    what: Mapped[str] = mapped_column(String(120))
    # The chart as it stood when the rule fired: centre, sigma, the limits and
    # how many readings they came from. Recomputing them later gives different
    # numbers, and then nobody can see what the MES actually acted on.
    window: Mapped[dict | None] = mapped_column(JSON)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    # The non-conformance this signal raised, or the one already open for this
    # rule on this characteristic - the same excursion, not a second finding.
    nonconformance_id: Mapped[int | None] = mapped_column(ForeignKey("non_conformances.id"), index=True)

    spec: Mapped[QualitySpec] = relationship()
