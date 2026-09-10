"""What another system has already told this MES, so it is never told twice.

Every inbound event carries its supplier's own identifier. This table is the
record of which of those have been applied, and to what. It is what makes
dropping the same file in the folder twice a no-op, and what lets a person
answer "where did this booking come from" from the other end.

The identity is `(source, kind, external_key)`. The supplier's key is only
promised to be unique inside that supplier's own numbering for that kind of
record: an incumbent MES whose downtime rows and count rows both start at 1
is ordinary, and treating those two rows as the same event would drop half
the data silently, which is the worst way to lose it.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fsmes.db import Base, utcnow
from fsmes.domain.common import str_enum


class InboundKind(enum.StrEnum):
    DOWNTIME_LABEL = "downtime_label"
    QUALITY_RESULT = "quality_result"
    MANUAL_COUNT = "manual_count"


class InboundEvent(Base):
    __tablename__ = "inbound_events"
    __table_args__ = (
        UniqueConstraint("source", "kind", "external_key", name="uq_inbound_source_kind_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The free name of the system that supplied it, e.g. `replay:incumbent-mes`.
    source: Mapped[str] = mapped_column(String(80), index=True)
    kind: Mapped[InboundKind] = mapped_column(str_enum(InboundKind))
    # The supplier's own id for the record. Not ours; we never mint it.
    external_key: Mapped[str] = mapped_column(String(120))
    # When the supplying system recorded it — not when we read it.
    recorded_at: Mapped[datetime]
    # What it produced here, so the trail runs both ways. Null when the event
    # was accepted and changed nothing that has an id of its own.
    entity_type: Mapped[str | None] = mapped_column(String(30))
    entity_id: Mapped[str | None] = mapped_column(String(60))
    # One sentence: what happened to it. Read back by the second attempt.
    detail: Mapped[str | None] = mapped_column(String(300))
    applied_at: Mapped[datetime] = mapped_column(default=utcnow)
