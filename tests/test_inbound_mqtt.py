"""The MQTT subscriber: what a broker's messages become, and what they do not.

No broker is started here. The source is three lines of fake — the same
choice `test_uns` makes about the publisher, and for the same reason: this
repository does not stand up brokers on the machine it is developed on.

What is pinned here is mostly what the driver *refuses*. A broker is the
easiest way yet to put numbers into an MES that nothing ever observed: it
redelivers, it replays retained messages as though they were new, and it
carries whatever a gateway felt like publishing. Every test below is one of
those, and the reading that would have been invented.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from fsmes import shadow
from fsmes.config import Settings, get_settings
from fsmes.domain import (
    AuditLog,
    EquipmentState,
    EquipmentStateName,
    InboundEvent,
    ProductionLog,
    ProductionSource,
    TagValue,
)
from fsmes.integrations.inbound import folder, mqtt
from fsmes.services import equipment, masterdata

# ------------------------------------------------------------------ fixtures

TAG_MAP = {
    "machines": [{"equipment": "MIX01", "object": "MIX01", "cycle_seconds": 4.0}],
    "mqtt": {
        "tags": [
            {"topic": "plant/line1/MIX01/good", "equipment": "MIX01", "tag": "GoodCount",
             "json_path": "value"},
            {"topic": "plant/line1/MIX01/scrap", "equipment": "MIX01", "tag": "ScrapCount",
             "json_path": "value"},
            {"topic": "plant/line1/MIX01/state", "equipment": "MIX01", "tag": "State",
             "json_path": "value", "state_map": {"1": "running", "3": "down"}},
            {"topic": "plant/line1/MIX01/temp", "equipment": "MIX01", "tag": "MotorTemp"},
        ]
    },
}

EVENT_MAPPING = {
    "counts": {
        "source": "gateway:line1",
        "source_kind": "terminal",
        "timezone": "UTC",
        "topic": "plant/line1/counts",
        "columns": {
            "external_key": "entry_id",
            "recorded_at": "entered_at",
            "equipment": "machine",
            "good": "good_qty",
            "scrap": "scrap_qty",
        },
    },
    "downtime": {
        "source": "replay:incumbent-mes",
        "source_kind": "replay",
        "timezone": "UTC",
        "columns": {
            "external_key": "stop_id",
            "recorded_at": "entered_at",
            "equipment": "machine",
            "started_at": "stop_start",
            "ended_at": "stop_end",
            "reason": "reason_code",
        },
    },
}


@pytest.fixture()
def tag_map(tmp_path) -> Path:
    path = tmp_path / "tag_map.json"
    path.write_text(json.dumps(TAG_MAP), encoding="utf-8")
    return path


@pytest.fixture()
def mappings(tmp_path):
    path = tmp_path / "inbound_mapping.json"
    path.write_text(json.dumps(EVENT_MAPPING), encoding="utf-8")
    return folder.load_mapping(path)


@pytest.fixture()
def ingest(tag_map, mappings) -> mqtt.Ingest:
    return mqtt.Ingest(
        mqtt.load_tag_subscriptions(tag_map),
        mqtt.load_event_subscriptions(mappings),
        source="gateway:line1",
    )


def send(ingest, scope, topic: str, payload, retained: bool = False) -> None:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    ingest.handle(scope, topic, raw, retained)


def booked(session) -> list[ProductionLog]:
    return list(session.scalars(select(ProductionLog).order_by(ProductionLog.id)))


class FakeBroker:
    """A broker that hands over a fixed list of messages and then holds.

    It holds rather than returning, because that is what a real connection
    does between messages, and a source that ended would have `run` treat a
    quiet shift as a dropped connection.
    """

    def __init__(self, queue, fail_first: bool = False) -> None:
        self.queue = queue
        self.subscribed: list[str] = []
        self.connections = 0
        self.fail_first = fail_first
        self.drained = asyncio.Event()

    async def messages(self, topics):
        self.connections += 1
        self.subscribed = list(topics)
        if self.fail_first and self.connections == 1:
            raise ConnectionError("the broker went away")
        for message in self.queue:
            yield message
        self.drained.set()
        await asyncio.Event().wait()


def listen(broker, ingest, scope) -> None:
    """Run the listener until the fake broker has handed everything over."""

    async def _go() -> None:
        task = asyncio.create_task(mqtt.run(broker, ingest, scope, reconnect_seconds=0))
        try:
            await asyncio.wait_for(broker.drained.wait(), timeout=5)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(_go())


# ------------------------------------------------------------ topic matching


@pytest.mark.parametrize(
    ("filter_", "topic", "matches"),
    [
        ("a/b/c", "a/b/c", True),
        ("a/b/c", "a/b/d", False),
        ("a/+/c", "a/b/c", True),
        ("a/+/c", "a/b/x/c", False),
        ("a/#", "a/b/c/d", True),
        ("a/#", "a", True),
        ("#", "a/b", True),
        ("#", "$SYS/broker/uptime", False),
        ("a/b", "a/b/c", False),
        ("a/b/c", "a/b", False),
    ],
)
def test_a_topic_filter_matches_what_mqtt_says_it_matches(filter_, topic, matches):
    assert mqtt.topic_matches(filter_, topic) is matches


def test_the_brokers_own_statistics_are_never_taken_for_plant_data():
    """`#` matches everything a plant publishes and nothing the broker does.

    A subscriber that swept `$SYS` into tag history would fill the evidence
    table with the broker's uptime.
    """
    assert not mqtt.topic_matches("#", "$SYS/broker/messages/received")


# ------------------------------------------------------------- the tag wiring


def test_the_broker_wiring_lives_in_the_tag_map_beside_the_opc_machines(tag_map):
    subscriptions = mqtt.load_tag_subscriptions(tag_map)
    assert [s.tag for s in subscriptions] == ["GoodCount", "ScrapCount", "State", "MotorTemp"]
    assert all(s.equipment == "MIX01" for s in subscriptions)


def test_a_tag_map_with_no_broker_section_is_not_an_error(tmp_path):
    """Most plants start with one transport, and that is not a misconfiguration."""
    path = tmp_path / "tag_map.json"
    path.write_text(json.dumps({"machines": TAG_MAP["machines"]}), encoding="utf-8")
    assert mqtt.load_tag_subscriptions(path) == []


def test_two_entries_for_one_topic_are_refused_because_they_would_double_count(tmp_path):
    path = tmp_path / "tag_map.json"
    path.write_text(json.dumps({"machines": [], "mqtt": {"tags": [
        {"topic": "plant/a", "equipment": "MIX01", "tag": "GoodCount"},
        {"topic": "plant/a", "equipment": "PACK01", "tag": "GoodCount"},
    ]}}), encoding="utf-8")
    with pytest.raises(mqtt.SubscriptionError, match="double-count"):
        mqtt.load_tag_subscriptions(path)


def test_a_counter_published_as_an_increment_is_refused_with_the_two_ways_out(tmp_path):
    """MQTT is at-least-once, so a redelivered increment books units twice.

    The refusal names both honest alternatives rather than leaving the plant
    to guess: publish the machine's own total, or send the counts as inbound
    `counts` events, which carry the supplier's key and deduplicate on it.
    """
    path = tmp_path / "tag_map.json"
    path.write_text(json.dumps({"machines": [], "mqtt": {"tags": [
        {"topic": "plant/a", "equipment": "MIX01", "tag": "GoodCount", "counter": "increment"},
    ]}}), encoding="utf-8")
    with pytest.raises(mqtt.SubscriptionError) as exc:
        mqtt.load_tag_subscriptions(path)
    assert "total" in str(exc.value) and "counts` events" in str(exc.value)


def test_a_mapping_that_says_neither_which_machine_nor_where_to_find_it_is_refused(tmp_path):
    path = tmp_path / "tag_map.json"
    path.write_text(json.dumps({"machines": [], "mqtt": {"tags": [
        {"topic": "plant/a", "tag": "GoodCount"},
    ]}}), encoding="utf-8")
    with pytest.raises(mqtt.SubscriptionError, match="equipment_from"):
        mqtt.load_tag_subscriptions(path)


def test_a_state_map_naming_a_state_this_mes_does_not_have_is_refused_at_load(tmp_path):
    path = tmp_path / "tag_map.json"
    path.write_text(json.dumps({"machines": [], "mqtt": {"tags": [
        {"topic": "plant/a", "equipment": "MIX01", "tag": "State", "state_map": {"1": "sleeping"}},
    ]}}), encoding="utf-8")
    with pytest.raises(mqtt.SubscriptionError, match="state this MES has no"):
        mqtt.load_tag_subscriptions(path)


# ------------------------------------------------------------ payload parsing


def test_a_payload_that_is_just_a_number_is_the_reading(session, scope, ingest):
    send(ingest, scope, "plant/line1/MIX01/temp", b"41.5")
    row = session.scalars(select(TagValue).where(TagValue.tag == "MIX01.MotorTemp")).one()
    assert row.value_num == pytest.approx(41.5)


def test_a_value_is_read_out_of_the_payload_at_the_configured_path(session, scope, ingest):
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 10, "unit": "ea"})
    assert ingest.report.readings == 1


def test_a_payload_with_nothing_at_the_configured_path_is_refused_and_counted(session, scope, ingest):
    send(ingest, scope, "plant/line1/MIX01/good", {"count": 10})
    assert ingest.report.refused == 1
    assert "has nothing at 'value'" in "".join(ingest.report.refusals)
    assert booked(session) == []


def test_a_payload_that_is_not_json_at_all_is_refused_rather_than_stalling_the_interface(session, scope, ingest):
    send(ingest, scope, "plant/line1/MIX01/good", b"<xml/>")
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 4})
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 9})
    assert ingest.report.refused == 1
    assert sum(row.good_qty for row in booked(session)) == pytest.approx(5)


# ---------------------------------------------------------- counter discipline


def test_the_first_counter_reading_is_a_baseline_and_books_nothing(session, scope, ingest):
    """Never invent production. The machine's total says nothing about when
    the units before it were made, so none of them are booked."""
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 1200})
    assert booked(session) == []
    assert ingest.report.bookings == 0


def test_only_the_rise_above_the_last_reading_is_booked(session, scope, ingest):
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 100})
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 103})
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 110})
    assert [row.good_qty for row in booked(session)] == [3, 7]


def test_a_message_the_broker_delivers_twice_books_nothing_the_second_time(session, scope, ingest):
    """QoS 1 is at-least-once, and this is what makes that safe: a repeated
    total is not a rise, so it is not production."""
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 100})
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 104})
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 104})
    assert [row.good_qty for row in booked(session)] == [4]


def test_a_counter_that_went_backwards_is_a_reset_and_becomes_the_new_baseline(session, scope, ingest):
    """The units around a gateway restart are unknowable, so none are booked."""
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 900})
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 2})
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 5})
    assert [row.good_qty for row in booked(session)] == [3]


def test_scrap_is_booked_as_scrap_and_not_as_good(session, scope, ingest):
    send(ingest, scope, "plant/line1/MIX01/scrap", {"value": 10})
    send(ingest, scope, "plant/line1/MIX01/scrap", {"value": 12})
    row = booked(session)[-1]
    assert (row.good_qty, row.scrap_qty) == (0, 2)


def test_what_a_broker_counted_says_so_on_every_row_it_booked(session, scope, ingest):
    """A shadow beside an incumbent has to be able to say which pipe a number
    came down; `manual` and `opc` are different facts from this one."""
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 1})
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 4})
    row = booked(session)[-1]
    assert row.source is ProductionSource.EXTERNAL
    assert row.source_system == "gateway:line1"


def test_a_counter_that_is_not_a_number_is_refused_rather_than_coerced(session, scope, ingest):
    send(ingest, scope, "plant/line1/MIX01/good", {"value": "many"})
    assert ingest.report.refused == 1
    assert booked(session) == []


# -------------------------------------------------------------- state changes


def test_a_state_word_becomes_the_state_its_map_says_it_is(session, scope, ingest):
    send(ingest, scope, "plant/line1/MIX01/state", {"value": 3})
    open_now = session.scalars(
        select(EquipmentState).where(EquipmentState.ended_at.is_(None))).all()
    assert any(s.state is EquipmentStateName.DOWN for s in open_now)
    assert ingest.report.state_changes == 1


def test_a_state_word_the_map_does_not_know_is_refused_and_never_guessed(session, scope, ingest):
    """An unrecognised state code means the map is wrong. Quietly calling it
    idle would corrupt every availability figure computed afterwards."""
    send(ingest, scope, "plant/line1/MIX01/state", {"value": 7})
    assert ingest.report.refused == 1
    assert "not in the state_map" in "".join(ingest.report.refusals)


def test_a_reading_this_mes_could_not_act_on_is_still_kept_as_evidence(session, scope, ingest):
    """Tag history is what a person argues with the gateway from, so the
    unmappable value is exactly the one that must be there."""
    send(ingest, scope, "plant/line1/MIX01/state", {"value": 7})
    row = session.scalars(select(TagValue).where(TagValue.tag == "MIX01.State")).one()
    assert row.value_num == pytest.approx(7)


def test_a_gateway_republishing_the_same_state_is_counted_not_reported_as_a_change(session, scope, ingest):
    """Most gateways publish on a timer, not on a change. Calling every one of
    those a state change would put a state history nobody can read beside a
    number of transitions nobody can believe."""
    send(ingest, scope, "plant/line1/MIX01/state", {"value": 1})
    for _ in range(5):
        send(ingest, scope, "plant/line1/MIX01/state", {"value": 1})
    assert ingest.report.state_changes == 1
    assert ingest.report.states_unchanged == 5


def test_a_state_change_from_a_broker_names_the_broker_as_who_said_so(session, scope, ingest):
    send(ingest, scope, "plant/line1/MIX01/state", {"value": 3})
    entry = session.scalars(
        select(AuditLog).where(AuditLog.action == "equipment.state_changed")
        .order_by(AuditLog.id.desc())).first()
    assert entry.actor == "gateway:line1"


# ------------------------------------------------------------------ equipment


def test_a_reading_for_a_machine_this_mes_does_not_hold_is_refused(session, scope, tmp_path, mappings):
    """The master data being behind is a fact worth a refusal. Creating the
    machine would put a plant shape in the database that nobody drew."""
    path = tmp_path / "tag_map.json"
    path.write_text(json.dumps({"machines": [], "mqtt": {"tags": [
        {"topic": "plant/+/+/good", "equipment_from": 2, "tag": "GoodCount"},
    ]}}), encoding="utf-8")
    ingest = mqtt.Ingest(mqtt.load_tag_subscriptions(path), {}, source="gateway:line1")
    send(ingest, scope, "plant/line1/NOPE/good", b"10")
    assert ingest.report.refused == 1
    assert session.scalars(select(TagValue)).all() == []


def test_the_machine_can_come_out_of_the_topic_when_the_namespace_is_laid_out_that_way(session, scope, tmp_path):
    path = tmp_path / "tag_map.json"
    path.write_text(json.dumps({"machines": [], "mqtt": {"tags": [
        {"topic": "plant/+/+/good", "equipment_from": 2, "tag": "GoodCount"},
    ]}}), encoding="utf-8")
    ingest = mqtt.Ingest(mqtt.load_tag_subscriptions(path), {}, source="gateway:line1")
    send(ingest, scope, "plant/line1/MIX01/good", b"10")
    send(ingest, scope, "plant/line1/MIX01/good", b"14")
    assert [row.good_qty for row in booked(session)] == [4]


# -------------------------------------------------------- retained messages


def test_a_retained_message_sets_the_baseline_and_books_nothing(session, scope, ingest):
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 500}, retained=True)
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 503})
    assert [row.good_qty for row in booked(session)] == [3]
    assert ingest.report.retained == 1


def test_a_retained_state_message_never_becomes_a_state_change(session, scope, ingest):
    """The broker replays it as though it had just happened, and nothing in it
    says how old it is. Dating an hour-old state to now would put an hour of
    the wrong state into availability."""
    send(ingest, scope, "plant/line1/MIX01/state", {"value": 3}, retained=True)
    assert ingest.report.state_changes == 0
    assert ingest.report.retained == 1
    open_now = session.scalars(select(EquipmentState).where(EquipmentState.ended_at.is_(None))).all()
    assert not any(s.state is EquipmentStateName.DOWN for s in open_now)


def test_a_retained_message_is_not_written_to_tag_history_either(session, scope, ingest):
    send(ingest, scope, "plant/line1/MIX01/temp", b"41.5", retained=True)
    assert session.scalars(select(TagValue)).all() == []


# ---------------------------------------------------------------- the events


def test_an_event_on_a_topic_is_the_same_row_a_file_would_have_carried(session, scope, ingest):
    send(ingest, scope, "plant/line1/counts", {
        "entry_id": "T-1", "entered_at": "2026-09-10T17:00:00",
        "machine": "MIX01", "good_qty": 3, "scrap_qty": 1})
    assert ingest.report.events == 1
    row = booked(session)[-1]
    assert (row.good_qty, row.scrap_qty) == (3, 1)
    assert row.source_system == "gateway:line1"


def test_the_same_event_delivered_twice_changes_nothing_the_second_time(session, scope, ingest):
    """The supplier's own key is what makes redelivery safe, exactly as it
    does for the same file dropped in the folder twice."""
    payload = {"entry_id": "T-1", "entered_at": "2026-09-10T17:00:00",
               "machine": "MIX01", "good_qty": 3, "scrap_qty": 1}
    send(ingest, scope, "plant/line1/counts", payload)
    send(ingest, scope, "plant/line1/counts", payload)
    assert (ingest.report.events, ingest.report.duplicates) == (1, 1)
    assert len(booked(session)) == 1


def test_an_event_the_contract_will_not_take_is_refused_with_the_reason_in_words(session, scope, ingest):
    send(ingest, scope, "plant/line1/counts", {
        "entered_at": "2026-09-10T17:00:00", "machine": "MIX01", "good_qty": 3, "scrap_qty": 1})
    assert ingest.report.refused == 1
    assert "external_key" in "".join(ingest.report.refusals)
    assert session.scalars(select(InboundEvent)).all() == []


def test_a_stream_with_no_topic_is_read_from_its_folder_only(mappings):
    """A plant may take its downtime labels off the broker and its quality
    results out of a folder; the mapping file is where that is said."""
    subscribed = mqtt.load_event_subscriptions(mappings)
    assert list(subscribed) == ["plant/line1/counts"]
    assert subscribed["plant/line1/counts"].name == "counts"


def test_a_broker_payload_may_call_the_fields_something_other_than_the_files_columns(tmp_path, session, scope):
    """One stream, two transports, one set of rules: the source, the zone and
    the defaults are the plant's decisions and do not change with the pipe."""
    spec = json.loads(json.dumps(EVENT_MAPPING))
    spec["counts"]["fields"] = {
        "external_key": "id", "recorded_at": "ts", "equipment": "eq",
        "good": "good", "scrap": "scrap"}
    path = tmp_path / "inbound_mapping.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    mappings = folder.load_mapping(path)
    ingest = mqtt.Ingest([], mqtt.load_event_subscriptions(mappings), source="gateway:line1")
    send(ingest, scope, "plant/line1/counts", {
        "id": "T-9", "ts": "2026-09-10T17:00:00", "eq": "MIX01", "good": 2, "scrap": 0})
    assert ingest.report.events == 1
    assert booked(session)[-1].good_qty == 2


def test_a_field_name_that_is_not_in_the_contract_is_refused_at_load(tmp_path):
    spec = json.loads(json.dumps(EVENT_MAPPING))
    spec["counts"]["fields"] = {"external_key": "id", "widget": "w"}
    path = tmp_path / "inbound_mapping.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(folder.MappingError, match="'fields'"):
        folder.load_mapping(path)


def test_a_retained_message_on_an_event_topic_is_not_read_as_an_event(session, scope, ingest):
    """A retained event means the publisher is using the broker as a database.
    Said once, rather than pretending something new was read."""
    send(ingest, scope, "plant/line1/counts", {
        "entry_id": "T-1", "entered_at": "2026-09-10T17:00:00",
        "machine": "MIX01", "good_qty": 3, "scrap_qty": 1}, retained=True)
    assert ingest.report.events == 0
    assert ingest.report.refused == 1
    assert booked(session) == []


# ---------------------------------------------------------------- the report


def test_a_message_on_a_topic_nothing_is_mapped_to_is_counted_not_dropped_in_silence(session, scope, ingest):
    """A plant's broker carries far more than this MES was pointed at, and a
    subscriber that said nothing about the rest would look like it was
    working when it was pointed at the wrong tree."""
    send(ingest, scope, "plant/line1/PACK01/good", b"5")
    assert ingest.report.unmatched == 1
    assert ingest.report.messages == 1


def test_the_report_states_its_totals_over_everything_received(session, scope, ingest):
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 10})
    send(ingest, scope, "plant/line1/MIX01/good", {"value": 12})
    send(ingest, scope, "plant/line1/MIX01/state", {"value": 9})
    send(ingest, scope, "plant/other/thing", b"1")
    line = ingest.report.render()[0]
    assert "4 message(s) received" in line
    assert "1 booking(s)" in line
    assert "1 refused" in line
    assert "1 on topics nothing is mapped to" in line


def test_a_refusal_the_broker_repeats_is_grouped_with_a_count(session, scope, ingest):
    for _ in range(4):
        send(ingest, scope, "plant/line1/MIX01/state", {"value": 9})
    assert ingest.report.refused == 4
    assert len(ingest.report.refusals) == 1
    assert any(line.strip().startswith("4 x ") for line in ingest.report.render())


# ------------------------------------------------------------- the connection


def test_the_subscriber_subscribes_to_every_filter_it_was_given(session, scope, ingest):
    broker = FakeBroker([("plant/line1/MIX01/good", b'{"value": 1}', False)])
    listen(broker, ingest, scope)
    assert broker.subscribed == ingest.topics
    assert len(broker.subscribed) == 5
    assert ingest.report.messages == 1


def test_a_broker_that_goes_away_is_reconnected_to_without_losing_the_baselines(session, scope, ingest):
    """The counter baselines are this driver's only state, and they are
    rebuilt from the first message after the reconnection exactly as they
    were at start-up — so a reconnection books nothing it should not."""
    broker = FakeBroker([("plant/line1/MIX01/good", b'{"value": 40}', False)], fail_first=True)
    listen(broker, ingest, scope)
    assert broker.connections == 2
    assert booked(session) == []


# ------------------------------------------------------------- shadow and CLI


def test_the_subscriber_holds_no_publish_call_at_all():
    """The claim the shadow register makes about this module, checked rather
    than trusted: it listens, and there is no way for it to talk back."""
    source = Path(mqtt.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.strip().startswith("#"))
    body = code.split('"""', 2)[-1]
    assert ".publish(" not in body


def test_being_told_things_keeps_working_with_every_outbound_path_shut(session, scope, ingest, monkeypatch):
    """Shadow mode closes the paths by which this MES could change something
    outside itself. A shadow that stopped listening would be comparing itself
    with the incumbent on half the evidence."""
    monkeypatch.setenv("MES_SHADOW", "true")
    monkeypatch.setenv("MES_ERP_MODE", "off")
    monkeypatch.setenv("MES_UNS_MODE", "off")
    get_settings.cache_clear()
    try:
        assert shadow.enabled() is True
        send(ingest, scope, "plant/line1/MIX01/good", {"value": 10})
        send(ingest, scope, "plant/line1/MIX01/good", {"value": 13})
        send(ingest, scope, "plant/line1/MIX01/state", {"value": 3})
    finally:
        get_settings.cache_clear()
    assert [row.good_qty for row in booked(session)] == [3]
    assert ingest.report.state_changes == 1


def test_the_register_says_what_shadow_mode_does_to_the_subscriber():
    """A path the register does not mention is a path nobody decided about."""
    entry = shadow.path("inbound.mqtt_subscribe")
    assert entry.verdict == "allowed"
    assert entry.where.startswith("fsmes.integrations.inbound.mqtt")


def test_the_subscriber_and_the_publisher_do_not_share_a_client_id_by_default():
    """Brokers evict the older session when two clients connect with one id,
    so a shared default would have the two workers cutting each other off."""
    settings = Settings()
    assert settings.inbound_mqtt_client_id != settings.uns_client_id


def test_listening_is_off_until_a_plant_turns_it_on():
    assert Settings().inbound_mqtt_mode == "off"


def test_a_machine_state_set_from_the_broker_is_the_state_a_screen_reads(session, scope, ingest):
    """No parallel truth: a broker's state word lands in the same interval
    table the OPC agent writes, so one timeline holds the plant and a machine
    is never running in one transport and down in the other."""
    machine = masterdata.get_equipment(session, "MIX01")
    equipment.set_state(session, equipment_code="MIX01", state=EquipmentStateName.RUNNING,
                        actor="opc-agent")
    send(ingest, scope, "plant/line1/MIX01/state", {"value": 3})
    open_now = session.scalars(
        select(EquipmentState).where(EquipmentState.equipment_id == machine.id,
                                     EquipmentState.ended_at.is_(None))).all()
    assert len(open_now) == 1
    assert open_now[0].state is EquipmentStateName.DOWN
