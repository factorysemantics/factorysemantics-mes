"""What the unified namespace has been told, and what it still owes.

The MES has one event stream: the transactional outbox in
`fsmes.domain.integration`. The ERP delivers from it and marks its own
progress on the message itself. A second consumer cannot share that
column — the first one to succeed would hide the message from the other —
so the unified-namespace publisher keeps its progress here, one row per
outbox message it has taken on.

The delivery discipline is deliberately the outbox's own: attempts, a
backoff, and a message that is finally declared dead rather than retried
until the end of time.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from fsmes.db import Base, utcnow
from fsmes.domain.common import str_enum
from fsmes.domain.integration import MessageStatus


class UnsPublication(Base):
    __tablename__ = "uns_publications"

    id: Mapped[int] = mapped_column(primary_key=True)
    # One publication per outbox message: the unique constraint is what makes
    # enrolment safe to run every cycle.
    message_id: Mapped[int] = mapped_column(ForeignKey("erp_messages.id"), unique=True, index=True)
    # The topic it went to, kept as evidence. Null until the first attempt
    # built one.
    topic: Mapped[str | None] = mapped_column(String(400))
    status: Mapped[MessageStatus] = mapped_column(
        str_enum(MessageStatus), default=MessageStatus.PENDING, index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime | None]
    error: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    published_at: Mapped[datetime | None]
