"""How far each machine counter has already been booked.

A machine counter is absolute: the PLC says "this filler has made 412,905
bottles", and production is the difference against the last value the MES
already booked. Keeping that last value only in the reader's memory had two
consequences, and both of them are house rule 1 read one way or the other:

* A reader that restarts has no baseline. The next reading is treated as a
  PLC reset (decision: the units around a reset are unknowable, so none are
  booked), so everything the machine made while the reader was away is lost -
  silently, with the plant's own day wrong about itself.
* A *second* reader has its own baseline and books the whole delta again, so
  a plant on two boxes counts everything twice.

So the baseline is a row, written in the same transaction as the booking it
belongs to. The two cannot disagree: either both are there or neither is.
A reader taking over seeds itself from these rows - never from zero, never
from another process's memory.

This runs on a single-box plant too, where it is simply the honest version of
what the reader already did.
"""

from __future__ import annotations

from sqlalchemy import select, update

from fsmes.db import utcnow
from fsmes.domain.leases import CounterBaseline

__all__ = ["load", "save"]


def load(session) -> dict[tuple[str, str], int]:
    """Every baseline, keyed the way the reader measures deltas: (machine
    code, tag name). An empty mapping is an empty mapping - a plant that has
    never booked a counter - and not a set of zeroes."""
    rows = session.execute(
        select(CounterBaseline.equipment_code, CounterBaseline.tag,
               CounterBaseline.value)).all()
    return {(code, tag): int(value) for code, tag, value in rows}


def save(session, baselines: dict[tuple[str, str], int], node: str | None = None) -> None:
    """Write these baselines in the caller's transaction.

    Called with the set of counter values a booking consumed, from inside the
    session that booked them, so "the baseline moves last" now means it moves
    last *and* durably rather than last in one process's memory.

    An UPDATE first and an INSERT only when no row was there: no dialect's
    upsert, so PostgreSQL and SQLite take the same path. A unique violation on
    the insert can only happen if two readers booked the same machine at the
    same moment, which is the thing the lease exists to prevent - it fails the
    whole batch, which the reader's retry re-measures from the baseline that
    is actually in the database.
    """
    if not baselines:
        return
    now = utcnow()
    for (code, tag), value in baselines.items():
        done = session.execute(
            update(CounterBaseline)
            .where(CounterBaseline.equipment_code == code, CounterBaseline.tag == tag)
            .values(value=int(value), node=node, updated_at=now)
            .execution_options(synchronize_session=False))
        if not done.rowcount:
            session.add(CounterBaseline(equipment_code=code, tag=tag, value=int(value),
                                        node=node, updated_at=now))
