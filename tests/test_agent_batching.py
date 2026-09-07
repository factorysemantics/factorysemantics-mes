"""The agent under a real factory's worth of tags.

This file exists because of two measured regressions.

The first: when each machine grew from four tags to thirteen, bottling's
breakdown detection fell from 100% to 0% - the MES recorded the breakdown
551 simulated seconds late, behind a queue of process values each opening
its own database session.

The second, at 27 stations and 60x: 0 of 5 breakdowns detected. The agent
subscribed with the library's default queue size, which is *unlimited*, so
the server delivered every single change (~2,700 a second) rather than one
sample per publish interval; the library spawned a task per reading; each
decision opened its own session behind a lock it reached only after a
thread hop, so State changes were booked in whatever order their threads
finished. The loop was 54,000 tasks behind inside a minute.

So these pin the shape that fixed both: the server keeps one value per
item per publish, a notification costs an append, and one consumer books
each batch in arrival order in one session.
"""

import asyncio

import pytest

from fsmes.integrations.opc import agent
from fsmes.integrations.opc.tag_map import MachineMap

STATES = {"1": "running", "4": "down"}
SPEC = MachineMap(equipment="WASH01", object="Washer", cycle_seconds=1.0,
                  analog="WashTemp",
                  extra_tags=("WashTempSP", "AlarmWord", "RunMinutes"), state_map=STATES)
OTHER = MachineMap(equipment="FILL01", object="Filler", cycle_seconds=1.0,
                   analog="FillWeight", state_map=STATES)


class Node:
    def __init__(self, name):
        self.name = name


def a_handler(tags, spec=SPEC):
    return agent._Handler({Node(t): (spec, t) for t in tags})


def test_a_reading_the_mes_acts_on_is_never_sampled_down():
    """State and the counters decide equipment state and book production. A
    breakdown that lands a batch late is a breakdown reported late."""
    assert set(agent.SEMANTIC_TAGS) == {"State", "GoodCount", "ScrapCount"}


def test_the_server_keeps_one_value_per_item_per_publish():
    """Queue size 0 is not "no queue", it is "queue everything". A publish
    interval that batches deliveries without thinning them is not sampling,
    and at 27 stations it buried the agent."""
    assert agent.QUEUE_SIZE == 1


@pytest.mark.asyncio
async def test_a_notification_costs_an_append(monkeypatch):
    """Nothing reaches a thread or the database from the OPC callback
    itself - that is what keeps the loop free to receive."""
    handler = a_handler(["State", "WashTemp"])
    monkeypatch.setattr(agent.asyncio, "to_thread",
                        lambda *a: (_ for _ in ()).throw(AssertionError("no thread from the callback")))
    for node in handler.node_info:
        await handler.datachange_notification(node, 1, None)
    assert len(handler._pending) == 2
    assert handler._wake.is_set()


@pytest.mark.asyncio
async def test_a_burst_is_booked_as_one_batch_in_arrival_order(monkeypatch):
    """The whole point: many readings, one session, the order they came in."""
    handler = a_handler(["State", "GoodCount", "WashTemp"])
    batches = []
    monkeypatch.setattr(handler, "_process", lambda batch: batches.append(batch))
    nodes = {tag: node for node, (_, tag) in handler.node_info.items()}
    order = [("WashTemp", 70.0), ("State", 4), ("GoodCount", 10), ("State", 1), ("WashTemp", 71.0)]
    for tag, value in order:
        await handler.datachange_notification(nodes[tag], value, None)

    booked = await handler._drain()
    assert booked == 5 and len(batches) == 1
    assert [(tag, value) for _spec, tag, value, _obs, _arr in batches[0]] == order
    assert handler._pending == [], "and the buffer starts again"


@pytest.mark.asyncio
async def test_the_consumer_drains_what_arrives_while_it_runs(monkeypatch):
    handler = a_handler(["State"])
    node = next(iter(handler.node_info))
    seen = []
    monkeypatch.setattr(handler, "_process", lambda batch: seen.append(len(batch)))
    handler.start()
    try:
        await handler.datachange_notification(node, 4, None)
        await handler.datachange_notification(node, 1, None)
        for _ in range(20):
            await asyncio.sleep(0.01)
            if sum(seen) == 2:
                break
        assert sum(seen) == 2, "the consumer must wake for what arrived"
    finally:
        await handler.stop("test")


def test_counters_coalesce_to_their_latest_value_per_machine(monkeypatch):
    """A delta is value-based; ten counter readings in one batch are one
    booking, and a machine's good and scrap go down together."""
    handler = a_handler(["GoodCount", "ScrapCount"])
    reports = []
    monkeypatch.setattr(handler, "_record", lambda *a: None)
    monkeypatch.setattr(agent.execution, "report",
                        lambda session, **kw: reports.append(kw) or object())
    handler.last_counts = {("WASH01", "GoodCount"): 100, ("WASH01", "ScrapCount"): 5}
    decisions = [(SPEC, "GoodCount", 101), (SPEC, "GoodCount", 102), (SPEC, "ScrapCount", 6),
                 (SPEC, "GoodCount", 104)]

    class Session:
        def begin_nested(self):
            import contextlib
            return contextlib.nullcontext()

    handler._book(Session(), decisions)
    assert reports == [{"equipment_code": "WASH01", "source": agent.ProductionSource.OPC,
                        "actor": "opc-agent", "good": 4, "scrap": 1}]
    assert handler.last_counts[("WASH01", "GoodCount")] == 104


def test_state_changes_are_booked_every_one_in_order(monkeypatch):
    """Coalescing would lose a stop that started and ended inside one
    batch; every State reading is booked, in sequence."""
    handler = a_handler(["State"])
    states = []
    monkeypatch.setattr(handler, "_record", lambda *a: None)
    monkeypatch.setattr(agent.equipment, "set_state",
                        lambda session, **kw: states.append((kw["equipment_code"], kw["state"])))

    class Session:
        def begin_nested(self):
            import contextlib
            return contextlib.nullcontext()

    handler._book(Session(), [(SPEC, "State", 1), (OTHER, "State", 4), (SPEC, "State", 4), (SPEC, "State", 1)])
    assert [code for code, _ in states] == ["WASH01", "FILL01", "WASH01", "WASH01"]


def test_a_failed_history_write_does_not_stop_the_line(monkeypatch):
    """History is the least important thing the agent does. Losing a batch
    must never take down the path that books production."""
    handler = a_handler(["State", "WashTemp"])
    booked = []

    def boom(_batch):
        raise RuntimeError("database busy")

    monkeypatch.setattr(handler, "_write_history", boom)
    monkeypatch.setattr(agent, "session_scope", lambda: __import__("contextlib").nullcontext(object()))
    monkeypatch.setattr(handler, "_book", lambda session, decisions: booked.append(len(decisions)))
    handler._process([(SPEC, "WashTemp", 70.0, None, 0.0), (SPEC, "State", 4, None, 0.0)])   # must not raise
    assert booked == [1], "the State change was booked although the history write failed"


def test_equipment_ids_are_looked_up_once(monkeypatch):
    """Per-reading lookups turned a list append into a round trip."""
    handler = a_handler(["WashTemp"])
    calls = []

    class Eq:
        id = 7

    monkeypatch.setattr(agent.masterdata, "get_equipment",
                        lambda session, code: (calls.append(code), Eq())[1])

    assert handler._equipment_id(None, "WASH01") == 7
    assert handler._equipment_id(None, "WASH01") == 7
    assert calls == ["WASH01"], "the second reading must not re-query"


def test_process_values_are_sampled_slower_than_decisions():
    """Two subscriptions, because two kinds of tag deserve two rates."""
    assert agent.HISTORY_RATIO > 1
    assert agent.MIN_HISTORY_MS >= 1000


def test_the_agent_says_how_far_behind_it_is(monkeypatch):
    """The scored run reads this to say whether a miss is the MES's or the
    harness's own. A window with no batches says nothing at all."""
    said = []
    monkeypatch.setattr(agent.log, "info", lambda event, **kw: said.append((event, kw)))
    stats = agent._Ingestion()
    stats.report(force=True)
    assert said == [], "nothing happened, nothing to say"
    stats.readings, stats.batches, stats.backlog_peak, stats.lag_max_s = 300, 3, 150, 0.42
    stats.report(force=True)
    assert said[0][0] == "agent ingestion"
    assert said[0][1]["backlog_peak"] == 150 and said[0][1]["lag_max_s"] == 0.42
    assert stats.readings == 0, "the window restarts"


def test_a_batch_that_fails_on_a_locked_database_is_booked_on_retry(monkeypatch):
    """The cutlery plant's first real-time hour: the batch carrying every
    machine's initial State failed on a momentary lock and was dropped, so
    the plant read as never observed for an hour. A failed batch is retried."""
    handler = a_handler(["State"])
    handler.BOOK_BACKOFF_S = 0.0
    attempts = []

    def flaky(session, decisions):
        attempts.append(len(decisions))
        if len(attempts) < 3:
            raise RuntimeError("database is locked")

    monkeypatch.setattr(handler, "_book", flaky)
    assert handler._book_with_retry([(SPEC, "State", 1)]) is True
    assert attempts == [1, 1, 1], "two failures, then booked - nothing dropped"

    attempts.clear()

    def always(session, decisions):
        attempts.append(1)
        raise RuntimeError("still locked")

    monkeypatch.setattr(handler, "_book", always)
    assert handler._book_with_retry([(SPEC, "State", 1)]) is False
    assert len(attempts) == handler.BOOK_ATTEMPTS


def test_an_inspection_group_is_one_event_and_an_unchanged_tag_is_filled_from_the_last_one():
    """A vision station stamps every tag of an event with one source time;
    OPC UA only notifies a value that changed, so a pass word of zero or an
    empty members field arrives once and then never again. The group is
    complete on its serial and sequence plus a publish cycle, with the rest
    filled from what the station last sent."""
    import time as _time
    from datetime import datetime

    from fsmes.integrations.opc import agent as agent_mod

    tags = ["InspSeq", "InspSerial", "InspPass", "InspMembers", "Insp_Length", "Insp_Gloss"]
    spec_map = {"spec": {"kind": "piece", "material": "UT-FORK",
                         "attributes": [{"name": "Length"}, {"name": "Gloss"}]}, "tags": set(tags)}
    handler = agent_mod._Handler({}, inspections={"FORK1_Mark": spec_map})
    handler.BOOK_BACKOFF_S = 0.0
    machine = SPEC.__class__(equipment="FORK1_Mark", object="FORK1_Mark", cycle_seconds=0.03)
    t1 = datetime(2026, 9, 6, 12, 0, 0)
    # Event 1: every tag arrives (first values all change).
    for tag, value in (("InspSerial", "F1-000000001"), ("Insp_Length", 165.0), ("Insp_Gloss", 82.0),
                       ("InspMembers", ""), ("InspPass", 0), ("InspSeq", 1)):
        handler._on_inspection(machine, tag, value, t1)
    events = handler._take_events()
    assert [e["serial"] for e in events] == ["F1-000000001"] and events[0]["partial"] is False
    assert events[0]["values"] == [165.0, 82.0] and events[0]["passed"] is True
    # Event 2: pass word and members unchanged, so they never arrive.
    t2 = datetime(2026, 9, 6, 12, 0, 0, 1)
    for tag, value in (("InspSerial", "F1-000000002"), ("Insp_Length", 164.8), ("Insp_Gloss", 83.1), ("InspSeq", 2)):
        handler._on_inspection(machine, tag, value, t2)
    assert handler._take_events() == [], "not yet: a publish cycle has not passed"
    handler._groups[("FORK1_Mark", t2)]["arrived"] -= agent_mod.GROUP_GRACE_S + 0.01
    events = handler._take_events()
    assert len(events) == 1 and events[0]["partial"] is False
    assert events[0]["fail_mask"] == 0 and events[0]["values"] == [164.8, 83.1] and events[0]["seq"] == 2
    # Event 3 fails one attribute: the pass word changes and arrives; members still do not.
    t3 = datetime(2026, 9, 6, 12, 0, 0, 2)
    for tag, value in (("InspSerial", "F1-000000003"), ("Insp_Length", 167.2), ("Insp_Gloss", 81.0),
                       ("InspPass", 1), ("InspSeq", 3)):
        handler._on_inspection(machine, tag, value, t3)
    handler._groups[("FORK1_Mark", t3)]["arrived"] -= agent_mod.GROUP_GRACE_S + 0.01
    events = handler._take_events()
    assert events[0]["passed"] is False and events[0]["fail_mask"] == 1
    # A group that never gets its serial is partial, counted, and still not dropped.
    t4 = datetime(2026, 9, 6, 12, 0, 0, 3)
    handler._on_inspection(machine, "Insp_Length", 165.1, t4)
    handler._groups[("FORK1_Mark", t4)]["arrived"] -= agent_mod.GROUP_TIMEOUT_S + 0.01
    assert handler._take_events() == [] and handler.inspection_stats["partial"] == 0, "no serial: nothing to record"
    _time.sleep(0)


def test_a_pile_of_inspection_events_is_written_in_bounded_transactions():
    """A plant running ahead of the agent piles events up; written as one
    transaction the pile grows without bound and everything behind it waits.
    The agent writes at most INGEST_MAX_EVENTS per transaction, in order."""
    import asyncio

    from fsmes.integrations.opc import agent as agent_mod

    handler = agent_mod._Handler({}, inspections={"X": {"spec": {"kind": "piece"}, "tags": set()}})
    seen: list[int] = []
    handler._ingest_with_retry = lambda events: seen.append(len(events)) or True   # type: ignore[assignment]
    n = agent_mod.INGEST_MAX_EVENTS
    handler._events = [{"equipment": "X", "serial": f"S{i}", "seq": i} for i in range(n * 2 + 5)]
    asyncio.run(handler._drain())
    assert seen == [n, n], "a pass writes its bounded share"
    asyncio.run(handler._drain())
    assert seen == [n, n, 5]
    assert handler.inspection_stats["batches"] == 3 and handler.inspection_stats["batch_max"] == n
    assert handler.inspection_stats["taken_max"] == n * 2 + 5


def test_events_are_written_pieces_first_and_a_container_missing_members_is_retried():
    """A stack's group can be assembled before its pieces' groups. Within a
    pass the events are ordered pieces, stacks, wraps, pallets; a container
    whose members were still not there is retried on later passes and only
    counted unknown when the retries are spent."""
    import asyncio
    from datetime import datetime

    from fsmes.integrations.opc import agent as agent_mod

    handler = agent_mod._Handler({}, inspections={"X": {"spec": {"kind": "piece"}, "tags": set()}})
    t = datetime(2026, 9, 7, 1, 0, 0)
    handler._events = [{"kind": "pallet", "serial": "PL", "seq": 1, "ts": t, "equipment": "X", "members": ["W"]},
                       {"kind": "stack", "serial": "ST", "seq": 1, "ts": t, "equipment": "X", "members": ["F"]},
                       {"kind": "piece", "serial": "F", "seq": 1, "ts": t, "equipment": "X"},
                       {"kind": "wrap", "serial": "W", "seq": 1, "ts": t, "equipment": "X", "members": ["ST"]}]
    assert [e["kind"] for e in handler._take_events()] == ["piece", "stack", "wrap", "pallet"]

    written: list[list[str]] = []
    retried: list[tuple[str, list[str]]] = []

    def fake_ingest(events):
        written.append([e["serial"] for e in events])
        handler.inspection_stats["events"] += len(events)
        if any(e["serial"] == "ST" for e in events):
            handler._deferred.append(("ST", ["F"], agent_mod.MEMBER_RETRIES))
        return True

    def fake_retry():
        deferred, handler._deferred = handler._deferred, []
        for serial, members, tries in deferred:
            retried.append((serial, members))
            if tries > 1:
                handler._deferred.append((serial, members, tries - 1))
            else:
                handler.inspection_stats["unknown_members"] += len(members)

    handler._ingest_with_retry = fake_ingest         # type: ignore[assignment]
    handler._retry_members = fake_retry              # type: ignore[assignment]
    handler._events = [{"kind": "stack", "serial": "ST", "seq": 2, "ts": t, "equipment": "X", "members": ["F"]}]
    asyncio.run(handler._drain())
    assert written == [["ST"]] and handler._deferred == [("ST", ["F"], agent_mod.MEMBER_RETRIES)]
    for _ in range(agent_mod.MEMBER_RETRIES):
        handler._events = [{"kind": "piece", "serial": f"P{_}", "seq": 3 + _, "ts": t, "equipment": "X"}]
        asyncio.run(handler._drain())
    assert len(retried) == agent_mod.MEMBER_RETRIES and handler._deferred == []
    assert handler.inspection_stats["unknown_members"] == 1, "counted unknown only when the retries are spent"


def test_a_pass_of_the_loop_writes_a_bounded_share_and_wakes_itself_for_the_rest():
    import asyncio

    from fsmes.integrations.opc import agent as agent_mod

    handler = agent_mod._Handler({}, inspections={"X": {"spec": {"kind": "piece"}, "tags": set()}})
    seen: list[int] = []
    handler._ingest_with_retry = lambda events: seen.append(len(events)) or True   # type: ignore[assignment]
    n = agent_mod.INGEST_MAX_EVENTS * agent_mod.INGEST_CHUNKS_PER_DRAIN
    handler._events = [{"kind": "piece", "equipment": "X", "serial": f"S{i}", "seq": i} for i in range(n + 7)]
    asyncio.run(handler._drain())
    assert sum(seen) == n and len(handler._events) == 7, "the rest waits for the next pass"
    assert handler._wake.is_set(), "and the next pass is asked for"
