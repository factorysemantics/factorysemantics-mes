"""The unified-namespace worker: one loop, one direction.

Enrol whatever the outbox has produced since last time, publish what is
due, record what happened. Delivery is at-least-once with the outbox's own
retry and backoff; a message the broker never accepts is declared dead
rather than retried forever.

Discipline borrowed from the ERP sync and worth repeating: a database
transaction NEVER spans a publish. The session that reads what is due is
closed before the first byte reaches the broker, and each result is written
in a transaction of its own, so a broker that has gone away cannot hold a
lock the plant floor is waiting on.
"""

from __future__ import annotations

import asyncio

import structlog

from fsmes.config import Settings
from fsmes.db import session_scope, utcnow
from fsmes.domain import UnsPublication
from fsmes.integrations.uns.envelope import encode
from fsmes.integrations.uns.topics import equipment_code_in, topic_for
from fsmes.integrations.uns.transport import UnsTransport
from fsmes.services import uns

log = structlog.get_logger("uns.publish")


async def cycle(transport: UnsTransport, scope, settings: Settings) -> dict:
    """One pass. `scope` is a callable returning a session context manager
    (session_scope in production; tests inject their own).

    Returns what it did, counted: enrolled, published, failed. Every number
    is stated even when it is zero, because "published: 0, failed: 12" and
    "nothing to do" are different mornings.
    """
    with scope() as session:
        enrolled = len(uns.enrol(session))

    # Build the topics and the bytes while the session is open; publish after
    # it is closed. Master data lookups are local and cheap, the broker is
    # neither.
    with scope() as session:
        work = [
            (publication.id,
             message.id,
             topic_for(session, settings, kind=message.kind,
                       equipment_code=equipment_code_in(message.payload)),
             encode(message, settings, utcnow()))
            for publication, message in uns.due(session, limit=settings.uns_batch)
        ]

    published = failed = 0
    for publication_id, message_id, topic, payload in work:
        try:
            await transport.publish(topic, payload, qos=settings.uns_qos,
                                    retain=settings.uns_retain)
        except Exception as exc:
            failed += 1
            with scope() as session:
                publication = uns.mark_error(session.get(UnsPublication, publication_id), exc, topic)
                status, attempts = publication.status.value, publication.attempts
            log.warning("publish failed", topic=topic, event_id=message_id,
                        attempts=attempts, status=status, error=str(exc))
        else:
            published += 1
            with scope() as session:
                uns.mark_published(session.get(UnsPublication, publication_id), topic)
            log.debug("published", topic=topic, event_id=message_id)

    if enrolled or published or failed:
        log.info("unified namespace cycle",
                 enrolled=enrolled, published=published, failed=failed, due=len(work))
    return {"enrolled": enrolled, "published": published, "failed": failed, "due": len(work)}


async def run(transport: UnsTransport, settings: Settings) -> None:
    """Publish until stopped. A cycle that raises is logged and retried on
    the next tick: this process outliving the broker is the whole point."""
    log.info("unified namespace publisher online",
             transport=type(transport).__name__, prefix=settings.uns_topic_prefix,
             poll_seconds=settings.uns_poll_seconds)
    try:
        # A broker that is down at start-up is not a reason to refuse to
        # start: the events keep queueing and the first cycle reconnects.
        try:
            await transport.connect()
        except Exception as exc:
            log.warning("broker not reachable yet; events will queue", error=str(exc))
        while True:
            try:
                await cycle(transport, session_scope, settings)
            except Exception:
                log.exception("unified namespace cycle failed")
            await asyncio.sleep(settings.uns_poll_seconds)
    finally:
        await transport.close()
