"""Triggers: a condition on a tag, and one action from a catalog.

Principle 7 at the machine boundary: what the plant does when a signal
crosses a line is configuration, not code. A trigger is a record - which
tag, what condition, for how long, then which catalogued action - with the
draft → approved lifecycle work instructions have, because logic that fires
against a live plant is a controlled thing. Every firing is recorded, so a
trigger that never fires and one that fires every minute are both visible.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fsmes.db import Base, utcnow
from fsmes.domain.common import str_enum


class TriggerCondition(enum.StrEnum):
    ABOVE = "above"            # value > threshold
    BELOW = "below"            # value < threshold
    EQUALS = "equals"          # value == threshold
    BIT_SET = "bit_set"        # int(value) has bit `threshold` set (alarm words)
    RISES_ABOVE = "rises_above"    # crossed upward since the last reading
    FALLS_BELOW = "falls_below"    # crossed downward since the last reading


class TriggerStatus(enum.StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    WITHDRAWN = "withdrawn"


class Trigger(Base):
    __tablename__ = "triggers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    # Null equipment means "any machine that publishes this tag".
    equipment_code: Mapped[str | None] = mapped_column(String(40), index=True)
    tag: Mapped[str] = mapped_column(String(60))
    condition: Mapped[TriggerCondition] = mapped_column(str_enum(TriggerCondition))
    threshold: Mapped[float]
    # The condition must hold this long before the trigger fires. Zero fires
    # on the first reading that satisfies it.
    sustained_seconds: Mapped[float] = mapped_column(default=0.0)
    # Once fired, quiet for this long: an alarm word that stays set is one
    # event, not one per publish interval.
    cooldown_seconds: Mapped[float] = mapped_column(default=300.0)
    # One name from the catalog in services.triggers.ACTIONS.
    action: Mapped[str] = mapped_column(String(40))
    action_params: Mapped[dict | None] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text)

    status: Mapped[TriggerStatus] = mapped_column(str_enum(TriggerStatus), default=TriggerStatus.DRAFT, index=True)
    created_by: Mapped[str] = mapped_column(String(40), default="system")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    approved_by: Mapped[str | None] = mapped_column(String(40))
    approved_at: Mapped[datetime | None]
    last_fired_at: Mapped[datetime | None]
    fire_count: Mapped[int] = mapped_column(default=0)

    firings: Mapped[list[TriggerFiring]] = relationship(back_populates="trigger", cascade="all, delete-orphan")


class TriggerFiring(Base):
    __tablename__ = "trigger_firings"
    __table_args__ = (Index("ix_trigger_firings_trigger_ts", "trigger_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger_id: Mapped[int] = mapped_column(ForeignKey("triggers.id"))
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    equipment_code: Mapped[str] = mapped_column(String(40))
    tag: Mapped[str] = mapped_column(String(60))
    value: Mapped[float | None]
    action: Mapped[str] = mapped_column(String(40))
    ok: Mapped[bool] = mapped_column(default=True)
    outcome: Mapped[dict | None] = mapped_column(JSON)

    trigger: Mapped[Trigger] = relationship(back_populates="firings")
