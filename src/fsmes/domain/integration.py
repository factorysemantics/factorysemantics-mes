"""ERP message log — a durable inbox/outbox so no exchange with the ERP is ever lost.

Outbound messages are written here first (status=pending) and the sync worker
delivers them; inbound payloads are recorded here as they are processed. This
is the classic transactional-outbox pattern, and it doubles as the integration
audit trail an ERP interface is expected to keep.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from fsmes.db import Base, utcnow
from fsmes.domain.common import str_enum


class MessageDirection(enum.StrEnum):
    IN = "in"
    OUT = "out"


class MessageStatus(enum.StrEnum):
    PENDING = "pending"
    SENT = "sent"
    PROCESSED = "processed"
    ERROR = "error"
    # Retried until the limit and still failing. Not pending, so the queue
    # does not carry it forever; not deleted, because it is a fact the ERP
    # never received and somebody has to decide about.
    DEAD = "dead"


class ErpMessage(Base):
    __tablename__ = "erp_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    direction: Mapped[MessageDirection] = mapped_column(str_enum(MessageDirection))
    kind: Mapped[str] = mapped_column(String(40))  # e.g. production_schedule, production_confirmation
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[MessageStatus] = mapped_column(str_enum(MessageStatus), default=MessageStatus.PENDING, index=True)
    error: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    processed_at: Mapped[datetime | None]
    # Delivery discipline. A message that keeps failing backs off, and after
    # enough attempts is declared dead rather than retried every five seconds
    # until the end of time - which is what the outbox did before.
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime | None]
    # order + step + kind: the same event queued twice is the same message.
    message_key: Mapped[str | None] = mapped_column(String(120), unique=True)
