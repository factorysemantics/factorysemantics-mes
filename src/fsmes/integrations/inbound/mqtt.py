"""The third inbound driver: a broker the plant already has.

The MES has published to MQTT since the unified namespace landed. This is
the other half — it listens. A plant that moves data has a broker (United
Manufacturing Hub, Node-RED, an IoT gateway of some make), and a first real
plant may well have sensors, counters and states that never touch an OPC UA
server at all. Everything on that broker is invisible to this MES until
something subscribes.

Two kinds of thing arrive, and they are kept apart on purpose:

**Tag values** — a counter, a state word, a process value published by a
gateway. These are *machine data*, held to exactly the discipline the OPC
agent holds its own to: a counter delta is computed from a monotonic total
and never taken on trust, an unmapped state word is refused rather than
guessed at, and a reading for a machine this MES does not hold is refused
rather than inventing the machine. The wiring lives beside the OPC wiring in
the tag map, because one document should describe one plant.

**Inbound events** — a downtime label, a quality result, a count, in the
contract `fsmes.integrations.inbound.contract` already defines. A broker
that carries those carries them as JSON objects, and one of those objects is
the same row a CSV would have carried, so the mapping, the parsing, the
deduplication and the writers are the folder driver's, unchanged. The topic
is the only new thing.

Three rules that come from this transport rather than from the contract:

*A counter is a total, never an increment.* MQTT at QoS 1 is at-least-once:
the broker may deliver the same message twice after a reconnect, and there
is nothing in it to tell the second delivery from the first. A redelivered
*total* changes nothing, because a delta is the rise above the last value
this MES saw. A redelivered *increment* would book units the plant never
made. So an increment publisher is refused by name, and told the two things
it can do instead — publish a total, or publish counts as inbound events,
which carry the supplier's own key and deduplicate properly.

*A retained message sets a baseline and nothing else.* The broker replays a
retained message to every new subscriber as though it had just happened. Its
value may be an hour old, and there is nothing in the message that says how
old. Booking a state change from one would put an hour of the wrong state
into availability; recording it as history would put an hour-old reading on
this minute's chart. So a retained message is used for the one thing it is
honestly good for — the counter baseline it saves the first delta from
being wrong about — and is counted in the report.

*Nothing here publishes.* This module holds no publish call, and shadow mode
is respected without gating it: subscribing changes nothing in the plant,
and a shadow that stopped listening would be comparing itself with the
incumbent on half the evidence. `fsmes.shadow.REGISTER` carries the entry
and the reason.

Clean-room: nothing here is derived from any commercial system's topic
tree or payload format. Which topics a plant publishes, and what its
payloads look like, is that plant's configuration.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from sqlalchemy import select

from fsmes.domain import (
    EquipmentState,
    EquipmentStateName,
    ProductionSource,
    TagValue,
)
from fsmes.integrations.inbound.folder import MappingError, StreamMapping, to_event
from fsmes.kernel.tags import COUNTER_TAGS
from fsmes.services import MesError, equipment, execution, masterdata
from fsmes.services import inbound as inbound_service

log = structlog.get_logger("inbound.mqtt")

#: The two counters a booking can be taken from. `TotalCount` is in
#: `COUNTER_TAGS` too and is deliberately not here: it is good plus scrap,
#: and booking it as well would count every unit twice.
BOOKED_COUNTERS = ("GoodCount", "ScrapCount")


class SubscriptionError(MappingError):
    """The broker wiring cannot be used as written, and says why."""


# ------------------------------------------------------------- topic filters


def topic_matches(filter_: str, topic: str) -> bool:
    """MQTT topic-filter matching, `+` for one level and `#` for the rest.

    Implemented here rather than taken from the client library so that a
    test can drive this driver with plain strings and no broker, and so that
    the matching a plant reads about is the matching that runs.
    """
    if filter_ == topic:
        return True
    wanted = filter_.split("/")
    got = topic.split("/")
    for i, level in enumerate(wanted):
        if level == "#":
            # `#` matches the rest, including nothing — but never a topic
            # starting `$`, which brokers reserve for their own statistics.
            return i <= len(got) and not (i == 0 and got and got[0].startswith("$"))
        if i >= len(got):
            return False
        if level != "+" and level != got[i]:
            return False
    return len(got) == len(wanted)


def value_at(payload: bytes, json_path: str | None):
    """The value one message carries, at the configured path.

    No path means the payload *is* the value — the way most gateways publish
    a single tag. A path means the payload is a JSON object and the value is
    at that dotted path, so `value` and `payload.count` both work.
    """
    text = payload.decode("utf-8", errors="replace").strip()
    if json_path is None:
        return _number_or_text(text)
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"the payload is not JSON, and the mapping reads {json_path!r} out of it: {exc}") from exc
    node = document
    for part in json_path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"the payload has nothing at {json_path!r}")
        node = node[part]
    if isinstance(node, bool) or node is None:
        raise ValueError(f"the value at {json_path!r} is {node!r}, which is not a reading")
    return node if isinstance(node, (int, float)) else _number_or_text(str(node))


def _number_or_text(text: str):
    try:
        return float(text)
    except ValueError:
        return text


# ------------------------------------------------------------- the tag wiring


@dataclass(frozen=True)
class TagSubscription:
    """One topic filter, and what the messages on it are about.

    `equipment` names the machine outright. `equipment_from` takes it from a
    level of the topic instead, which is what a namespace laid out by machine
    needs — but the code that comes out of the topic still has to be one this
    MES holds, or the message is refused. A topic level is never turned into
    a machine.
    """

    topic: str
    tag: str
    equipment: str | None = None
    equipment_from: int | None = None
    json_path: str | None = None
    state_map: dict[str, EquipmentStateName] = field(default_factory=dict)

    def equipment_in(self, topic: str) -> str:
        if self.equipment:
            return self.equipment
        levels = topic.split("/")
        if self.equipment_from is None or self.equipment_from >= len(levels):
            raise ValueError(
                f"the mapping takes the machine from level {self.equipment_from} of the topic "
                f"and {topic!r} has {len(levels)}"
            )
        return levels[self.equipment_from]

    def to_state(self, value) -> EquipmentStateName:
        """A raw state word as an MES state, or a refusal naming the value.

        The OPC agent's rule, for the same reason: an unrecognised state code
        means the map is wrong, and quietly calling it idle would corrupt
        every availability figure downstream.
        """
        if not self.state_map:
            try:
                return EquipmentStateName(str(value).strip().lower())
            except ValueError:
                raise ValueError(
                    f"State={value!r} is not one of {[s.value for s in EquipmentStateName]} and the "
                    f"mapping for {self.topic!r} has no state_map to translate it"
                ) from None
        key = str(int(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)
        try:
            return self.state_map[key]
        except KeyError:
            raise ValueError(
                f"State={value!r} is not in the state_map for {self.topic!r} (known: {sorted(self.state_map)})"
            ) from None


def load_tag_subscriptions(path: Path) -> list[TagSubscription]:
    """The broker half of the tag map, or a sentence saying what is wrong.

    It lives in the tag map beside the OPC machines so that one document
    describes one plant. A tag map with no `mqtt` section simply has no
    broker tags, which is not an error: most plants start with one transport.
    """
    path = Path(path)
    if not path.is_file():
        raise SubscriptionError(
            f"no tag map at {path}. It is the file that says which topics carry which machine's "
            "tags; without it nothing can be subscribed to, and this MES will not guess topics."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SubscriptionError(f"{path} is not valid JSON: {exc}") from exc
    section = raw.get("mqtt") or {}
    if not isinstance(section, dict):
        raise SubscriptionError(f"{path}: 'mqtt' should be an object with a 'tags' list")
    entries = section.get("tags") or []
    if not isinstance(entries, list):
        raise SubscriptionError(f"{path}: 'mqtt.tags' should be a list of topic mappings")

    subscriptions: list[TagSubscription] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise SubscriptionError(f"{path}: every entry in 'mqtt.tags' should be an object")
        topic = str(entry.get("topic") or "").strip()
        tag = str(entry.get("tag") or "").strip()
        if not topic or not tag:
            raise SubscriptionError(f"{path}: every entry in 'mqtt.tags' needs a 'topic' and a 'tag'")
        if topic in seen:
            raise SubscriptionError(
                f"{path}: two entries in 'mqtt.tags' subscribe to {topic!r}. One topic carries one "
                "tag of one machine; a second entry for it would double-count every message."
            )
        seen.add(topic)
        named = entry.get("equipment")
        from_level = entry.get("equipment_from")
        if bool(named) == (from_level is not None):
            raise SubscriptionError(
                f"{path}: {topic!r} needs either 'equipment' (the machine code) or 'equipment_from' "
                "(which level of the topic carries it), and not both."
            )
        if from_level is not None and (not isinstance(from_level, int) or from_level < 0):
            raise SubscriptionError(f"{path}: {topic!r} has an 'equipment_from' that is not a level number")
        counter = str(entry.get("counter") or "total").lower()
        if tag in COUNTER_TAGS and counter != "total":
            raise SubscriptionError(
                f"{path}: {topic!r} publishes {tag} as {counter!r}. This MES only accepts a counter as a "
                "running total: MQTT delivers at least once, so a redelivered increment would book units "
                "the plant never made, and nothing in the message can tell the second delivery from the "
                "first. Publish the machine's own total, or send the counts as inbound `counts` events, "
                "which carry the supplying system's key and deduplicate on it."
            )
        try:
            state_map = {str(k): EquipmentStateName(v) for k, v in (entry.get("state_map") or {}).items()}
        except ValueError as exc:
            raise SubscriptionError(f"{path}: {topic!r} has a state_map naming a state this MES has no: {exc}") from exc
        if state_map and tag != "State":
            raise SubscriptionError(f"{path}: {topic!r} maps a state_map onto {tag!r}, which is not the State tag")
        subscriptions.append(TagSubscription(
            topic=topic, tag=tag, equipment=str(named) if named else None,
            equipment_from=from_level,
            json_path=str(entry["json_path"]) if entry.get("json_path") else None,
            state_map=state_map,
        ))
    return subscriptions


def load_event_subscriptions(mappings: dict[str, StreamMapping]) -> dict[str, StreamMapping]:
    """The streams whose mapping names a topic, keyed by that topic.

    A stream with no `topic` is a folder stream and nothing more; a plant may
    take downtime labels off the broker and quality results out of a folder,
    and the mapping file says which is which.
    """
    by_topic: dict[str, StreamMapping] = {}
    for mapping in mappings.values():
        if not mapping.topic:
            continue
        if mapping.topic in by_topic:
            raise SubscriptionError(
                f"two streams subscribe to {mapping.topic!r} ({by_topic[mapping.topic].name} and "
                f"{mapping.name}); one topic carries one kind of event"
            )
        by_topic[mapping.topic] = mapping
    return by_topic


# ---------------------------------------------------------------- the report


@dataclass
class Report:
    """What a stretch of listening did. Totals over everything received."""

    #: Every message the broker delivered, matched or not.
    messages: int = 0
    #: Messages no configured topic filter matched. Not an error: a plant's
    #: broker carries far more than this MES was pointed at.
    unmatched: int = 0
    #: Retained messages, used as a baseline and otherwise not acted on.
    retained: int = 0
    readings: int = 0
    bookings: int = 0
    state_changes: int = 0
    #: State messages that repeated the state already open. Counted rather
    #: than hidden: a gateway publishing its state word every second is the
    #: normal case, and a plant should be able to see that is what it is.
    states_unchanged: int = 0
    events: int = 0
    duplicates: int = 0
    #: reason -> how many messages were refused for it. Grouped because a
    #: broker repeats a misconfiguration thousands of times a shift, and a
    #: log line each says less than one line with a count on it.
    refusals: dict[str, int] = field(default_factory=dict)

    @property
    def refused(self) -> int:
        return sum(self.refusals.values())

    def refuse(self, reason: str) -> None:
        self.refusals[reason] = self.refusals.get(reason, 0) + 1

    def render(self) -> list[str]:
        lines = [
            f"{self.messages} message(s) received: {self.readings} reading(s) recorded, "
            f"{self.bookings} booking(s), {self.state_changes} state change(s) "
            f"({self.states_unchanged} repeated the open state), {self.events} event(s) recorded, "
            f"{self.duplicates} already seen, {self.refused} refused, "
            f"{self.unmatched} on topics nothing is mapped to, "
            f"{self.retained} retained (baseline only)."
        ]
        for reason, count in sorted(self.refusals.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"  {count} x {reason}")
        return lines


# --------------------------------------------------------------- the listener


class Ingest:
    """Turns broker messages into MES facts, one message at a time.

    Holds the only state this driver has: the last value seen for each
    counter, which is what makes a delta a delta. Nothing is buffered — a
    message is written before the next is read — so a crash loses at most
    the message in hand, and the broker redelivers it at QoS 1.
    """

    def __init__(self, tags: list[TagSubscription], events: dict[str, StreamMapping], source: str) -> None:
        self.tags = tags
        self.events = events
        #: The name this plant calls the broker, used as the actor on
        #: everything written from it and as the source on production.
        self.source = source
        self.last_counts: dict[tuple[str, str], float] = {}
        self.report = Report()

    @property
    def topics(self) -> list[str]:
        """Every filter this subscribes to. Tags first, then events."""
        return [t.topic for t in self.tags] + sorted(self.events)

    def handle(self, session_scope, topic: str, payload: bytes, retained: bool = False) -> None:
        """One message, written or refused. Never raises to the caller.

        A single bad message must not stall the interface: the plant keeps
        publishing whatever happens here, and a driver that fell over on a
        malformed payload would lose every good message behind it.
        """
        self.report.messages += 1
        subscription = next((t for t in self.tags if topic_matches(t.topic, topic)), None)
        if subscription is not None:
            self._tag(session_scope, subscription, topic, payload, retained)
            return
        mapping = next((m for f, m in self.events.items() if topic_matches(f, topic)), None)
        if mapping is not None:
            self._event(session_scope, mapping, topic, payload, retained)
            return
        self.report.unmatched += 1

    # ------------------------------------------------------------ tag values

    def _tag(self, session_scope, sub: TagSubscription, topic: str, payload: bytes, retained: bool) -> None:
        try:
            code = sub.equipment_in(topic)
            value = value_at(payload, sub.json_path)
        except ValueError as exc:
            self.report.refuse(f"{topic}: {exc}")
            return

        if retained:
            self.report.retained += 1
            # The one thing a replayed message is honestly good for: the
            # counter this MES would otherwise compute its first delta from.
            if sub.tag in COUNTER_TAGS and isinstance(value, (int, float)):
                self.last_counts[(code, sub.tag)] = float(value)
            return

        try:
            with session_scope() as session:
                machine = masterdata.get_equipment(session, code)
                # History first, and for every message: tag history is the
                # evidence, and a reading whose state word this MES could not
                # translate is exactly the reading somebody needs to see.
                self._record(session, machine.id, machine.code, sub.tag, value)
                self.report.readings += 1
                if sub.tag == "State":
                    self._set_state(session, machine, sub, value)
                elif sub.tag in BOOKED_COUNTERS:
                    self._book(session, machine.code, sub.tag, value)
        except (MesError, ValueError) as exc:
            self.report.refuse(f"{topic}: {exc}")
        except Exception as exc:  # a broken message must not stall the interface
            log.error("inbound mqtt message failed", topic=topic, error=str(exc))
            self.report.refuse(f"{topic}: {type(exc).__name__}: {exc}")

    def _set_state(self, session, machine, sub: TagSubscription, value) -> None:
        """Move the machine to the state its gateway published.

        A gateway usually publishes its state word on a timer rather than on
        a change, so most of these messages say what this MES already
        believes. `equipment.set_state` is a no-op then, and the difference is
        counted rather than reported as a change that never happened.
        """
        state = sub.to_state(value)
        open_before = session.scalar(
            select(EquipmentState).where(EquipmentState.equipment_id == machine.id,
                                         EquipmentState.ended_at.is_(None)))
        interval = equipment.set_state(session, equipment_code=machine.code, state=state,
                                       actor=self.source)
        if open_before is not None and open_before.id == interval.id:
            self.report.states_unchanged += 1
        else:
            self.report.state_changes += 1

    def _record(self, session, equipment_id: int, code: str, tag: str, value) -> None:
        """Tag history, under the machine's own tag name — the same shape the
        OPC agent writes, so a plant reading one transport's history reads
        the other's the same way."""
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        session.add(TagValue(
            equipment_id=equipment_id,
            tag=f"{code}.{tag}",
            value_num=float(value) if numeric else None,
            value_text=None if numeric else str(value),
        ))

    def _book(self, session, code: str, tag: str, value) -> None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{tag}={value!r} is not a number, so no delta can be taken from it")
        delta = self._counter_delta(code, tag, float(value))
        if not delta:
            return
        quantities = {"good" if tag == "GoodCount" else "scrap": delta}
        op = execution.report(session, equipment_code=code, source=ProductionSource.EXTERNAL,
                              source_system=self.source[:80], actor=self.source, **quantities)
        self.report.bookings += 1
        if op is None:
            log.info("machine counted with no active order; recorded as unassigned production",
                     equipment=code, **quantities)

    def _counter_delta(self, code: str, tag: str, value: float) -> float:
        """The rise of a monotonic counter, and the OPC agent's rule for it.

        Never invent production. The first value seen is a baseline and books
        nothing — which is also what makes a redelivered message harmless. A
        value that has fallen to near zero is a gateway or PLC reset and
        becomes the new baseline, because the units around the reset are
        unknowable and none of them are guessed at.
        """
        key = (code, tag)
        last = self.last_counts.get(key)
        if last is None or value < last / 2:
            self.last_counts[key] = value
            return 0
        if value <= last:
            return 0
        self.last_counts[key] = value
        return value - last

    # --------------------------------------------------------------- events

    def _event(self, session_scope, mapping: StreamMapping, topic: str, payload: bytes, retained: bool) -> None:
        if retained:
            # An event carries its own recording time and its own key, so a
            # retained one would be recorded correctly — and then recorded
            # again by every restart until somebody clears the topic. The
            # ledger would refuse the repeats, but a retained *event* is a
            # sign the publisher is using the broker as a database, and this
            # says so once rather than pretending it read something new.
            self.report.retained += 1
            self.report.refuse(f"{topic}: a retained message on an event topic is not read as an event")
            return
        try:
            document = json.loads(payload.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            self.report.refuse(f"{topic}: the payload is not JSON: {exc}")
            return
        if not isinstance(document, dict):
            self.report.refuse(f"{topic}: an event payload should be one JSON object")
            return
        try:
            with session_scope() as session:
                event = to_event(document, mapping.as_payload_mapping(), "the payload")
                outcome = inbound_service.WRITERS[mapping.name](session, event)
        except (ValueError, inbound_service.Refused) as exc:
            self.report.refuse(f"{topic}: {exc}")
            return
        except Exception as exc:
            log.error("inbound mqtt event failed", topic=topic, error=str(exc))
            self.report.refuse(f"{topic}: {type(exc).__name__}: {exc}")
            return
        if outcome.duplicate:
            self.report.duplicates += 1
        else:
            self.report.events += 1


# -------------------------------------------------------------- the transport


class MqttSource:
    """A real broker over aiomqtt, subscribed and never published to.

    The client lives in the `[mqtt]` extra, the same one the publisher uses,
    because a plant PC that does not speak MQTT should not carry an MQTT
    library. Reconnection is the caller's loop, not this class's: a broker
    that is down for an hour costs an hour of listening and no state of its
    own here.
    """

    def __init__(self, address, client_id: str = "fsmes-inbound", qos: int = 1) -> None:
        self.address = address
        self.client_id = client_id
        self.qos = qos

    async def messages(self, topics: list[str]):
        """Yield `(topic, payload, retained)` for as long as the broker holds.

        A clean session on purpose. A persistent one would have the broker
        queue this MES's messages while it is away and deliver the backlog on
        reconnect, every message of it stamped with no time at all — an
        afternoon of counters and states arriving as though they were now.
        The events that must survive a disconnection are the inbound ones,
        and they carry the supplier's own recording time.
        """
        from fsmes.integrations.uns.transport import import_aiomqtt

        aiomqtt = import_aiomqtt()
        kwargs: dict = {"hostname": self.address.host, "port": self.address.port,
                        "identifier": self.client_id, "clean_session": True}
        if self.address.username:
            kwargs["username"] = self.address.username
            kwargs["password"] = self.address.password
        if self.address.tls:
            import ssl

            kwargs["tls_context"] = ssl.create_default_context()
        async with aiomqtt.Client(**kwargs) as client:
            for topic in topics:
                await client.subscribe(topic, qos=self.qos)
            log.info("listening", broker=repr(self.address), topics=len(topics), qos=self.qos)
            async for message in client.messages:
                yield str(message.topic), bytes(message.payload or b""), bool(message.retain)


RECONNECT_SECONDS = 5.0


async def run(source, ingest: Ingest, session_scope, on_report=None,
              report_seconds: float = 60.0, reconnect_seconds: float = RECONNECT_SECONDS) -> None:
    """Listen forever, writing every message and saying so periodically.

    A broker that goes away is reconnected to, without end and without
    losing what has already been written: this MES's own database is the
    record, and the counter baselines are rebuilt from the first message
    after the reconnection exactly as they were at start-up.
    """
    import time

    while True:
        said_at = time.monotonic()
        try:
            async for topic, payload, retained in source.messages(ingest.topics):
                ingest.handle(session_scope, topic, payload, retained)
                if on_report and time.monotonic() - said_at >= report_seconds:
                    on_report(ingest.report)
                    said_at = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("broker connection lost; reconnecting", error=str(exc),
                        wait_s=reconnect_seconds)
        if on_report:
            on_report(ingest.report)
        await asyncio.sleep(reconnect_seconds)


def streams_and_topics(tags: list[TagSubscription], events: dict[str, StreamMapping]) -> list[str]:
    """What `fsmes inbound check` prints: every filter and what it carries."""
    lines = [f"{sub.topic}  ->  {sub.equipment or f'level {sub.equipment_from} of the topic'}.{sub.tag}"
             + (f" (at {sub.json_path!r} in the payload)" if sub.json_path else "")
             for sub in tags]
    lines += [f"{topic}  ->  {mapping.name} events from {mapping.source!r} ({mapping.source_kind})"
              for topic, mapping in sorted(events.items())]
    return lines
