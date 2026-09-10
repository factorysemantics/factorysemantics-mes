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


class InboundWatermark(Base):
    """How far a polling driver has read one supplier's table, so it can stop.

    The folder driver needs nothing like this: a file is read once and moved.
    A poller has no such mark on the world, so it keeps its own — the last
    value of the supplier's own ordering column that it has read *through*.

    The mark is kept as the text the supplier's column gave, not as a value
    of this MES's own making. A supplier whose timestamps are local, or whose
    ids are strings, must get its own value back unchanged in the next query;
    a number this MES normalised would silently move the boundary and skip or
    repeat a shift's worth of rows.

    It is a cursor, not the record of what was applied. That record is
    `inbound_events`, keyed on the supplier's own id, and it is what actually
    makes a second read of the same row a no-op. So a watermark that is
    behind costs a re-read and changes nothing, which is the direction a
    cursor should fail in.

    `held_reason` is set when a row could not be recorded: the cursor stops
    at that row rather than stepping over it, because a row nothing was done
    with is not a row that was read.
    """

    __tablename__ = "inbound_watermarks"
    __table_args__ = (
        UniqueConstraint("source", "stream", name="uq_inbound_watermark_source_stream"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The supplying system, as the configuration names it. Part of the
    # identity: pointing a stream at a different system starts a new cursor
    # rather than inheriting a position that means nothing there.
    source: Mapped[str] = mapped_column(String(80), index=True)
    # `downtime`, `quality` or `counts` — the contract's stream names.
    stream: Mapped[str] = mapped_column(String(30))
    # The supplier's own ordering value, as text, read through and inclusive
    # of: the query asks for rows strictly after it.
    position: Mapped[str] = mapped_column(String(120))
    # `id` or `timestamp`, from the configuration. Kept so the value can be
    # handed back to the supplier's database as the type its column holds.
    position_type: Mapped[str] = mapped_column(String(20))
    # Rows this cursor's passes have taken - recorded, or found already
    # recorded. A total, stated. It is not the position: a pass whose cursor
    # is held still takes the rows after the one holding it.
    rows_seen: Mapped[int] = mapped_column(default=0)
    # Why the cursor is not moving, and which of the supplier's rows holds it.
    # Null means nothing is holding it.
    held_reason: Mapped[str | None] = mapped_column(String(300))
    held_key: Mapped[str | None] = mapped_column(String(120))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
