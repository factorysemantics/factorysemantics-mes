"""Recommended adjustments: the only way anything writes to a PLC.

Write-back is a recommendation queue, never a direct write. Analysis - an
agent, a trigger, a person - proposes: this setpoint, this value, this
rationale, this evidence. An engineer approves on screen. Only then does
the OPC agent write, and only a tag the tag map declares writable, inside
its declared bounds, with the bounds checked independently at proposal, at
approval and in the agent. After the write the system verifies whether the
process followed and records the outcome. The audit story is the feature.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from fsmes.db import Base, utcnow
from fsmes.domain.common import str_enum


class AdjustmentStatus(enum.StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"      # a person said yes; the agent has not written yet
    REJECTED = "rejected"
    WRITTEN = "written"        # the agent wrote it; verification pending
    VERIFIED = "verified"      # the process followed the setpoint
    FAILED = "failed"          # the write failed, or the process did not follow


class RecommendedAdjustment(Base):
    __tablename__ = "recommended_adjustments"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    equipment_code: Mapped[str] = mapped_column(String(40), index=True)
    tag: Mapped[str] = mapped_column(String(60))          # the setpoint
    drives: Mapped[str | None] = mapped_column(String(60))  # the process value it drives
    current_value: Mapped[float | None]                     # the setpoint when proposed
    proposed_value: Mapped[float]
    minimum: Mapped[float]                                  # bounds snapshotted from the manifest
    maximum: Mapped[float]
    rationale: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict | None] = mapped_column(JSON)     # what the proposer looked at
    proposed_by: Mapped[str] = mapped_column(String(40))
    proposed_at: Mapped[datetime] = mapped_column(default=utcnow)

    status: Mapped[AdjustmentStatus] = mapped_column(
        str_enum(AdjustmentStatus), default=AdjustmentStatus.PROPOSED, index=True)
    decided_by: Mapped[str | None] = mapped_column(String(40))
    decided_at: Mapped[datetime | None]
    decision_note: Mapped[str | None] = mapped_column(Text)

    written_at: Mapped[datetime | None]
    written_value: Mapped[float | None]
    verify_after_seconds: Mapped[float] = mapped_column(default=120.0)
    verified_at: Mapped[datetime | None]
    verification: Mapped[dict | None] = mapped_column(JSON)  # pv before/after, followed, error
