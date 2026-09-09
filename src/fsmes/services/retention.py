"""Retention: tag history is evidence, and evidence is pruned on a schedule.

Two classes of data, opposite policies (the results store made the rule):
*understanding* - bookings, states, checks, audit, scorecards - is kept for
ever; *evidence* - raw tag samples - is kept for a window and then deleted
in batches. A six-station line writes about two million rows a day at one
hertz; nothing pruned it until now, and the bottling plant's file had
reached 300 MB in three days.

Deleting is honest as long as it is said: the Ops screen shows the policy
and the oldest sample kept, so a trend that stops at the window's edge
reads as "pruned", not as "the machine was silent".
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import TagValue

BATCH = 20_000


def prune_tag_values(session: Session, keep_days: float, batch: int = BATCH) -> int:
    """Delete samples older than the window, oldest first, in batches so the
    single-writer lock is never held for long. Returns rows deleted."""
    if keep_days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=keep_days)
    deleted = 0
    while True:
        ids = session.scalars(select(TagValue.id).where(TagValue.ts < cutoff)
                              .order_by(TagValue.id).limit(batch)).all()
        if not ids:
            break
        session.execute(delete(TagValue).where(TagValue.id.in_(ids)))
        session.flush()
        deleted += len(ids)
        if len(ids) < batch:
            break
    return deleted


def report(session: Session, keep_days: float) -> dict:
    """What is held, and the policy that bounds it.

    Read from the two ends of the table, never across it. count(*), min(ts)
    and max(ts) each walked an index of the whole history - 24 million rows
    after a day of a 108-station plant, 27 seconds cold - for an answer the
    ends already give: ids are assigned in time order and pruning removes
    the oldest rows first, so the span of ids is the row count and the first
    and last rows are the oldest and newest samples.
    """
    first = session.execute(select(TagValue.id, TagValue.ts).order_by(TagValue.id).limit(1)).first()
    last = session.execute(select(TagValue.id, TagValue.ts).order_by(TagValue.id.desc()).limit(1)).first()
    rows = (last.id - first.id + 1) if first is not None else 0
    oldest = first.ts if first is not None else None
    newest = last.ts if last is not None else None
    return {
        "tag_values": rows,
        "oldest": oldest,
        "newest": newest,
        "keep_days": keep_days,
        "policy": (f"samples older than {keep_days:g} days are deleted hourly; bookings, states, "
                   f"checks and the audit trail are kept for ever") if keep_days > 0
        else "retention is off: samples are kept for ever",
    }
