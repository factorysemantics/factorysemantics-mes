"""The unified-namespace worker: one loop, one direction.

Enrol whatever the outbox has produced since last time, publish what is
due, record what happened. Delivery is at-least-once with the outbox's own
retry and backoff; a message the broker never accepts is declared dead
rather than retried forever.

Discipline borrowed from the ERP sync and worth repeating: a database
transaction NEVER spans a publish. The session that reads what is due is
closed before the first byte reaches the broker, and the results are written
in a transaction opened after the last publish has come back, so a broker
that has gone away cannot hold a lock the plant floor is waiting on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import structlog

from fsmes.config import Settings
from fsmes.db import session_scope, utcnow
from fsmes.integrations.uns.envelope import encode
from fsmes.integrations.uns.topics import TopicResolver, equipment_code_in
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

    # One clock read for the whole batch. The envelope carries a
    # `published_at` and so does the publication row; reading the clock twice
    # meant the database and the consumer disagreed about the same event.
    published_at = utcnow()

    # Build the topics and the bytes while the session is open; publish after
    # it is closed. Master data lookups are local and cheap, the broker is
    # neither — and one resolver for the batch keeps them to one per machine
    # rather than one per event.
    with scope() as session:
        resolver = TopicResolver(session, settings)
        work = [
            (publication.id,
             message.id,
             resolver.topic(kind=message.kind,
                            equipment_code=equipment_code_in(message.payload)),
             encode(message, settings, published_at))
            for publication, message in uns.due(session, limit=settings.uns_batch)
        ]

    outcomes: list[uns.Outcome] = []
    published = failed = 0
    for group in _in_flight(work, settings.uns_inflight):
        results = await asyncio.gather(
            *(transport.publish(topic, payload, qos=settings.uns_qos,
                                retain=settings.uns_retain)
              for _publication_id, _message_id, topic, payload in group),
            return_exceptions=True)
        for (publication_id, message_id, topic, _payload), result in zip(group, results, strict=True):
            if isinstance(result, asyncio.CancelledError):
                # Somebody is stopping the worker, not a broker refusing an
                # event. Recording it as a failed publish would spend one of
                # the eight attempts on a shutdown.
                raise result
            if isinstance(result, BaseException):
                failed += 1
                outcomes.append(uns.Outcome(publication_id, message_id, topic, result))
            else:
                published += 1
                outcomes.append(uns.Outcome(publication_id, message_id, topic))

    if outcomes:
        with scope() as session:
            written = uns.record(session, outcomes, now=published_at)
            for outcome in outcomes:
                publication = written.get(outcome.publication_id)
                if outcome.error is None:
                    log.debug("published", topic=outcome.topic, event_id=outcome.message_id)
                elif publication is None:
                    log.warning("publish failed and its publication is gone",
                                topic=outcome.topic, event_id=outcome.message_id,
                                error=str(outcome.error))
                else:
                    log.warning("publish failed", topic=outcome.topic,
                                event_id=outcome.message_id, attempts=publication.attempts,
                                status=publication.status.value, error=str(outcome.error))

    if enrolled or published or failed:
        log.info("unified namespace cycle",
                 enrolled=enrolled, published=published, failed=failed, due=len(work))
    return {"enrolled": enrolled, "published": published, "failed": failed, "due": len(work)}


def _in_flight(work: list, size: int) -> Iterator[list]:
    """The batch in groups small enough for the client to hold in flight.

    QoS 1 means every publish waits for the broker to acknowledge it. Awaited
    one at a time that is one round trip per event, so a namespace on the far
    side of a plant network moved at the speed of its latency and not its
    bandwidth. A group goes out together and is acknowledged together;
    per-connection order is preserved, and the group is bounded because an
    MQTT client has a fixed number of unacknowledged messages it will hold.

    `uns_inflight` of 1 restores the old one-at-a-time behaviour exactly.
    """
    size = max(1, size)
    for start in range(0, len(work), size):
        yield work[start:start + size]


def draining(counted: dict | None, settings: Settings) -> bool:
    """Did that cycle leave more behind it than it took?

    `uns_poll_seconds` is how long to wait when the plant is quiet. After an
    outage it is the wrong wait entirely: a cycle that filled its batch has
    emptied `uns_batch` events with more still queued, so sleeping between
    cycles drained a backlog at `uns_batch / uns_poll_seconds` events a
    second however fast the broker was — two hours of a busy line took the
    best part of an hour to catch up.

    A full batch with nothing failed goes straight back for the next one.
    Anything else waits: a batch that did not fill means the queue is empty,
    and a batch with a failure in it means the broker is unhappy, which is
    what the backoff is for.
    """
    if not counted or not counted["due"]:
        return False
    return counted["due"] >= settings.uns_batch and not counted["failed"]


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
            counted = None
            try:
                counted = await cycle(transport, session_scope, settings)
            except Exception:
                log.exception("unified namespace cycle failed")
            if draining(counted, settings):
                # Straight back for the next batch, but through the event
                # loop, so a backlog cannot starve everything else here.
                await asyncio.sleep(0)
            else:
                await asyncio.sleep(settings.uns_poll_seconds)
    finally:
        await transport.close()
