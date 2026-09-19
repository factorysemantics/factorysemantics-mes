"""Equipment state history — the raw material for availability and downtime analysis."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fsmes.db import Base, utcnow
from fsmes.domain.common import ShiftStamped, str_enum
from fsmes.domain.masterdata import Equipment


class EquipmentStateName(enum.StrEnum):
    RUNNING = "running"
    IDLE = "idle"
    DOWN = "down"
    SETUP = "setup"


class EquipmentState(ShiftStamped, Base):
    """One contiguous stretch in a state. The open interval (ended_at IS NULL)
    is the equipment's current state."""

    __tablename__ = "equipment_states"
    __table_args__ = (
        Index("ix_equipment_states_eq_started", "equipment_id", "started_at"),
        # "Which intervals are open" and "which overlap the last eight hours"
        # both ask about ended_at. Without this, each was a scan of every
        # interval the plant ever recorded - 1.4 million after a day of a
        # 108-station plant, three quarters of a second per Floor refresh.
        Index("ix_equipment_states_ended", "ended_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"))
    state: Mapped[EquipmentStateName] = mapped_column(str_enum(EquipmentStateName))
    reason: Mapped[str | None] = mapped_column(String(120))
    # The code from the plant's approved downtime vocabulary, when the label
    # came from the list. Beside `reason`, never instead of it: the three
    # other writers of a reason - triggers, inbound feeds and agent tools -
    # keep writing text, every interval recorded before the plant had a
    # vocabulary keeps exactly the text it has, and the pareto says how much
    # of a window came from the list and how much did not rather than
    # pretending. Null is the normal case on a plant that has not named its
    # reasons yet, and stays null on one that never does.
    reason_code: Mapped[str | None] = mapped_column(String(40))
    # Who named the stop. The interval is always this MES's own observation;
    # the label on it may have been supplied by another system, and a pareto
    # that cannot tell the two apart is a pareto nobody can audit.
    reason_source: Mapped[str | None] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    ended_at: Mapped[datetime | None]
    # `shift_code`/`shift_day` (ShiftStamped) are the shift the interval
    # *began* in. An interval that runs past a shift boundary is not split
    # here - it is one thing the machine did - and per-shift reporting clips
    # it to the window instead, so its seconds land on both shifts in the
    # proportion the machine actually spent there.

    equipment: Mapped[Equipment] = relationship()


class ConnectionStateName(enum.StrEnum):
    """Whether this MES can currently see the machine. Two values, and they
    are not production states — see decision 0030."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


class EquipmentConnection(Base):
    """One contiguous stretch of being able - or unable - to see a machine.

    The same shape as `EquipmentState` because it answers the same kind of
    question: the open interval (ended_at IS NULL) is now, and the closed ones
    are history a window can be re-read against.

    It is a *separate* history because it is a separate fact. A machine can be
    broken and reachable, or fine and unreachable, and a plant that has to
    choose one column for both has thrown away the more useful half. A machine
    with no row here has no connection fact at all - nothing is watching it
    over a connection - and that is reported as unknown, never as connected.
    """

    __tablename__ = "equipment_connections"
    __table_args__ = (
        Index("ix_equipment_connections_eq_started", "equipment_id", "started_at"),
        # "Which machines are disconnected right now" is asked by every floor
        # refresh and by /health; the same index the state history needed.
        Index("ix_equipment_connections_ended", "ended_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"))
    state: Mapped[ConnectionStateName] = mapped_column(str_enum(ConnectionStateName))
    #: The last moment the MES had positive evidence of the link - not the
    #: moment it noticed. For a disconnection those are different, and the
    #: seconds between them are genuinely unknown.
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    #: When the MES noticed. Equal to `started_at` for a connection coming up,
    #: later than it for one going down.
    detected_at: Mapped[datetime] = mapped_column(default=utcnow)
    ended_at: Mapped[datetime | None]
    #: Why, in the words of whatever noticed: "the server did not answer",
    #: "the agent stopped". Never a code nobody can read.
    reason: Mapped[str | None] = mapped_column(String(200))
    #: What was dialled - the OPC endpoint, usually. Provenance for the same
    #: reason `opc.connected` is audited: a replay and a real server leave
    #: identical rows otherwise.
    source: Mapped[str | None] = mapped_column(String(200))

    equipment: Mapped[Equipment] = relationship()
