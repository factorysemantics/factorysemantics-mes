"""The last time you played: the latest stretch the MES watched without a break.

A plant fed by a game is not run in shifts. It runs while somebody plays, and
"how did it go" means *the last time I played*, not the last eight hours, most
of which the game was closed. So a reader that knows when its feed was live
writes it down, and this module turns that into a window.

THE READING. One plant reading, `FEED.Stretch`, per poll that the reader
applied with a building list and the game not paused:

* **0** - the first poll of a stretch: the reader had just started, or the
  previous poll was a pause, or the polls had stopped for longer than the gap.
* **1** - the poll continues the stretch the one before it began.

Nothing is booked across a gap or a pause, and the same rule draws a
stretch's ends, so **watched time** (the sum of the intervals that end in a 1)
is exactly the time the reader could book units in.

THE WINDOW. The latest stretch with at least two polls: from its 0 to its last
1. A stretch of one poll has no watched time in it and is passed over. A
reader that restarts mid-play starts a new stretch, because it books nothing
in the interval its restart spans.

A plant with no stretch readings has no last-play window, and asking for one
is refused in one sentence: unknown is not zero, and a window nobody watched
is not "the last time you played".
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.domain.timeseries import TagValue

#: The plant reading a reader writes once per watched poll; see the module.
STRETCH = "FEED.Stretch"
#: What a caller passes as `shift=` to ask for this window.
SPEC = "last_play"
#: The code the window carries as a `Shift`, so screens can tell it apart.
CODE = "LAST_PLAY"
NAME = "last played"


def _rows(db: Session, *conditions):
    return select(TagValue.ts, TagValue.value_num).where(
        TagValue.equipment_id.is_(None), TagValue.tag == STRETCH, *conditions)


def has_stretches(db: Session) -> bool:
    """Whether any reader ever wrote a stretch reading on this plant."""
    return db.scalar(select(TagValue.id).where(
        TagValue.equipment_id.is_(None), TagValue.tag == STRETCH).limit(1)) is not None


def latest(db: Session) -> tuple[datetime, datetime] | None:
    """The latest stretch with watched time in it, as (first poll, last poll);
    None when the plant has none."""
    end = db.scalar(select(TagValue.ts).where(
        TagValue.equipment_id.is_(None), TagValue.tag == STRETCH,
        TagValue.value_num == 1).order_by(TagValue.ts.desc()).limit(1))
    if end is None:
        return None
    start = db.scalar(select(TagValue.ts).where(
        TagValue.equipment_id.is_(None), TagValue.tag == STRETCH,
        TagValue.value_num == 0, TagValue.ts <= end).order_by(TagValue.ts.desc()).limit(1))
    return (start or end), end


def watched_seconds(db: Session, start: datetime, end: datetime) -> float | None:
    """Seconds of the window the reader watched: every interval between two
    polls of one stretch, clipped to the window. None when the plant has no
    stretch readings at all - not zero, because nobody said."""
    if not has_stretches(db):
        return None
    before = db.execute(_rows(db, TagValue.ts <= start)
                        .order_by(TagValue.ts.desc()).limit(1)).first()
    rows = db.execute(_rows(db, TagValue.ts > start, TagValue.ts <= end)
                      .order_by(TagValue.ts)).all()
    previous = before.ts if before is not None else None
    total = 0.0
    for ts, value in rows:
        if value == 1 and previous is not None:
            total += (ts - max(previous, start)).total_seconds()
        previous = ts
    return total


__all__ = ["CODE", "NAME", "SPEC", "STRETCH", "has_stretches", "latest", "watched_seconds"]
