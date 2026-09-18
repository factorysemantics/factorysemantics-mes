"""Process values collected from the machine layer (OPC tags)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from fsmes.db import Base, utcnow


class TagValue(Base):
    __tablename__ = "tag_values"
    __table_args__ = (
        Index("ix_tag_values_tag_ts", "tag", "ts"),
        # The latest-value queries every machine card and tag screen make:
        # ORDER BY id DESC LIMIT 1 per (machine, tag). Without this the cost
        # of a dashboard grew with the length of history.
        Index("ix_tag_values_equipment_tag_id", "equipment_id", "tag", "id"),
        # The same question with the tag *not* named: the machine card asks a
        # plant whose tag map does not declare a process value for "whatever
        # this machine last published that is not one of the structural tags",
        # which is ORDER BY id DESC LIMIT 1 per machine. The index above
        # cannot serve that order across tags, so the database read every row
        # the machine had ever written and sorted them - 100 ms for six
        # machines on eight hours of one-second history, growing with the
        # history and not with the question.
        Index("ix_tag_values_eq_id", "equipment_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_id: Mapped[int | None] = mapped_column(ForeignKey("equipment.id"))
    tag: Mapped[str] = mapped_column(String(80))
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    value_num: Mapped[float | None]
    value_text: Mapped[str | None] = mapped_column(String(120))
