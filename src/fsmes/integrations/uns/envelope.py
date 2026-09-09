"""What one MES event looks like on the wire.

JSON, one object per event, and deliberately an envelope around the outbox
payload rather than a reshaping of it: the MES publishes what it recorded.
A consumer that already understands the ERP contract understands this.

Delivery is at-least-once, so a consumer will see the same event twice
after a broker hiccup. `event_id` — the outbox row this came from — is what
it dedupes on, together with `plant` for anyone merging two MES databases
into one namespace. There is no exactly-once here and there is not going to
be; that promise cannot be kept across a network and pretending otherwise
is how a plant ends up double-booking a shift.
"""

from __future__ import annotations

import json
from datetime import datetime

from fsmes.config import Settings
from fsmes.domain import ErpMessage

SCHEMA_VERSION = 1


def envelope(message: ErpMessage, settings: Settings, published_at: datetime) -> dict:
    """One event as a plain dict."""
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "factorysemantics-mes",
        "plant": settings.plant_name or None,
        # The outbox row id: unique per MES database, and the dedupe key.
        "event_id": message.id,
        # The MES's own idempotency key for the thing that happened
        # (`WO-1004:op20`). Null for events queued before keys existed.
        "message_key": message.message_key,
        "kind": message.kind,
        "direction": message.direction.value,
        # When the MES recorded it, not when the broker heard about it. A
        # backlog delivered after an outage must not look like a burst of
        # production that happened at reconnect time.
        "recorded_at": _iso(message.created_at),
        "published_at": _iso(published_at),
        "payload": message.payload,
    }


def encode(message: ErpMessage, settings: Settings, published_at: datetime) -> bytes:
    """The envelope as the bytes that go on the topic."""
    return json.dumps(envelope(message, settings, published_at),
                      separators=(",", ":"), default=str).encode("utf-8")


def _iso(value: datetime | None) -> str | None:
    """Timestamps are naive UTC throughout the MES; say so on the wire."""
    return f"{value.isoformat()}Z" if value is not None else None
