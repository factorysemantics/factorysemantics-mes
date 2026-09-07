"""Idempotency keys: a repeated command returns its first answer.

Principle 3 says command tools are idempotent. Networks retry, agents
retry, people double-click; without this a retried "create order" is a
second order. A client that sends `Idempotency-Key` gets the stored
response back for the same key, scoped to the account that sent it, so
two people cannot collide on a short key.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fsmes.db import Base, utcnow


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("actor", "key", name="uq_idempotency_actor_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(40))
    key: Mapped[str] = mapped_column(String(120))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(200))
    status_code: Mapped[int]
    body: Mapped[dict | list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
