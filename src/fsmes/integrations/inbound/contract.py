"""The inbound contract, typed: what another system can tell this MES.

Three events, because there are three things people do that no machine
signal can supply — they name a stop, they record a measurement, and they
count what was made. Everything else the MES knows, it watched.

Every event carries the same four facts about its provenance, and they are
the point of the contract rather than metadata on the side:

`source`         the free name of the system that supplied it, as its
                 operators would say it out loud — `replay:incumbent-mes`,
                 `lims:brookfield`, `terminal:line1`. Never guessed here;
                 it comes from configuration, because only the plant knows
                 what its own systems are called.
`source_kind`    a fixed category, so a report can group without parsing
                 the free string.
`external_key`   the supplier's own identifier for the record. It is what
                 makes the same file dropped twice a no-op. We never mint
                 one: an event with no key from its supplier is rejected,
                 because a key we invented would be different next time.
`recorded_at`    when the supplying system recorded it. Not when we read
                 the file. A stop labelled at 09:12 and handed over at
                 17:00 happened at 09:12, and the difference is the whole
                 reason a replay can be compared with an observation.

Unknown fields stay null. Nothing here is defaulted to zero, and nothing is
defaulted to "now" — a timestamp the MES made up is indistinguishable, a
week later, from one the plant supplied.

Clean-room: these models are this project's own shape for facts any plant
has. Nothing here is derived from any commercial MES's schema, export
format or documentation. What a particular plant's columns are called is
that plant's configuration, not this file's business.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class InboundSourceKind(enum.StrEnum):
    """What kind of system supplied the event.

    Deliberately coarse. The category is for grouping a report; the free
    `source` string is what actually names the system, and a plant with two
    of the same kind needs the string, not a longer enum.
    """

    # Another execution system's own records, handed over — the shadow case.
    REPLAY = "replay"
    # A business system: an ERP, an MRP, a planning tool.
    ERP = "erp"
    # A laboratory or inspection system.
    LAB = "lab"
    # A shop-floor terminal or data-collection app people type into.
    TERMINAL = "terminal"
    # Something else. The `source` string says what.
    OTHER = "other"


class InboundEventBase(BaseModel):
    """The four provenance facts every inbound event carries."""

    model_config = ConfigDict(extra="forbid")

    source: str
    source_kind: InboundSourceKind
    external_key: str
    recorded_at: datetime

    @field_validator("source", "external_key")
    @classmethod
    def _not_blank(cls, value: str, info) -> str:
        value = value.strip()
        if not value:
            raise ValueError(
                f"{info.field_name} is empty; an inbound event that cannot say which system "
                "supplied it, and under what id, cannot be deduplicated and is not accepted"
            )
        return value

    @field_validator("recorded_at")
    @classmethod
    def _aware_times_are_normalised(cls, value: datetime) -> datetime:
        return drop_zone(value)


def drop_zone(value: datetime) -> datetime:
    """An aware timestamp as naive UTC — this system's one time convention.

    A naive timestamp is passed through unchanged and is the *driver's*
    problem: it is the driver that knows, from configuration, which zone the
    supplying system writes in. Guessing here is how every stop in a shift
    ends up an hour out.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


class DowntimeLabel(InboundEventBase):
    """A stop, named by whoever was standing in front of the machine.

    The MES sees a machine stop. It cannot see why. This is the why,
    supplied by the system the technician actually typed into.
    """

    equipment: str
    started_at: datetime
    # Null means the supplier says the stop is still open. It does not mean
    # "ended now", and it is never filled in on the supplier's behalf.
    ended_at: datetime | None = None
    reason: str
    note: str | None = None

    @field_validator("started_at", "ended_at")
    @classmethod
    def _times(cls, value: datetime | None) -> datetime | None:
        return None if value is None else drop_zone(value)

    @field_validator("reason")
    @classmethod
    def _reason_is_a_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError(
                "reason is empty; an unlabelled stop is already what the MES has, and "
                "recording a blank label would make it look labelled"
            )
        return value

    @model_validator(mode="after")
    def _ends_after_it_starts(self) -> DowntimeLabel:
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError(f"the stop ends ({self.ended_at}) before it starts ({self.started_at})")
        return self


class QualityResult(InboundEventBase):
    """One measurement, taken elsewhere.

    Addressed by machine or by order, because inspection systems are
    organised both ways and neither is wrong. At least one is required:
    a reading attached to nothing cannot be compared with anything.
    """

    equipment: str | None = None
    order: str | None = None
    characteristic: str
    value: float
    unit: str | None = None
    # The supplier's own reference for the specification it measured
    # against. Kept as text; this MES does not resolve another system's
    # spec ids into its own.
    spec_reference: str | None = None
    # The instrument, by whatever code the supplier uses. Matched against
    # this MES's gauges if it knows one by that code, and left unmatched —
    # and said so — if it does not.
    gauge: str | None = None
    # The supplier's verdict, when it sends one. Null means it sent none;
    # it never means "pass".
    passed: bool | None = None
    # Who took it, as an opaque id. Names are the plant's to hold, not this
    # repository's, and a badge number compares just as well.
    inspector: str | None = None

    @field_validator("characteristic")
    @classmethod
    def _characteristic(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("characteristic is empty; a number with no name is not a measurement")
        return value

    @model_validator(mode="after")
    def _attached_to_something(self) -> QualityResult:
        if not (self.equipment or self.order):
            raise ValueError(
                "the result names neither equipment nor an order; there is nothing to attach it to"
            )
        return self


class ManualCount(InboundEventBase):
    """Units somebody counted and typed into another system.

    Both quantities are required. A count that states good and says nothing
    about scrap leaves scrap unknown, and the production log has no way to
    hold "unknown" — writing zero there would be this MES asserting a fact
    nobody supplied. A supplier that genuinely never reports scrap says so
    once, in the mapping file, as a default a person wrote down.
    """

    equipment: str
    order: str | None = None
    good: float
    scrap: float
    # When the units were made, if the supplier distinguishes that from when
    # it recorded them. Null means it does not, and `recorded_at` is used —
    # the supplier's own clock either way, never ours.
    when: datetime | None = None

    @field_validator("when")
    @classmethod
    def _times(cls, value: datetime | None) -> datetime | None:
        return None if value is None else drop_zone(value)

    @field_validator("good", "scrap")
    @classmethod
    def _not_negative(cls, value: float, info) -> float:
        if value < 0:
            raise ValueError(f"{info.field_name} is negative ({value}); a count cannot be less than nothing")
        return value

    @property
    def made_at(self) -> datetime:
        """The best-supported time for the units, and never this MES's clock."""
        return self.when or self.recorded_at


InboundEvent = DowntimeLabel | QualityResult | ManualCount

#: The contract's three shapes, by the name a driver's configuration uses
#: for the stream that carries them.
EVENT_TYPES: dict[str, type[BaseModel]] = {
    "downtime": DowntimeLabel,
    "quality": QualityResult,
    "counts": ManualCount,
}
