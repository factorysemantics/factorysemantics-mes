"""Audit trail: who did what to which entity, with before/after snapshots.

This is the 21 CFR Part 11-style record every regulated MES keeps. Services
write here on every meaningful mutation; the API exposes it read-only.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from fsmes.db import Base, utcnow
from fsmes.domain.common import ShiftStamped


class AuditLog(ShiftStamped, Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(40))  # person code, "system", or an agent name
    # The person an agent acted for. Principle 3: an agent token never earns a
    # role of its own standing; what it does is done for someone, and the
    # record says who. Null for a person acting as themselves.
    on_behalf_of: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(60))  # e.g. "workorder.released"
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(60))  # business code, not the numeric PK
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    # `shift_code`/`shift_day` (ShiftStamped) are the shift the action fell in,
    # resolved when the row was written from the plant calendar as it stood
    # (decision 0028) - so a change and the production it explains can be read
    # in the same unit a plant is run in. Null is *not attributed*: a plant
    # with no shift patterns, or an instant no pattern covers. Nothing is
    # backfilled, so every row written before this column existed is null.
