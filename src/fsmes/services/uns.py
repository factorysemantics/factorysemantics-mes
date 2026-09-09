"""The MES side of the unified namespace: which events are owed to the
broker, and what happened to each.

There is one event stream in this system — the transactional outbox that
the ERP already delivers from — and this adds a second reader of it, not a
second copy of it. A reader needs its own progress, because the ERP marks
its progress on the message itself and two consumers cannot share that
column. So every outbox message is enrolled here exactly once, and the
publication row carries the attempts, the backoff and the death that the
outbox carries for the ERP.

Nothing in here talks to a broker. Transport is
`fsmes.integrations.uns.transport`, the same split as `services.erp` and
the ERP adapters.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import ErpMessage, MessageDirection, MessageStatus, UnsPublication
from fsmes.services import Invalid, NotFound, audit

MAX_ATTEMPTS = 8
BASE_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 3600


def enrol(session: Session, limit: int = 1000) -> list[UnsPublication]:
    """Take on every outbox message that has no publication row yet.

    Outbound only: an inbound message is an order the ERP sent us, and it
    reaches the namespace as the confirmations the plant produces against
    it, not as an echo of the ERP's own request.

    This is an anti-join over the outbox rather than a high-water mark on
    the id. A watermark would be cheaper and wrong: outbox rows are written
    by several workers and commit out of id order, so a lower id can become
    visible after a higher one has already been published, and a watermark
    would step straight over it. Correct beats cheap at the plant sizes this
    runs at; a bounded cursor is the change to make if an outbox ever grows
    past a scan.
    """
    missing = session.scalars(
        select(ErpMessage)
        .outerjoin(UnsPublication, UnsPublication.message_id == ErpMessage.id)
        .where(ErpMessage.direction == MessageDirection.OUT, UnsPublication.id.is_(None))
        .order_by(ErpMessage.id)
        .limit(limit)).all()
    enrolled = [UnsPublication(message_id=message.id) for message in missing]
    session.add_all(enrolled)
    session.flush()
    return enrolled


def due(session: Session, limit: int = 200, now: datetime | None = None
        ) -> list[tuple[UnsPublication, ErpMessage]]:
    """What is owed to the broker right now: pending, and not backing off.

    Oldest first. A namespace that delivers a shift out of order is worse
    than one that is an hour behind.
    """
    now = now or utcnow()
    rows = session.execute(
        select(UnsPublication, ErpMessage)
        .join(ErpMessage, ErpMessage.id == UnsPublication.message_id)
        .where(UnsPublication.status == MessageStatus.PENDING,
               (UnsPublication.next_attempt_at.is_(None))
               | (UnsPublication.next_attempt_at <= now))
        .order_by(UnsPublication.message_id)
        .limit(limit))
    return [(publication, message) for publication, message in rows]


def backoff_seconds(attempts: int) -> int:
    """5 s, 10 s, 20 s ... capped at an hour. The outbox's own curve."""
    return min(BASE_BACKOFF_SECONDS * 2 ** max(0, attempts - 1), MAX_BACKOFF_SECONDS)


def mark_published(publication: UnsPublication, topic: str) -> UnsPublication:
    publication.status = MessageStatus.SENT
    publication.topic = topic[:400]
    publication.error = None
    publication.next_attempt_at = None
    publication.published_at = utcnow()
    return publication


def mark_error(publication: UnsPublication, error: Exception, topic: str | None = None,
               now: datetime | None = None) -> UnsPublication:
    """Record the failure, back off, and after enough attempts stop trying.

    Dead is not deleted. An event the plant's namespace never received is a
    fact somebody has to decide about, and `fsmes uns publish` will not
    quietly drop it to keep the queue looking tidy.
    """
    now = now or utcnow()
    publication.attempts = (publication.attempts or 0) + 1
    publication.error = str(error)[:400]
    if topic:
        publication.topic = topic[:400]
    if publication.attempts >= MAX_ATTEMPTS:
        publication.status = MessageStatus.DEAD
        publication.next_attempt_at = None
    else:
        publication.next_attempt_at = now + timedelta(seconds=backoff_seconds(publication.attempts))
    return publication


def retry(session: Session, publication_id: int, actor: str = "system") -> UnsPublication:
    """Put a dead or backing-off publication back in the queue, now."""
    publication = session.get(UnsPublication, publication_id)
    if publication is None:
        raise NotFound(f"no UNS publication {publication_id}")
    if publication.status is MessageStatus.SENT:
        raise Invalid(f"UNS publication {publication_id} was already published")
    before = publication.status.value
    publication.status = MessageStatus.PENDING
    publication.next_attempt_at = None
    audit.record(session, actor=actor, action="uns.retried", entity_type="uns_publication",
                 entity_id=str(publication_id),
                 before={"status": before, "attempts": publication.attempts},
                 after={"status": "pending"})
    return publication


def queue_summary(session: Session, limit: int = 30) -> dict:
    """The queue as a person needs to see it, totals first.

    `enrolled` is the total this reports against: a list of thirty recent
    rows out of an unstated number is how a backlog hides.
    """
    counts = {status.value: 0 for status in MessageStatus}
    for status, n in session.execute(
            select(UnsPublication.status, func.count()).group_by(UnsPublication.status)):
        counts[status.value] = n
    enrolled = sum(counts.values())
    outbox_total = session.scalar(select(func.count()).select_from(ErpMessage)
                                  .where(ErpMessage.direction == MessageDirection.OUT)) or 0
    oldest = session.scalar(select(func.min(UnsPublication.created_at))
                            .where(UnsPublication.status == MessageStatus.PENDING))
    recent = session.scalars(
        select(UnsPublication).order_by(UnsPublication.id.desc()).limit(limit)).all()
    return {
        "enrolled": enrolled,
        "outbox_outbound": outbox_total,
        "not_yet_enrolled": max(outbox_total - enrolled, 0),
        "counts": counts,
        "oldest_pending_seconds": round((utcnow() - oldest).total_seconds(), 1) if oldest else None,
        "showing": min(limit, enrolled),
        "recent": [
            {"id": p.id, "message_id": p.message_id, "status": p.status.value, "topic": p.topic,
             "attempts": p.attempts, "error": p.error, "created_at": p.created_at,
             "published_at": p.published_at, "next_attempt_at": p.next_attempt_at}
            for p in recent
        ],
    }
