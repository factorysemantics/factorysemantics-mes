"""The unified-namespace publisher: where an event lands, what it carries,
and what happens when the broker will not take it.

No broker is started here. The transport is three methods, so a fake one is
the honest test double — the same choice `test_erpnext` makes about Frappe.
The one test that needs a real broker is `test_a_running_broker_receives_
what_the_publisher_sent`, marked slow and skipped unless somebody points
MES_UNS_TEST_BROKER at one.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import contextmanager

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from fsmes.config import Settings
from fsmes.domain import (
    Equipment,
    EquipmentLevel,
    EquipmentStateName,
    ErpMessage,
    MessageDirection,
    MessageStatus,
    ProductionSource,
    UnsPublication,
)
from fsmes.integrations.uns import publisher
from fsmes.integrations.uns.envelope import envelope
from fsmes.integrations.uns.topics import equipment_topics, topic_for
from fsmes.integrations.uns.transport import (
    BrokerAddress,
    LogTransport,
    MqttTransport,
    make_transport,
)
from fsmes.services import equipment, erp, execution, uns, workorders

# ------------------------------------------------------------------ doubles

class FakeBroker:
    """A broker that records what it was told, and refuses when told to."""

    def __init__(self, refuse: int = 0) -> None:
        self.received: list[tuple[str, dict, int, bool]] = []
        self.refuse = refuse          # fail this many publishes, then accept
        self.connects = 0
        self.closed = 0

    async def connect(self) -> None:
        self.connects += 1

    async def publish(self, topic, payload, *, qos, retain) -> None:
        if self.refuse:
            self.refuse -= 1
            raise ConnectionError("broker refused the publish")
        self.received.append((topic, json.loads(payload), qos, retain))

    async def close(self) -> None:
        self.closed += 1

    def topics(self) -> list[str]:
        return [topic for topic, _payload, _qos, _retain in self.received]


def settings_for(**overrides) -> Settings:
    """Settings a test controls, never the process's environment."""
    base = {"uns_mode": "log", "uns_topic_prefix": "umh/v1", "uns_enterprise": "",
            "uns_site": "", "uns_schema": "_mes", "uns_qos": 1, "uns_retain": False,
            "plant_name": ""}
    return Settings(**{**base, **overrides})


def queue_confirmation(session, order_code: str = "WO-UNS-1", equipment: str | None = "MIX01"):
    """One outbound event in the outbox, without driving a whole order
    through the plant."""
    message = ErpMessage(
        direction=MessageDirection.OUT, kind="operation_confirmation",
        message_key=f"{order_code}:op10",
        payload={"kind": "operation_confirmation", "order": order_code, "seq": 10,
                 "equipment": equipment, "good_qty": 12.0, "scrap_qty": 1.0})
    session.add(message)
    session.flush()
    return message


def run_cycle(transport, scope, settings) -> dict:
    return asyncio.run(publisher.cycle(transport, scope, settings))


# ------------------------------------------------------------------- topics

def test_the_topic_follows_the_equipment_tree_from_the_enterprise_down_to_the_machine(session):
    """The seeded plant is ACME → KC1 → PKG → LINE1 → MIX01, and every rung
    of it appears, in order, under the configured prefix."""
    topic = topic_for(session, settings_for(), kind="operation_confirmation",
                      equipment_code="MIX01")
    assert topic == "umh/v1/ACME/KC1/PKG/LINE1/MIX01/_mes/operation_confirmation"


def test_a_tree_deeper_than_line_and_machine_publishes_every_rung_it_has(session):
    """A cell between the line and the machine is part of where the event
    happened; a publisher that assumed two rungs would lose it."""
    line = session.scalar(
        select(Equipment).where(Equipment.code == "LINE1"))
    cell = Equipment(code="CELL-A", name="Cell A", level=EquipmentLevel.WORK_CENTER, parent=line)
    machine = Equipment(code="CAP01", name="Capper 01", level=EquipmentLevel.WORK_UNIT, parent=cell)
    session.add_all([cell, machine])
    session.flush()

    topic = topic_for(session, settings_for(), kind="operation_confirmation",
                      equipment_code="CAP01")
    assert topic == "umh/v1/ACME/KC1/PKG/LINE1/CELL-A/CAP01/_mes/operation_confirmation"


def test_an_event_that_names_no_machine_publishes_at_the_site_and_not_under_an_invented_one(session):
    """An order completion is an order-level fact. Hanging it under some
    machine would tell the namespace a machine finished the order."""
    topic = topic_for(session, settings_for(), kind="order_completion", equipment_code=None)
    assert topic == "umh/v1/ACME/KC1/_mes/order_completion"


def test_a_level_the_plant_has_not_modelled_is_published_as_unknown_rather_than_guessed(session):
    """Two sites in one database means the tree cannot say which site an
    order-level event belongs to. `unknown` is the honest segment; picking
    the first one alphabetically would be an invented answer."""
    enterprise = session.scalar(
        select(Equipment).where(Equipment.code == "ACME"))
    session.add(Equipment(code="KC2", name="Second Plant", level=EquipmentLevel.SITE,
                          parent=enterprise))
    session.flush()

    topic = topic_for(session, settings_for(), kind="order_completion")
    assert topic == "umh/v1/ACME/unknown/_mes/order_completion"


def test_the_settings_decide_the_enterprise_and_site_whatever_the_tree_calls_them(session):
    """The same database is deployed under different namespace names in test
    and in production; that is a plant-boundary setting, not a code change."""
    topic = topic_for(session, settings_for(uns_enterprise="acme", uns_site="kansas-city"),
                      kind="operation_confirmation", equipment_code="MIX01")
    assert topic.startswith("umh/v1/acme/kansas-city/PKG/LINE1/MIX01/")


def test_a_topic_segment_never_carries_a_character_mqtt_reserves(session):
    """A machine code with a slash, a plus or a hash in it would silently
    split the topic or turn it into a wildcard."""
    line = session.scalar(
        select(Equipment).where(Equipment.code == "LINE1"))
    session.add(Equipment(code="FILL/01 +#", name="Filler", level=EquipmentLevel.WORK_UNIT,
                          parent=line))
    session.flush()

    topic = topic_for(session, settings_for(), kind="operation_confirmation",
                      equipment_code="FILL/01 +#")
    assert topic.endswith("/LINE1/FILL_01___/_mes/operation_confirmation")
    assert "+" not in topic and "#" not in topic


def test_a_machine_the_master_data_does_not_hold_is_still_published(session):
    """A confirmation naming a machine nobody has modelled yet is a real
    event. It publishes under its own code with no ancestors, rather than
    being dropped or hung under a hierarchy that was made up for it."""
    topic = topic_for(session, settings_for(), kind="operation_confirmation",
                      equipment_code="NOT-IN-MASTER-DATA")
    assert topic == "umh/v1/ACME/KC1/NOT-IN-MASTER-DATA/_mes/operation_confirmation"


def test_the_topic_listing_covers_every_work_unit_the_plant_holds(session):
    """`fsmes uns topics` is a list, so it has to be a complete one."""
    units = session.scalars(
        select(Equipment).where(Equipment.level == EquipmentLevel.WORK_UNIT)).all()
    topics = equipment_topics(session, settings_for())
    assert len(topics) == len(units)
    assert all(topic.startswith("umh/v1/ACME/KC1/") for topic in topics)
    # The prefix a kind is appended to, not a topic with a placeholder in it.
    assert all(topic.endswith("/_mes") for topic in topics)


# ----------------------------------------------------------------- envelope

def test_the_published_event_carries_the_event_id_a_consumer_dedupes_on(session):
    """At-least-once means a consumer will see an event twice. It needs the
    MES's own id to know that is what happened."""
    message = queue_confirmation(session)
    body = envelope(message, settings_for(plant_name="kc1"), published_at=message.created_at)

    assert body["event_id"] == message.id
    assert body["message_key"] == "WO-UNS-1:op10"
    assert body["plant"] == "kc1"
    assert body["kind"] == "operation_confirmation"


def test_the_event_says_when_the_mes_recorded_it_not_when_the_broker_heard_it(session):
    """A backlog delivered after an outage must not read as a burst of
    production that happened at reconnect time."""
    from datetime import timedelta

    message = queue_confirmation(session)
    later = message.created_at + timedelta(hours=2)
    body = envelope(message, settings_for(), published_at=later)

    assert body["recorded_at"].startswith(message.created_at.isoformat())
    assert body["published_at"].startswith(later.isoformat())
    assert body["recorded_at"] != body["published_at"]


def test_the_outbox_payload_is_published_as_the_mes_recorded_it(session):
    """The publisher relays; it does not reshape. A consumer that reads the
    ERP contract reads this."""
    message = queue_confirmation(session)
    body = envelope(message, settings_for(), published_at=message.created_at)
    assert body["payload"] == message.payload


# ------------------------------------------------------------------- cycles

def test_every_outbox_message_is_published_once_however_often_the_cycle_runs(session, scope):
    """Enrolment runs every cycle; enrolling the same message twice would
    double every number in the namespace."""
    queue_confirmation(session, "WO-UNS-1")
    queue_confirmation(session, "WO-UNS-2")
    broker = FakeBroker()

    first = run_cycle(broker, scope, settings_for())
    second = run_cycle(broker, scope, settings_for())

    assert first == {"enrolled": 2, "published": 2, "failed": 0, "due": 2}
    assert second == {"enrolled": 0, "published": 0, "failed": 0, "due": 0}
    assert len(broker.received) == 2


def test_publishing_to_the_namespace_does_not_consume_the_erp_outbox(session, scope):
    """Two consumers, one stream. The ERP's own delivery marks live on the
    message; if the publisher touched them the ERP would silently stop
    receiving confirmations."""
    queue_confirmation(session)
    run_cycle(FakeBroker(), scope, settings_for())

    still_owed = erp.pending_outbound(session)
    assert len(still_owed) == 1
    assert still_owed[0].status is MessageStatus.PENDING


def test_only_what_the_plant_sent_outward_reaches_the_namespace(session, scope):
    """An inbound message is the ERP's own order request. It reaches the
    namespace as the confirmations produced against it, not as an echo."""
    session.add(ErpMessage(direction=MessageDirection.IN, kind="production_schedule",
                           payload={"code": "WO-IN-1", "material": "FG-1", "quantity": 5}))
    queue_confirmation(session)
    session.flush()
    broker = FakeBroker()

    counted = run_cycle(broker, scope, settings_for())

    assert counted["enrolled"] == 1
    assert [payload["kind"] for _t, payload, _q, _r in broker.received] == ["operation_confirmation"]


def test_a_kind_the_publisher_has_never_seen_is_published_rather_than_dropped(session, scope):
    """The outbox is the MES's event stream and will grow new kinds. An
    unknown kind gets its own topic; silently dropping it would make the
    namespace quietly incomplete."""
    session.add(ErpMessage(direction=MessageDirection.OUT, kind="hold_placed",
                           message_key="WO-UNS-9:hold",
                           payload={"kind": "hold_placed", "order": "WO-UNS-9",
                                    "equipment": "PACK01", "reason": "quality"}))
    session.flush()
    broker = FakeBroker()

    run_cycle(broker, scope, settings_for())

    assert broker.topics() == ["umh/v1/ACME/KC1/PKG/LINE1/PACK01/_mes/hold_placed"]


def test_a_machine_changing_state_reaches_the_namespace_on_its_own_topic(session, scope):
    """The reason the outbox became an event log. A state change is recorded
    by the MES, published under the machine it happened on, and carries what
    the machine left as well as what it entered."""
    equipment.set_state(session, equipment_code="MIX01",
                        state=EquipmentStateName.RUNNING, actor="opc")
    equipment.set_state(session, equipment_code="MIX01",
                        state=EquipmentStateName.DOWN, reason="jam", actor="opc")
    session.flush()
    broker = FakeBroker()

    run_cycle(broker, scope, settings_for())

    topics = broker.topics()
    assert topics == ["umh/v1/ACME/KC1/PKG/LINE1/MIX01/_mes/equipment_state_change"] * 2
    _topic, body, _qos, _retain = broker.received[-1]
    assert body["kind"] == "equipment_state_change"
    assert body["payload"]["state"] == "down" and body["payload"]["reason"] == "jam"
    assert body["payload"]["previous_state"] == "running"
    assert body["payload"]["work_center"] == "LINE1"


def test_the_first_state_of_a_machine_publishes_an_unknown_previous_run_not_a_zero_one(session, scope):
    """What reaches the broker keeps the MES's own answer: null, because
    nothing was observed before it, not zero seconds of running."""
    equipment.set_state(session, equipment_code="MIX01",
                        state=EquipmentStateName.RUNNING, actor="opc")
    session.flush()
    broker = FakeBroker()

    run_cycle(broker, scope, settings_for())

    _topic, body, _qos, _retain = broker.received[0]
    assert body["payload"]["previous_state"] is None
    assert body["payload"]["previous_seconds"] is None


def test_an_order_going_on_hold_publishes_at_the_site_with_its_reason(session, scope):
    """A hold is an order-level fact: no machine, so it hangs at the site,
    the same rule an order completion follows."""
    workorders.create(session, code="WO-UNS-H", material_code="FG-COLA", quantity=4, actor="test")
    workorders.release(session, "WO-UNS-H", actor="test")
    execution.report(session, equipment_code="MIX01", good=1, source=ProductionSource.OPC)
    workorders.hold(session, "WO-UNS-H", "awaiting a quality decision", actor="SUP")
    session.flush()
    broker = FakeBroker()

    run_cycle(broker, scope, settings_for())

    held = [(topic, body) for topic, body, _q, _r in broker.received
            if body["kind"] == "order_hold"]
    assert len(held) == 1
    topic, body = held[0]
    assert topic == "umh/v1/ACME/KC1/_mes/order_hold"
    assert body["payload"]["reason"] == "awaiting a quality decision"
    assert body["payload"]["previous_status"] == "running"


def test_the_qos_and_retain_settings_reach_the_broker(session, scope):
    """At-least-once, and not retained: a retained event is replayed to
    every new subscriber as though it had just happened."""
    queue_confirmation(session)
    broker = FakeBroker()

    run_cycle(broker, scope, settings_for())

    _topic, _payload, qos, retain = broker.received[0]
    assert (qos, retain) == (1, False)


# --------------------------------------------------------- failure and retry

def test_a_broker_that_refuses_leaves_the_event_pending_and_backs_off(session, scope):
    """The event is not lost and it is not retried in a tight loop."""
    queue_confirmation(session)
    broker = FakeBroker(refuse=1)

    counted = run_cycle(broker, scope, settings_for())

    publication = session.scalars(select(UnsPublication)).one()
    assert counted["failed"] == 1
    assert publication.status is MessageStatus.PENDING
    assert publication.attempts == 1
    assert publication.next_attempt_at is not None
    assert "refused" in publication.error


def test_an_event_still_backing_off_is_not_retried_before_its_time(session, scope):
    """Otherwise the backoff is decoration and the broker gets hammered."""
    queue_confirmation(session)
    broker = FakeBroker(refuse=1)
    run_cycle(broker, scope, settings_for())

    counted = run_cycle(broker, scope, settings_for())

    assert counted["due"] == 0
    assert broker.received == []


def test_an_event_the_broker_never_accepts_is_declared_dead_and_kept(session, scope):
    """Not deleted: an event the plant's namespace never received is a fact
    somebody has to decide about."""
    queue_confirmation(session)
    publication = uns.enrol(session)[0]
    for _ in range(uns.MAX_ATTEMPTS):
        uns.mark_error(publication, ConnectionError("broker refused the publish"))

    assert publication.status is MessageStatus.DEAD
    assert publication.next_attempt_at is None
    assert uns.due(session) == []
    assert session.get(UnsPublication, publication.id) is not None


def test_a_dead_event_can_be_put_back_in_the_queue_by_hand(session, scope):
    """The broker came back; a person decides the backlog still matters."""
    queue_confirmation(session)
    publication = uns.enrol(session)[0]
    for _ in range(uns.MAX_ATTEMPTS):
        uns.mark_error(publication, ConnectionError("no"))

    uns.retry(session, publication.id, actor="TESTER")
    broker = FakeBroker()
    counted = run_cycle(broker, scope, settings_for())

    assert counted["published"] == 1


def test_the_backoff_grows_and_stops_growing(session):
    """5 s, 10 s, 20 s ... and never more than an hour, so a broker that has
    been away all weekend is still tried every hour."""
    assert [uns.backoff_seconds(n) for n in (1, 2, 3)] == [5, 10, 20]
    assert uns.backoff_seconds(99) == uns.MAX_BACKOFF_SECONDS


def test_the_queue_summary_states_how_much_it_is_not_showing(session, scope):
    """Thirty recent rows out of an unstated number is how a backlog hides."""
    for n in range(3):
        queue_confirmation(session, f"WO-UNS-{n}")
    run_cycle(FakeBroker(), scope, settings_for())

    summary = uns.queue_summary(session, limit=2)

    assert summary["enrolled"] == 3
    assert summary["outbox_outbound"] == 3
    assert summary["not_yet_enrolled"] == 0
    assert summary["showing"] == 2
    assert summary["counts"]["sent"] == 3


# ------------------------------------------------------------------ settings

def test_the_broker_url_decides_the_host_the_port_and_whether_tls_is_used():
    """A wrong broker URL should fail when the worker starts, not on the
    first event of the shift."""
    plain = BrokerAddress("mqtt://broker.plant.example")
    secure = BrokerAddress("mqtts://broker.plant.example:8884")

    assert (plain.host, plain.port, plain.tls) == ("broker.plant.example", 1883, False)
    assert (secure.host, secure.port, secure.tls) == ("broker.plant.example", 8884, True)


def test_a_password_in_the_settings_beats_one_written_into_the_url():
    """Credentials belong in an environment variable, not in a URL that ends
    up in a log line."""
    address = BrokerAddress("mqtt://url-user:url-secret@broker.example", "env-user", "env-secret")
    assert (address.username, address.password) == ("env-user", "env-secret")
    assert "secret" not in repr(address)


def test_an_unknown_broker_scheme_is_refused_by_name():
    with pytest.raises(ValueError, match="unknown broker scheme"):
        BrokerAddress("amqp://broker.example")


def test_the_mode_setting_decides_the_transport_and_off_means_off():
    assert make_transport(settings_for(uns_mode="off")) is None
    assert isinstance(make_transport(settings_for(uns_mode="log")), LogTransport)
    assert isinstance(make_transport(settings_for(uns_mode="mqtt")), MqttTransport)
    with pytest.raises(ValueError, match="unknown UNS mode"):
        make_transport(settings_for(uns_mode="kafka"))


def test_mqtt_mode_without_the_extra_installed_says_which_extra_to_install(monkeypatch):
    """The core install carries no MQTT client on purpose; the error has to
    say so rather than reading as a bug."""
    monkeypatch.setitem(sys.modules, "aiomqtt", None)
    transport = MqttTransport(BrokerAddress("mqtt://broker.example"))

    with pytest.raises(RuntimeError, match=r"factorysemantics-mes\[mqtt\]"):
        asyncio.run(transport.connect())


def test_log_mode_publishes_the_whole_namespace_without_contacting_a_broker(session, scope):
    """How a plant sees its topics before anyone has stood up a broker."""
    queue_confirmation(session)
    transport = LogTransport()

    counted = run_cycle(transport, scope, settings_for(uns_mode="log"))

    assert counted["published"] == 1
    assert transport.sent[0][0].endswith("/MIX01/_mes/operation_confirmation")


# --------------------------------------------------- what a cycle costs the database

class Cost:
    """What one cycle cost the database: the statements it ran and the
    transactions it committed, counted rather than reasoned about."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.commits = 0

    def reading(self, fragment: str) -> int:
        return len([s for s in self.statements if fragment in s])

    @property
    def equipment_lookups(self) -> int:
        return self.reading("FROM equipment")


@contextmanager
def counting(engine):
    """Count every statement and every commit on this engine."""
    cost = Cost()

    def statement(_conn, _cursor, statement, _params, _context, _executemany):
        cost.statements.append(" ".join(statement.split()))

    def commit(_conn):
        cost.commits += 1

    event.listen(engine, "before_cursor_execute", statement)
    event.listen(engine, "commit", commit)
    try:
        yield cost
    finally:
        event.remove(engine, "before_cursor_execute", statement)
        event.remove(engine, "commit", commit)


def committing_scope(engine):
    """`session_scope` over the test engine: a real session and a real commit
    per unit of work. The shared `scope` fixture reuses one session and never
    commits, which is exactly what a test counting transactions must not do."""

    @contextmanager
    def _scope():
        session = Session(engine, expire_on_commit=False)
        try:
            yield session
            session.commit()
        finally:
            session.close()

    return _scope


def queue_many(session, count: int, start: int = 0,
               machines: tuple[str, ...] = ("MIX01", "PACK01")) -> None:
    """A backlog: `count` confirmations spread over a plant's few machines."""
    for n in range(start, start + count):
        queue_confirmation(session, f"WO-UNS-BULK-{n}", equipment=machines[n % len(machines)])
    session.commit()


def test_a_cycle_looks_a_machine_up_once_however_many_events_it_carries(session, engine):
    """A topic depends on the kind and the machine and nothing else, and a
    plant has a handful of machines. Resolved event by event, a full batch of
    200 read the same few Equipment rows a thousand times."""
    queue_many(session, 2)
    with counting(engine) as small:
        first = run_cycle(FakeBroker(), committing_scope(engine), settings_for())

    queue_many(session, 40, start=100)
    with counting(engine) as large:
        second = run_cycle(FakeBroker(), committing_scope(engine), settings_for())

    assert (first["published"], second["published"]) == (2, 40)
    assert large.equipment_lookups == small.equipment_lookups
    assert small.equipment_lookups <= 6, "two machines, and their ancestors, once each"


def test_a_cycle_writes_every_result_in_one_transaction(session, engine):
    """One session, one row read by primary key and one commit per published
    event meant 200 transactions and 400 statements for a full batch — all of
    them after the broker had already taken the events."""
    queue_many(session, 2)
    with counting(engine) as small:
        run_cycle(FakeBroker(), committing_scope(engine), settings_for())

    queue_many(session, 40, start=100)
    with counting(engine) as large:
        run_cycle(FakeBroker(), committing_scope(engine), settings_for())

    # Enrol, read what is due, write what happened. Three, whatever the batch.
    assert small.commits == large.commits == 3


def test_a_transaction_is_still_never_open_while_the_broker_is_being_published_to(session, engine):
    """The one rule the batched write must not break. A transaction open
    across a publish holds a lock the plant floor is waiting on for as long
    as the broker takes to answer — which, when a broker has gone away, is
    the timeout."""
    queue_many(session, 4)
    live: list[Session] = []
    open_while_publishing: list[list[Session]] = []

    @contextmanager
    def watched_scope():
        unit = Session(engine, expire_on_commit=False)
        live.append(unit)
        try:
            yield unit
            unit.commit()
        finally:
            live.remove(unit)
            unit.close()

    class Watching(FakeBroker):
        async def publish(self, topic, payload, *, qos, retain):
            open_while_publishing.append([unit for unit in live if unit.in_transaction()])
            await super().publish(topic, payload, qos=qos, retain=retain)

    run_cycle(Watching(), lambda: watched_scope(), settings_for())

    assert len(open_while_publishing) == 4
    assert not any(open_while_publishing), \
        "a unit of work was open while the publisher was talking to the broker"


def test_enrolment_does_not_read_the_payloads_it_is_only_counting(session, engine):
    """It needs one id per message. Hydrating the whole row pulled every JSON
    payload in the backlog through the driver to find it."""
    queue_many(session, 20)

    with counting(engine) as cost, committing_scope(engine)() as fresh:
        assert len(uns.enrol(fresh)) == 20

    assert cost.reading("erp_messages.payload") == 0


def test_the_outbox_can_be_read_by_direction_without_scanning_every_message_ever_sent(session):
    """Both readers of the log ask for one direction, oldest first, and the
    table only grows. An index the migration adds is what keeps that a lookup."""
    indexes = {index.name for index in ErpMessage.__table__.indexes}
    assert "ix_erp_messages_direction_id" in indexes


def test_the_published_at_a_consumer_sees_is_the_one_the_database_recorded(session, scope):
    """The envelope stamped one instant and the publication row stamped
    another a moment later, so the MES's own record disagreed with what it
    had already told the plant."""
    queue_confirmation(session)
    broker = FakeBroker()

    run_cycle(broker, scope, settings_for())

    _topic, body, _qos, _retain = broker.received[0]
    publication = session.scalars(select(UnsPublication)).one()
    assert body["published_at"] == f"{publication.published_at.isoformat()}Z"


# ------------------------------------------------------------ keeping up with a plant

class SlowBroker(FakeBroker):
    """A broker that takes a moment to acknowledge, and remembers the most
    publishes it was holding at once."""

    def __init__(self, delay: float = 0.005) -> None:
        super().__init__()
        self.delay = delay
        self.holding = 0
        self.most_held = 0

    async def publish(self, topic, payload, *, qos, retain) -> None:
        self.holding += 1
        self.most_held = max(self.most_held, self.holding)
        try:
            await asyncio.sleep(self.delay)
            await super().publish(topic, payload, qos=qos, retain=retain)
        finally:
            self.holding -= 1


class Stop(Exception):
    """Ends the worker's loop from inside a test's stand-in for sleeping."""


class Clock:
    """`asyncio` with the sleeps written down, and the loop stopped the first
    time the worker really waits."""

    def __init__(self) -> None:
        self.slept: list[float] = []
        self.gather = asyncio.gather
        self.CancelledError = asyncio.CancelledError

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        if seconds:
            raise Stop


def test_a_backlog_drains_at_the_brokers_speed_and_not_at_the_poll_interval(session, scope,
                                                                            monkeypatch):
    """Sleeping after a full batch drained a backlog at uns_batch divided by
    uns_poll_seconds events a second however fast the broker was, so an hour
    of a busy line took most of an hour to catch up after an outage."""
    queue_many(session, 5)
    clock = Clock()
    monkeypatch.setattr(publisher, "session_scope", scope)
    monkeypatch.setattr(publisher, "asyncio", clock)
    broker = FakeBroker()

    with pytest.raises(Stop):
        asyncio.run(publisher.run(broker, settings_for(uns_batch=2, uns_poll_seconds=2.0)))

    # Two full batches straight after one another, then a batch that did not
    # fill, which means the queue is empty and waiting is the right thing.
    assert clock.slept == [0, 0, 2.0]
    assert len(broker.received) == 5


def test_a_batch_with_a_failure_in_it_waits_even_when_it_was_full(session):
    """A full batch means there is more behind it; a failure in it means the
    broker is unhappy, and going straight back is the opposite of a backoff."""
    settings = settings_for(uns_batch=200, uns_poll_seconds=2.0)

    assert publisher.draining({"enrolled": 0, "due": 200, "published": 200, "failed": 0}, settings)
    assert not publisher.draining({"enrolled": 0, "due": 200, "published": 199, "failed": 1},
                                  settings)
    assert not publisher.draining({"enrolled": 0, "due": 199, "published": 199, "failed": 0},
                                  settings)
    # A cycle that raised counted nothing, so there is nothing to say it is behind.
    assert not publisher.draining(None, settings)


def test_qos_one_events_go_out_in_groups_rather_than_one_round_trip_each(session, scope):
    """QoS 1 waits for the broker to acknowledge every event. Awaited one at a
    time, a namespace on the far side of a plant network moved at the speed of
    its latency rather than its bandwidth."""
    queue_many(session, 12)
    broker = SlowBroker()

    counted = run_cycle(broker, scope, settings_for(uns_inflight=4))

    assert counted["published"] == 12
    assert broker.most_held == 4


def test_a_plant_can_ask_for_strictly_one_publish_at_a_time(session, scope):
    """A broker that will hold only one unacknowledged message, or anyone who
    wants the old behaviour back, sets it to 1 and gets exactly that."""
    queue_many(session, 6)
    broker = SlowBroker()

    run_cycle(broker, scope, settings_for(uns_inflight=1))

    assert broker.most_held == 1
    assert len(broker.received) == 6


def test_events_still_reach_the_broker_oldest_first_when_they_go_out_in_groups(session, scope):
    """A namespace that delivers a shift out of order is worse than one that
    is an hour behind, and that has to survive the pipelining."""
    queue_many(session, 10)
    broker = SlowBroker()

    run_cycle(broker, scope, settings_for(uns_inflight=4))

    assert [body["event_id"] for _t, body, _q, _r in broker.received] == \
        sorted(body["event_id"] for _t, body, _q, _r in broker.received)


def test_one_refused_publish_does_not_take_the_group_around_it_with_it(session, scope):
    """Each event in a group is recorded on its own: the refused one backs
    off and the rest are published, exactly as when they went one at a time."""
    queue_many(session, 4)
    broker = FakeBroker(refuse=1)

    counted = run_cycle(broker, scope, settings_for(uns_inflight=4))

    assert (counted["published"], counted["failed"]) == (3, 1)
    statuses = sorted(p.status.value for p in session.scalars(select(UnsPublication)))
    assert statuses == ["pending", "sent", "sent", "sent"]


# -------------------------------------------------------- against a real broker

@pytest.mark.slow
def test_a_running_broker_receives_what_the_publisher_sent(session, scope):
    """The only test here that needs a broker. Point MES_UNS_TEST_BROKER at
    one (`mqtt://127.0.0.1:1883`) to run it; skipped otherwise, and skipped
    in CI, because this repository does not start brokers on the machine it
    is developed on."""
    url = os.environ.get("MES_UNS_TEST_BROKER")
    if not url:
        pytest.skip("set MES_UNS_TEST_BROKER to a reachable broker to run this")
    pytest.importorskip("aiomqtt", reason="the [mqtt] extra is not installed")

    from fsmes.integrations.uns.transport import MqttTransport as Real

    queue_confirmation(session)
    transport = Real(BrokerAddress(url), client_id="fsmes-test")

    async def _publish() -> dict:
        try:
            return await publisher.cycle(transport, scope, settings_for(uns_mode="mqtt"))
        finally:
            await transport.close()

    counted = asyncio.run(_publish())
    assert counted == {"enrolled": 1, "published": 1, "failed": 0, "due": 1}
