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
    #: How many pieces this characteristic is inspected at a time - the
    #: sampling plan, which is the plant's decision and not the product's.
    #: Null or 1 is one piece at a time, which is every specification written
    #: before this column existed and is left exactly as it was. Above 1 the
    #: readings are a *subgroup*: the chart's points are the sample means,
    #: and the rules run on those. See decision 0040.
    sample_size: Mapped[int | None]
    #: Whether this characteristic is *counted* rather than measured. Null or
    #: false is measured, which is every specification written before this
    #: column existed: a gauge gives a number, and the chart is individuals or
    #: X-bar and R by the sampling plan above. True is an inspector looking at
    #: a lot and counting - how many were looked at, how many were sent back -
    #: and the chart is a p chart. The two are exclusive, and the write path
    #: says so: a count has no value to measure and a measurement has no
    #: denominator. Decision 0041, amending 0040.
    counted: Mapped[bool | None]

    material: Mapped[Material] = relationship()


class CheckResult(enum.StrEnum):
    PASS = "pass"
    FAIL = "fail"


class QualitySample(ShiftStamped, Base):
    """One subgroup: the n pieces a sampled characteristic was inspected on.

    The readings themselves stay in `quality_checks`, one row each, with
    `sample_id` pointing here. That is deliberate and it is the smallest
    shape that keeps every existing query true: a count of checks, the
    measurements card, the certificate, the gauge's impact list and the
    inspection history all read `quality_checks`, and a sample stored as five
    numbers in one row would have made each of them wrong in a different way.

    What this row holds is the facts that belong to the *sample* rather than
    to a reading: when it was taken, against which specification and order,
    at which station, with which instrument, and by whom. A sample is one act
    of measurement by one person with one gauge; recording that five times
    would invite the five copies to disagree.
    """

    __tablename__ = "quality_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    spec_id: Mapped[int] = mapped_column(ForeignKey("quality_specs.id"), index=True)
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_orders.id"), index=True)
    # The station the pieces were measured at, and the instrument that
    # measured them. Null is *not recorded* on both, exactly as on a check.
    equipment_id: Mapped[int | None] = mapped_column(ForeignKey("equipment.id"), index=True)
    gauge_id: Mapped[int | None] = mapped_column(ForeignKey("gauges.id"), index=True)
    checked_by: Mapped[str | None] = mapped_column(String(40))
    ts: Mapped[datetime] = mapped_column(default=utcnow)

    spec: Mapped[QualitySpec] = relationship()


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
    # Which sample this reading is one of, when the specification says the
    # characteristic is inspected n pieces at a time. Null is a reading taken
    # on its own, which is every reading written before this column existed
    # and every reading on a specification with no sampling plan.
    sample_id: Mapped[int | None] = mapped_column(ForeignKey("quality_samples.id"), index=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)

    spec: Mapped[QualitySpec] = relationship()
    sample: Mapped[QualitySample | None] = relationship()


class QualityCount(ShiftStamped, Base):
    """One inspection count: how many were looked at, how many were sent back.

    This is the record for a characteristic the plant *counts* rather than
    measures. An inspector - or a camera station - watches a stretch of one
    order, and what comes out of it is two whole numbers and a list of what
    was wrong. There is no measured value anywhere in it, which is why it
    cannot live in `quality_checks`: a check is one float against one
    specification, and writing a row per unit inspected to make a count out
    of it was refused in decision 0027 and is still refused here. Ten
    thousand bottles looked at is one row.

    `lot_ref` is what the plant calls the stretch, as text, because that is
    what the plant has. `lot_id` points at a real material lot when this
    plant tracks them and this count was taken on one; null is *this plant
    does not ship lots yet*, not "no lot", and the screen says so in words
    rather than showing an empty link.

    `defects` is the breakdown - `[{"code": "low_fill", "count": 3}, ...]` -
    and it is the whole of the answer to "which ones". It is a list and not
    a set of columns because the codes are the plant's, not the product's,
    and a new code must not need a migration. The counts in it are of
    *defects*, which can exceed `rejected`: one bottle sent back can be
    short-filled and badly capped at once. Nothing here assumes otherwise.
    """

    __tablename__ = "quality_counts"

    id: Mapped[int] = mapped_column(primary_key=True)
    spec_id: Mapped[int] = mapped_column(ForeignKey("quality_specs.id"), index=True)
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_orders.id"), index=True)
    # The station that did the counting, when a station did. Null is *not
    # recorded*, exactly as on a check: a person with a clipboard does not
    # name a machine, and naming one from the route would name a machine
    # nobody stood at.
    equipment_id: Mapped[int | None] = mapped_column(ForeignKey("equipment.id"), index=True)
    # The stretch of production this count covers. Both are recorded because
    # a count without its window cannot be put beside the line's own output:
    # "50 of 1200" means nothing until you know which twenty minutes.
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    #: How many units were looked at. The denominator of the chart's point.
    inspected: Mapped[int]
    #: How many of them were sent back. Never greater than `inspected`; the
    #: write path refuses it rather than drawing a rate above one.
    rejected: Mapped[int]
    #: What the plant calls the lot or stretch this count was taken on, in
    #: the plant's own text. Required: a count nobody can tie back to a
    #: batch of product is a number without a subject.
    lot_ref: Mapped[str] = mapped_column(String(60), index=True)
    #: The material lot, when this plant tracks lots and this is one of them.
    lot_id: Mapped[int | None] = mapped_column(ForeignKey("material_lots.id"), index=True)
    checked_by: Mapped[str | None] = mapped_column(String(40))
    #: `[{"code": ..., "count": ...}]`, the breakdown by defect. Null is *not
    #: recorded* and an empty list is *nothing found* - the screen says which,
    #: because a chart that showed "no defects" for a count nobody broke down
    #: would be inventing a clean lot.
    defects: Mapped[list | None] = mapped_column(JSON)
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
    # The inspection count the rule fired on, when the characteristic is
    # counted rather than measured. Exactly one of these two is set, and
    # `window_key` carries a prefix per kind so a count's ids and a check's
    # ids can never be read as the same window.
    count_id: Mapped[int | None] = mapped_column(ForeignKey("quality_counts.id"))
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
