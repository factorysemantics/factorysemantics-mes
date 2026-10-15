"""Who holds each one-at-a-time job in this plant, and how far each machine
counter has already been booked.

Two tables with one purpose: letting a plant run on more than one box without
counting anything twice. See decision 0042.

`plant_leases` is the lease. One row per role, the holder's node id in it, and
an expiry that is only ever set from the database's own clock - never from the
clock of the box that wrote it, because two boxes do not agree about the time
and the whole point of this row is that two boxes agree about who is working.

`counter_baselines` is how far the reader got. A machine counter is absolute,
so a booking is a difference against the last value already booked; keeping
that last value only in the reader's memory meant a reader taking over had to
either start from zero (inventing production) or book nothing at all until the
counter moved past wherever the dead reader had got to (losing it). It is
written in the same transaction as the booking it belongs to, so the two
cannot disagree. It also makes a plain single-box restart honest, which it was
not before.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from fsmes.db import Base, utcnow

# The reader: everything `fsmes run-opc-agent` does - subscribing to the
# machines, writing tag history, booking counter deltas, writing the order
# code and approved setpoints down to the PLC.
ROLE_READER = "reader"
# The simulated floor (`fsmes run-operations`). A real plant has no such role;
# a demo plant must not raise two of every inspection.
ROLE_FLOOR = "floor"
# The hourly tag-history prune, inside the API process. Every node runs the
# API, so this is the one role a plant that only serves still has.
ROLE_HOUSEKEEPING = "housekeeping"

ROLES: tuple[str, ...] = (ROLE_READER, ROLE_FLOOR, ROLE_HOUSEKEEPING)


class PlantLease(Base):
    """One role, and the node that may do it until `expires_at`."""

    __tablename__ = "plant_leases"

    role: Mapped[str] = mapped_column(String(40), primary_key=True)
    holder: Mapped[str] = mapped_column(String(80))
    # When this holder first took the role, kept across its own renewals: the
    # answer to "how long has this node been the reader", which is a different
    # question from "when did it last say so".
    acquired_at: Mapped[datetime] = mapped_column(default=utcnow)
    renewed_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(default=utcnow)


class CounterBaseline(Base):
    """The last value of one machine counter that has already been booked.

    Keyed by the machine's code and the tag name rather than by an equipment
    id, because that is the key the reader measures deltas against and a tag
    map may name a machine this plant's master data does not have.
    """

    __tablename__ = "counter_baselines"

    equipment_code: Mapped[str] = mapped_column(String(40), primary_key=True)
    tag: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger)
    # Which node booked up to here. Null is *not recorded* - a baseline
    # written by a plant that does not run redundantly has no node to name.
    node: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
