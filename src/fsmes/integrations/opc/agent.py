"""The OPC agent: MES-TWIN's connection to the machine layer.

Downward: keeps each machine's OrderCode tag pointed at the next dispatched
order (empty = stop). Upward: subscribes to every mapped tag and turns data
changes into MES facts — tag history rows, equipment state changes, and
production bookings from counter deltas.

Everything that varies between servers lives in the tag map, not here: which
tag carries the process value, how the machine's State integer maps onto MES
states, whether tags are browsed or addressed by node id, and whether the
machine can be written to at all. A read-only source (a CSV replay, a
historian, a server the MES has no write rights on) simply has no order tag;
the agent then observes without commanding, and orders still flow because OPC
counter deltas auto-start a released operation.

The agent reconnects forever, so it survives PLC (or simulator) restarts.
"""

import asyncio
import contextlib
import time
from datetime import UTC, datetime
from pathlib import Path

import structlog
from asyncua import Client

from fsmes.config import Settings
from fsmes.db import session_scope
from fsmes.domain import ProductionSource, TagValue
from fsmes.integrations.opc.security import apply_security, explain_connection_error
from fsmes.integrations.opc.tag_map import MachineMap, load_manifest, load_tag_map
from fsmes.kernel.tags import COUNTER_TAGS, INSPECTION_PREFIX, INSPECTION_TAGS, SEMANTIC_TAGS
from fsmes.services import audit, equipment, execution, masterdata, serialization, triggers, workorders

log = structlog.get_logger("opc.agent")

_ORDER_SYNC_SECONDS = 2.0


# How much slower process-value history is sampled than the semantic tags,
# and the floor below which sampling faster buys nothing a person will read.
HISTORY_RATIO = 10
MIN_HISTORY_MS = 1000

# The queue the server keeps per monitored item. One means "the latest value
# at each publish" - which is what sampling every N milliseconds is supposed
# to mean. The library's default (0) is *unlimited*: the server queues every
# single change and the client receives all of them, so a publish interval
# batched the deliveries without ever thinning them. Measured at 27 stations
# and 60x: ~2,700 readings a second arriving, 54,000 tasks pending inside a
# minute, every scripted breakdown missed. The scorer already states its
# resolution as one sample per interval; this is what makes that true.
QUEUE_SIZE = 1

# How often the agent says how well it is keeping up. Read by the scored
# run, which reports it beside the scorecard: a result the pipeline itself
# fell behind on is not a result about the MES.
INGESTION_REPORT_SECONDS = 5.0
# The most inspection events one transaction takes. A plant running ahead
# of the agent piles events up faster than they are written; written as one
# transaction that pile grows past what the database does in a bounded time
# (a quarter of a million groups took a minute) and everything behind it
# waits. Bounded, the agent falls behind at a steady, reported rate and
# every transaction stays short.
INGEST_MAX_EVENTS = 2000
# How many such transactions one pass of the loop writes before it goes
# back for the tag readings. The counters and state words are small and
# time-sensitive; an inspection backlog must not hold them up for more
# than a couple of transactions.
INGEST_CHUNKS_PER_DRAIN = 2
# A container whose members were not all found is retried this many times
# on later passes, in case its pieces were written just after it.
MEMBER_RETRIES = 3
# Pieces before the stacks that hold them, stacks before wraps, wraps before
# pallets: the order a transaction needs so a container finds its members.
KIND_RANK = {"piece": 0, "stack": 1, "wrap": 2, "pallet": 3}
# An inspection group is taken once its serial and sequence are in and it
# has waited this long for the rest - one publish cycle or two. OPC UA
# notifies a value only when it changes, so a tag that repeats from one
# event to the next (a pass word of zero, an empty members field, an
# attribute the station always reads the same) never arrives again; the
# agent fills it from the last value the station sent, which is exactly
# what an unchanged value means.
GROUP_GRACE_S = 0.25
# A group still without its serial or sequence after this long is taken as
# partial and counted - recorded, never dropped silently.
GROUP_TIMEOUT_S = 3.0
# How long a station's current order is trusted before it is looked up again.
ORDER_CACHE_S = 10.0


def _is_inspection_tag(tag: str) -> bool:
    return tag in INSPECTION_TAGS or tag.startswith(INSPECTION_PREFIX)


class _Ingestion:
    """How far behind the agent is, over the current reporting window."""

    def __init__(self) -> None:
        self.readings = 0
        self.batches = 0
        self.backlog_peak = 0       # most readings waiting when a batch was taken
        self.lag_max_s = 0.0        # longest any reading waited to be booked
        self.busy_s = 0.0           # time spent booking
        self.window_started = time.monotonic()

    def report(self, force: bool = False) -> None:
        elapsed = time.monotonic() - self.window_started
        if not force and elapsed < INGESTION_REPORT_SECONDS:
            return
        if self.batches:
            log.info(
                "agent ingestion",
                readings=self.readings,
                batches=self.batches,
                backlog_peak=self.backlog_peak,
                lag_max_s=round(self.lag_max_s, 3),
                busy=round(self.busy_s / elapsed, 3) if elapsed > 0 else None,
            )
        self.readings = self.batches = self.backlog_peak = 0
        self.lag_max_s = self.busy_s = 0.0
        self.window_started = time.monotonic()


def _source_time(notification) -> datetime | None:
    """When the server says the value was observed, not when we stored it.

    Servers may leave SourceTimestamp unset, in which case the column's own
    default (now) is the honest answer.
    """
    try:
        stamp = notification.monitored_item.Value.SourceTimestamp
    except AttributeError:
        return None
    if stamp is None:
        return None
    # The MES records naive UTC throughout; asyncua hands back aware datetimes.
    return stamp.replace(tzinfo=None) if stamp.tzinfo else stamp


class _Handler:
    """Receives OPC data changes and books them into the MES.

    A notification costs an append. One consumer drains what has arrived,
    in arrival order, and books each batch in a single database session:
    history rows added together, counter deltas coalesced to one booking per
    machine, state changes in sequence. Before this every reading spawned
    its own task and every decision opened its own session - fine at six
    stations, and 54,000 tasks behind at twenty-seven, with State changes
    booked in whatever order their threads happened to finish.
    """

    def __init__(self, node_info: dict, evaluator=None, inspections: dict | None = None):
        self.node_info = node_info  # Node -> (MachineMap, tag_name)
        # Approved triggers watch every reading - fast tags and sampled ones
        # alike - and act through the same services a person would.
        self.evaluator = evaluator
        self.last_counts: dict[tuple[str, str], int] = {}
        # (spec, tag, value, observed-at, arrived-at) in arrival order.
        self._pending: list[tuple] = []
        self._wake = asyncio.Event()
        self._consumer: asyncio.Task | None = None
        # Equipment ids do not change while the agent runs: looked up once.
        self._equipment_ids: dict[str, int] = {}
        self.stats = _Ingestion()
        # Inspection groups: {equipment: {"spec": manifest block, "tags": expected tag names}}
        # and the groups still being assembled, keyed by the source time the
        # station stamped on every tag of the event.
        self.inspections: dict[str, dict] = inspections or {}
        self._groups: dict[tuple[str, datetime | None], dict] = {}
        # The last value each station sent for each group tag, with when:
        # what a tag that did not change since means.
        self._last_seen: dict[str, dict[str, tuple[datetime | None, object]]] = {}
        self._events: list[dict] = []
        # Containers whose members were not all there yet: (serial, members, tries left).
        self._deferred: list[tuple[str, list[str], int]] = []
        self._orders: dict[str, tuple[float, str | None]] = {}
        self.inspection_stats = {"events": 0, "partial": 0, "units": 0, "duplicates": 0, "unknown_members": 0}

    async def datachange_notification(self, node, value, _data) -> None:
        spec, tag = self.node_info[node]
        if _is_inspection_tag(tag) and spec.equipment in self.inspections:
            self._on_inspection(spec, tag, value, _source_time(_data))
            self._wake.set()
            return
        self._pending.append((spec, tag, value, _source_time(_data), time.monotonic()))
        self._wake.set()

    # ------------------------------------------------------------ inspection groups
    def _on_inspection(self, spec: MachineMap, tag: str, value, observed) -> None:
        """One tag of a station's group. The station stamps every tag of an
        event with the same source time, so the time is the group's key; the
        group is an event once every expected tag has arrived."""
        key = (spec.equipment, observed)
        group = self._groups.get(key)
        if group is None:
            group = self._groups[key] = {"values": {}, "arrived": time.monotonic()}
        group["values"][tag] = value
        seen = self._last_seen.setdefault(spec.equipment, {})
        previous = seen.get(tag)
        if previous is None or observed is None or previous[0] is None or observed >= previous[0]:
            seen[tag] = (observed, value)
        expected = self.inspections[spec.equipment]["tags"]
        if expected <= set(group["values"]):
            del self._groups[key]
            self._events.append(self._event(spec.equipment, observed, group["values"], partial=False))

    def _complete(self, code: str, observed, values: dict) -> dict:
        """A group whose remaining tags did not change since the station last
        sent them: filled from that last value, when it is not newer than the
        event itself."""
        filled = dict(values)
        seen = self._last_seen.get(code, {})
        for tag in self.inspections[code]["tags"]:
            if tag in filled:
                continue
            last = seen.get(tag)
            if last is not None and (observed is None or last[0] is None or last[0] <= observed):
                filled[tag] = last[1]
        return filled

    def _event(self, code: str, observed, values: dict, partial: bool) -> dict:
        info = self.inspections[code]["spec"]
        attrs = [a["name"] for a in info.get("attributes", [])]
        mask = int(values.get("InspPass") or 0)
        members = [m for m in str(values.get("InspMembers") or "").split(",") if m]
        event = {
            "kind": info["kind"], "equipment": code, "seq": int(values.get("InspSeq") or 0),
            "ts": observed or datetime.now(UTC).replace(tzinfo=None),
            "serial": str(values.get("InspSerial") or ""),
            "material": info.get("material", ""), "passed": mask == 0, "fail_mask": mask,
            "values": [values.get(f"{INSPECTION_PREFIX}{a}") for a in attrs], "partial": partial,
        }
        if info["kind"] == "wrap" and info.get("plate") and members:
            event["plate"] = {"serial": members[-1], "material": info["plate"]["material"]}
            members = members[:-1]
        if info["kind"] in ("stack", "wrap", "pallet"):
            event["members"] = members
        return event

    def _take_events(self) -> list[dict]:
        """The completed events; the groups that have their serial and
        sequence and have waited a publish cycle for the rest, filled from
        the station's last values; and any group still without a serial
        after too long - taken as partial and counted, never dropped in
        silence."""
        now = time.monotonic()
        for key, group in list(self._groups.items()):
            values = group["values"]
            waited = now - group["arrived"]
            if "InspSerial" in values and "InspSeq" in values and waited > GROUP_GRACE_S:
                del self._groups[key]
                self._events.append(self._event(key[0], key[1], self._complete(key[0], key[1], values), partial=False))
            elif waited > GROUP_TIMEOUT_S:
                del self._groups[key]
                if values.get("InspSerial"):
                    filled = self._complete(key[0], key[1], values)
                    self._events.append(self._event(key[0], key[1], filled, partial=True))
                    self.inspection_stats["partial"] += 1
        events, self._events = self._events, []
        events.sort(key=lambda e: (KIND_RANK.get(e.get("kind"), 9), e.get("ts") or datetime.min))
        return events

    def _order_for(self, session, code: str) -> str | None:
        cached = self._orders.get(code)
        if cached and time.monotonic() - cached[0] < ORDER_CACHE_S:
            return cached[1]
        ops = workorders.dispatch_list(session, code)
        order = ops[0].order.code if ops else None
        self._orders[code] = (time.monotonic(), order)
        return order

    def _ingest(self, events: list[dict]) -> None:
        with session_scope() as session:
            for e in events:
                e["order"] = self._order_for(session, e["equipment"])
            out = serialization.ingest_inspections(session, events)
        self.inspection_stats["events"] += len(events)
        for k in ("units", "duplicates"):
            self.inspection_stats[k] += out[k]
        if out["unknown_members"]:
            # Not counted as unknown yet: the pieces may be in the next
            # transaction. The containers of this one are retried.
            for e in events:
                members = list(e.get("members") or [])
                if e.get("plate"):
                    members.append(e["plate"]["serial"])
                if members:
                    self._deferred.append((e["serial"], members, MEMBER_RETRIES))

    def _retry_members(self) -> None:
        """Containers whose members were not all found when they were
        written: attach what is there now; give up, and count, after the
        retries are spent."""
        if not self._deferred:
            return
        deferred, self._deferred = self._deferred, []
        with session_scope() as session:
            for serial, members, tries in deferred:
                missing = serialization.attach_members(session, serial, members)
                if missing and tries > 1:
                    self._deferred.append((serial, members, tries - 1))
                elif missing:
                    self.inspection_stats["unknown_members"] += missing
                    log.warning("container members never seen", container=serial, missing=missing)

    def _ingest_with_retry(self, events: list[dict]) -> bool:
        for attempt in range(1, self.BOOK_ATTEMPTS + 1):
            try:
                self._ingest(events)
                return True
            except Exception:
                if attempt == self.BOOK_ATTEMPTS:
                    log.exception("failed to ingest inspection events", events=len(events))
                    return False
                log.warning("inspection ingest failed, retrying", events=len(events), attempt=attempt)
                time.sleep(self.BOOK_BACKOFF_S * 2 ** (attempt - 1))
        return False

    def start(self) -> None:
        """Begin booking. Separate from construction so a handler can be
        built and inspected without a running loop."""
        self._consumer = asyncio.create_task(self._consume(), name="opc-ingest")

    async def stop(self, why: str) -> None:
        """Stop the consumer, then book what it had not reached. Whatever
        happens to the connection - a dropped link, a SIGTERM, a restarted
        PLC - the readings already received are the plant's, not ours to
        discard."""
        if self._consumer is not None:
            self._consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._consumer
            self._consumer = None
        await self.flush(why)

    async def _consume(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            await self._drain()

    async def _drain(self) -> int:
        """Book everything that has arrived: the tag readings first, as one
        batch, then a bounded share of the inspection events - the rest
        wake the loop again. Returns how many readings."""
        batch, self._pending = self._pending, []
        rows = await self._drain_readings(batch)
        events = self._take_events()
        st = self.inspection_stats
        st["taken_max"] = max(st.get("taken_max", 0), len(events))
        limit = INGEST_MAX_EVENTS * INGEST_CHUNKS_PER_DRAIN
        if len(events) > limit:
            events, rest = events[:limit], events[limit:]
            self._events = rest + self._events
            self._wake.set()
        if events and self._deferred:
            await asyncio.to_thread(self._retry_members)
        for start in range(0, len(events), INGEST_MAX_EVENTS):
            chunk = events[start:start + INGEST_MAX_EVENTS]
            started = time.monotonic()
            try:
                await asyncio.to_thread(self._ingest_with_retry, chunk)
            except Exception:
                log.exception("failed to ingest a batch of inspection events", events=len(chunk))
            took = time.monotonic() - started
            st["batches"] = st.get("batches", 0) + 1
            st["ingest_s"] = st.get("ingest_s", 0.0) + took
            st["batch_max"] = max(st.get("batch_max", 0), len(chunk))
            st["ingest_max_s"] = max(st.get("ingest_max_s", 0.0), took)
        if events and time.monotonic() - st.get("reported", 0.0) >= INGESTION_REPORT_SECONDS:
            st["reported"] = time.monotonic()
            log.info("inspection ingestion", events=st["events"], units=st["units"], partial=st["partial"],
                     duplicates=st["duplicates"], unknown_members=st["unknown_members"],
                     batches=st["batches"], batch_max=st["batch_max"], taken_max=st["taken_max"],
                     ingest_mean_ms=round(1000 * st["ingest_s"] / st["batches"], 1),
                     ingest_max_ms=round(1000 * st["ingest_max_s"], 1), pending_groups=len(self._groups),
                     backlog_events=len(self._events), deferred_containers=len(self._deferred))
        return rows

    async def _drain_readings(self, batch: list[tuple]) -> int:
        if not batch:
            return 0
        started = time.monotonic()
        stats = self.stats
        stats.backlog_peak = max(stats.backlog_peak, len(batch))
        stats.lag_max_s = max(stats.lag_max_s, started - batch[0][4])
        try:
            # The DB work runs in a thread so a busy database never blocks
            # the OPC loop; one batch at a time, so order is kept.
            await asyncio.to_thread(self._process, batch)
        except Exception:
            log.exception("failed to book a batch of readings", rows=len(batch))
        stats.busy_s += time.monotonic() - started
        stats.readings += len(batch)
        stats.batches += 1
        stats.report()
        return len(batch)

    async def flush(self, why: str) -> None:
        """Book whatever is waiting, now. Called before the agent lets go of
        a connection and before it exits, because a reading the plant sent
        and the MES silently dropped is the one outcome this module may not
        have."""
        rows = await self._drain()
        if rows:
            log.info("readings flushed", rows=rows, why=why)

    def _process(self, batch: list[tuple]) -> None:
        """One batch, in arrival order: triggers first, then the decisions
        in their own session, then the history in another. Two sessions so
        a failed history write - the least important thing the agent does -
        can never take the production booking down with it."""
        if self.evaluator is not None:
            for spec, tag, value, _observed, _arrived in batch:
                try:
                    for firing in self.evaluator.observe(spec.equipment, tag, value):
                        log.info("trigger fired", **firing)
                except Exception:
                    log.exception("trigger evaluation failed", equipment=spec.equipment, tag=tag)

        decisions = [(spec, tag, value) for spec, tag, value, _o, _a in batch if tag in SEMANTIC_TAGS]
        history = [(spec.equipment, tag, value, observed)
                   for spec, tag, value, observed, _a in batch if tag not in SEMANTIC_TAGS]
        if decisions:
            self._book_with_retry(decisions)
        if history:
            try:
                self._write_history(history)
            except Exception:
                log.exception("failed to write tag history", rows=len(history))

    # A batch that fails is tried again before it is given up. The cutlery
    # plant found why: the first batch after subscribing carries every
    # machine's initial State, and a database locked for a moment by the
    # API's own start-up made that one batch fail - after which the plant's
    # machines were "never observed" for an hour, because a State arrives
    # only when it changes. Dropping readings the plant sent is the one
    # outcome this module may not have; a moment's lock is not a reason to.
    #
    # It is not what stops "database is locked" any more, and it never
    # should have been: booking opens a savepoint and writes inside it, and
    # db.py now begins that transaction with the write lock already taken.
    # What is left for this retry is a database genuinely busy for longer
    # than busy_timeout, and any other transient failure - not a bug being
    # slept through.
    BOOK_ATTEMPTS = 4
    BOOK_BACKOFF_S = 0.5

    def _book_with_retry(self, decisions: list[tuple]) -> bool:
        for attempt in range(1, self.BOOK_ATTEMPTS + 1):
            try:
                with session_scope() as session:
                    self._book(session, decisions)
                return True
            except Exception:
                if attempt == self.BOOK_ATTEMPTS:
                    log.exception("failed to book decisions", rows=len(decisions), attempts=attempt)
                    return False
                log.warning("booking failed, retrying", rows=len(decisions), attempt=attempt,
                            wait_s=self.BOOK_BACKOFF_S * 2 ** (attempt - 1))
                time.sleep(self.BOOK_BACKOFF_S * 2 ** (attempt - 1))
        return False

    def _book(self, session, decisions: list[tuple]) -> None:
        """State changes are booked every one, in order. Counters coalesce
        to their latest value per machine and tag - a delta is value-based,
        so the readings in between would add nothing but sessions - and a
        machine's good and scrap go down as one booking."""
        latest: dict[tuple[str, str], int] = {}
        for i, (spec, tag, _value) in enumerate(decisions):
            if tag in COUNTER_TAGS:
                latest[(spec.equipment, tag)] = i
        deltas: dict[str, dict[str, int]] = {}
        order: list[str] = []
        for i, (spec, tag, value) in enumerate(decisions):
            if tag == "State":
                try:
                    with session.begin_nested():
                        self._record(session, spec, tag, value)
                        equipment.set_state(
                            session, equipment_code=spec.equipment,
                            state=spec.to_state(value), actor="opc-agent")
                except Exception:
                    log.exception("failed to process data change",
                                  equipment=spec.equipment, tag=tag, value=value)
            elif tag in COUNTER_TAGS and latest.get((spec.equipment, tag)) == i:
                self._record(session, spec, tag, value)
                delta = self._counter_delta(spec.equipment, tag, int(value))
                if delta:
                    if spec.equipment not in deltas:
                        order.append(spec.equipment)
                    deltas.setdefault(spec.equipment, {})[
                        "good" if tag == "GoodCount" else "scrap"] = delta
        for code in order:
            quantities = deltas[code]
            try:
                with session.begin_nested():
                    op = execution.report(session, equipment_code=code, source=ProductionSource.OPC,
                                          actor="opc-agent", **quantities)
                if op is None:
                    log.warning("machine counted with no active order", equipment=code, **quantities)
            except Exception:
                log.exception("failed to book production", equipment=code, **quantities)

    def _record(self, session, spec: MachineMap, tag: str, value) -> None:
        """A semantic reading is history too - and it keeps the machine's
        own tag name (RD01.State), because the point of tag history is to be
        able to argue with the PLC about what it actually sent."""
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        session.add(TagValue(
            equipment_id=self._equipment_id(session, spec.equipment),
            tag=f"{spec.equipment}.{tag}",
            value_num=float(value) if numeric else None,
            value_text=None if numeric else str(value),
        ))

    def _equipment_id(self, session, code: str) -> int:
        """Equipment ids do not change while the agent runs; looking one up
        per reading turned a list append into a database round trip."""
        if code not in self._equipment_ids:
            self._equipment_ids[code] = masterdata.get_equipment(session, code).id
        return self._equipment_ids[code]

    def _write_history(self, batch: list[tuple[str, str, object, object]]) -> None:
        """One session, every reading in it - each keeping its own moment.

        Batching once collapsed a whole batch onto the instant it was
        flushed, because `TagValue.ts` defaults at insert time. That turned
        two hundred readings into two hundred points stacked on one tick
        followed by a gap, in the one table whose stated purpose is being
        able to argue with the PLC about what it actually sent.
        """
        with session_scope() as session:
            for code, tag, value, observed in batch:
                numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
                row = TagValue(
                    equipment_id=self._equipment_id(session, code),
                    tag=f"{code}.{tag}",
                    value_num=float(value) if numeric else None,
                    value_text=None if numeric else str(value),
                )
                if observed is not None:
                    row.ts = observed
                session.add(row)

    def _counter_delta(self, equipment_code: str, tag: str, value: int) -> int:
        """The increase of a monotonic machine counter. The one rule that
        outranks all others: never invent production. Stale or duplicate
        readings count for nothing; a counter that fell to near zero is a PLC
        reset and becomes the new baseline (the units around the reset are
        unknowable, so none are booked)."""
        key = (equipment_code, tag)
        last = self.last_counts.get(key)
        if last is None or value < last // 2:
            self.last_counts[key] = value
            return 0
        if value <= last:
            return 0
        self.last_counts[key] = value
        return value - last


def _desired_order_codes(equipment_codes: list[str]) -> dict[str, str]:
    with session_scope() as session:
        return {
            code: (ops[0].order.code if (ops := workorders.dispatch_list(session, code)) else "")
            for code in equipment_codes
        }


async def _order_code_loop(order_nodes: dict) -> None:
    """Keep each machine's OrderCode pointing at its next dispatched order."""
    written: dict[str, str] = {}
    while True:
        desired = await asyncio.to_thread(_desired_order_codes, list(order_nodes))
        for equipment_code, order_code in desired.items():
            if written.get(equipment_code) != order_code:
                await order_nodes[equipment_code].write_value(order_code)
                written[equipment_code] = order_code
                log.info("order code written to machine", equipment=equipment_code, order=order_code or "(none)")
        await asyncio.sleep(_ORDER_SYNC_SECONDS)


async def resolve_nodes(client: Client, machines: list[MachineMap], namespace: str) -> tuple[dict, dict]:
    """Locate every mapped tag, by node id where the map gives one and by
    browsing otherwise. Browsing is only possible on servers that expose an
    object tree, which many real ones do not.

    Public because `fsmes opc-verify` resolves through this exact function: what
    the check proves live is, by construction, what the agent will subscribe to.
    """
    node_info: dict = {}
    order_nodes: dict = {}
    idx = None
    for spec in machines:
        if spec.by_node_id:
            for tag in spec.tags:
                node_info[client.get_node(spec.node(tag))] = (spec, tag)
            # A read-only machine has no order tag; one addressed purely by a
            # `nodes` listing that omits it is read-only by omission.
            order_node = spec.node(spec.order_tag) if spec.order_tag else None
            if order_node:
                order_nodes[spec.equipment] = client.get_node(order_node)
            continue
        if idx is None:  # only browsed machines need the namespace to exist
            idx = await client.get_namespace_index(namespace)
        obj = await client.nodes.objects.get_child(f"{idx}:{spec.object}")
        for tag in spec.tags:
            node_info[await obj.get_child(f"{idx}:{tag}")] = (spec, tag)
        if spec.order_tag:
            order_nodes[spec.equipment] = await obj.get_child(f"{idx}:{spec.order_tag}")
    return node_info, order_nodes


def _record_connection(settings: Settings, machines: list[MachineMap]) -> None:
    """Write down which machine layer we actually connected to.

    Provenance, not decoration. Every figure the MES reports downstream — OEE,
    order progress, the units moving on the 3D line — is only as good as the
    source that fed it, and "which OPC server was this?" is otherwise
    unanswerable after the fact: a replayed CSV and a real KEPServerEX produce
    identical rows. Configuration cannot answer it either, because the agent and
    the API are separate processes that can be pointed at different things.
    """
    with session_scope() as session:
        audit.record(
            session,
            actor="opc-agent",
            action="opc.connected",
            entity_type="machine_layer",
            entity_id=settings.opc_endpoint,
            after={
                "endpoint": settings.opc_endpoint,
                "tag_map": Path(settings.tag_map_file).name,
                "security": settings.opc_security or "none (anonymous)",
                "machines": [m.equipment for m in machines],
                "addressing": "node_id" if any(m.node_id for m in machines) else "browse",
            },
        )


def inspection_specs(manifest: dict, machines: list[MachineMap]) -> dict[str, dict]:
    """Which machines publish an inspection group, what it judges, and the
    tags the agent must see before the group is an event."""
    out: dict[str, dict] = {}
    tables = manifest.get("tables", {})
    for spec in machines:
        table = tables.get(spec.object, {})
        block = table.get("inspection")
        if not block:
            continue
        tags = {t for t, meta in table.get("tags", {}).items() if meta.get("kind") == "inspection"}
        out[spec.equipment] = {"spec": block, "tags": tags}
    return out


async def run(settings: Settings) -> None:
    manifest = load_manifest(settings.replay_dir)
    machines = load_tag_map(settings.tag_map_file, manifest)
    inspections = inspection_specs(manifest, machines)
    if inspections:
        log.info("inspection groups mapped", stations=sorted(inspections))
    while True:
        try:
            client = Client(settings.opc_endpoint)
            await apply_security(client, settings)
            async with client:
                node_info, order_nodes = await resolve_nodes(client, machines, settings.opc_namespace)

                handler = _Handler(node_info, evaluator=triggers.Evaluator(scope=session_scope),
                                   inspections=inspections)

                # The tags the MES reasons about arrive at full rate; the
                # rest are history and are sampled. Splitting them is what
                # keeps a machine's thirteen signals from burying the one
                # State change that says it broke down. Inspection groups are
                # events, not samples: every change is queued and delivered,
                # because a queue of one would keep one judged unit in ten.
                fast = [n for n, (_, tag) in node_info.items() if tag in SEMANTIC_TAGS]
                slow = [n for n, (_, tag) in node_info.items()
                        if tag not in SEMANTIC_TAGS and not _is_inspection_tag(tag)]
                groups = [n for n, (spec, tag) in node_info.items()
                          if _is_inspection_tag(tag) and spec.equipment in inspections]

                subscription = await client.create_subscription(
                    settings.opc_publish_ms, handler
                )
                await subscription.subscribe_data_change(fast, queuesize=QUEUE_SIZE)
                if slow:
                    history = await client.create_subscription(
                        max(settings.opc_publish_ms * HISTORY_RATIO,
                            MIN_HISTORY_MS), handler
                    )
                    await history.subscribe_data_change(slow, queuesize=QUEUE_SIZE)
                if groups:
                    events = await client.create_subscription(
                        max(settings.opc_publish_ms // 5, 50), handler)
                    await events.subscribe_data_change(groups, queuesize=0)
                handler.start()
                # Only after the subscription is live, so this records a source
                # that is genuinely feeding us rather than one we merely dialled.
                await asyncio.to_thread(_record_connection, settings, machines)
                log.info(
                    "agent online",
                    endpoint=settings.opc_endpoint,
                    machines=[m.equipment for m in machines],
                    commanded=sorted(order_nodes) or "none (read-only source)",
                )
                writer = asyncio.create_task(_adjustment_loop(node_info, machines, manifest))
                try:
                    if not order_nodes:
                        # Nothing to command — just hold the subscription open.
                        await asyncio.Future()
                    await _order_code_loop(order_nodes)
                finally:
                    writer.cancel()
                    # The handler is rebuilt per connection, so this is the
                    # last chance to book what it received.
                    await handler.stop("connection closing")
        except (TimeoutError, OSError) as exc:
            log.warning("OPC connection lost, retrying in 3s", error=str(exc))
            await asyncio.sleep(3)
        except Exception as exc:
            # A rejected certificate or refused login looks like a hang
            # otherwise; say what actually needs doing, then keep retrying.
            log.warning("OPC connection failed, retrying in 3s", error=explain_connection_error(exc))
            await asyncio.sleep(3)


# ------------------------------------------------------------- write-back

_ADJUSTMENT_POLL_SECONDS = 5.0


def _manifest_bounds(machines: list[MachineMap], manifest: dict) -> dict[str, dict[str, dict]]:
    """equipment -> tag -> {min, max} for every setpoint the manifest declares
    writable. The agent's own copy: guard three."""
    tables = (manifest or {}).get("tables", {})
    out: dict[str, dict[str, dict]] = {}
    for spec in machines:
        table = tables.get(spec.object) or tables.get(spec.equipment) or {}
        for tag, meta in table.get("tags", {}).items():
            if meta.get("writable") and meta.get("min") is not None and meta.get("max") is not None:
                out.setdefault(spec.equipment, {})[tag] = {"min": float(meta["min"]), "max": float(meta["max"])}
    return out


async def write_approved_adjustments(nodes: dict, bounds: dict, scope) -> list[str]:
    """Write every approved recommendation whose tag this agent holds a node
    for, refusing anything outside the agent's own manifest bounds. Returns
    the codes written. Called on a loop; also directly by the tests."""
    from fsmes.services import adjustments

    with scope() as session:
        due = adjustments.approved_writes(session)
    written = []
    for rec in due:
        limits = bounds.get(rec["equipment"], {}).get(rec["tag"])
        node = nodes.get((rec["equipment"], rec["tag"]))
        if limits is None or node is None:
            reason = ("the agent's manifest does not declare this tag writable" if limits is None
                      else "the agent holds no node for this tag")
            with scope() as session:
                adjustments.mark_failed(session, rec["code"], reason)
            log.warning("adjustment refused by the agent", code=rec["code"], why=reason)
            continue
        if not limits["min"] <= rec["value"] <= limits["max"]:
            with scope() as session:
                adjustments.mark_failed(session, rec["code"],
                                        f"{rec['value']:g} is outside the agent's manifest bounds "
                                        f"{limits['min']:g}-{limits['max']:g}")
            log.warning("adjustment refused by the agent: out of bounds", code=rec["code"])
            continue
        try:
            await node.write_value(float(rec["value"]))
        except Exception as exc:
            with scope() as session:
                adjustments.mark_failed(session, rec["code"], f"write failed: {exc}")
            log.exception("adjustment write failed", code=rec["code"])
            continue
        with scope() as session:
            adjustments.mark_written(session, rec["code"], float(rec["value"]))
        log.info("setpoint written", code=rec["code"], equipment=rec["equipment"], tag=rec["tag"],
                 value=rec["value"])
        written.append(rec["code"])
    return written


async def verify_written_adjustments(nodes: dict, scope, before: dict[str, float]) -> None:
    """After the wait, read the driven process value back and record whether
    it followed. `before` remembers the PV at write time per code."""
    from fsmes.services import adjustments

    with scope() as session:
        due = adjustments.due_for_verification(session)
    for rec in due:
        pv_node = nodes.get((rec["equipment"], rec["drives"])) if rec["drives"] else None
        try:
            after = float(await pv_node.read_value()) if pv_node is not None else None
        except Exception:
            after = None
        with scope() as session:
            adjustments.verify(session, rec["code"], pv_before=before.pop(rec["code"], rec["current_value"]),
                               pv_after=after)
        log.info("adjustment verified", code=rec["code"], pv_after=after)


async def _adjustment_loop(node_info: dict, machines: list[MachineMap], manifest: dict) -> None:
    """The write path: approved recommendations go to the PLC, then get checked."""
    nodes = {(spec.equipment, tag): node for node, (spec, tag) in node_info.items()}
    bounds = _manifest_bounds(machines, manifest)
    pv_before: dict[str, float] = {}
    while True:
        try:
            # Remember the driven PV before writing, so verification can say
            # how far the process travelled rather than only where it ended.
            # Read-only, and it says so: this transaction stays open across
            # OPC reads, and on SQLite a writing transaction holds the one
            # write lock for as long as it is open.
            with session_scope(write=False) as session:
                from fsmes.services import adjustments
                for rec in adjustments.approved_writes(session):
                    pv = nodes.get((rec["equipment"], rec["drives"])) if rec["drives"] else None
                    if pv is not None and rec["code"] not in pv_before:
                        with contextlib.suppress(Exception):
                            pv_before[rec["code"]] = float(await pv.read_value())
            await write_approved_adjustments(nodes, bounds, session_scope)
            await verify_written_adjustments(nodes, session_scope, pv_before)
        except Exception:
            log.exception("adjustment loop failed; continuing")
        await asyncio.sleep(_ADJUSTMENT_POLL_SECONDS)


# ------------------------------------------------------- one plant, many agents

def agent_configs(settings: Settings) -> list[Settings]:
    """One settings object per OPC server. With `opc_endpoints` empty that is
    the plant's single endpoint; otherwise each entry inherits the plant's
    settings and overrides its endpoint, tag map and namespace."""
    import json

    raw = (settings.opc_endpoints or "").strip()
    if not raw:
        return [settings]
    entries = json.loads(raw)
    if not isinstance(entries, list) or not entries:
        raise ValueError("MES_OPC_ENDPOINTS must be a non-empty JSON list")
    configs = []
    for entry in entries:
        update = {"opc_endpoint": entry["endpoint"]}
        if entry.get("tag_map"):
            update["tag_map_file"] = Path(entry["tag_map"])
        if entry.get("namespace"):
            update["opc_namespace"] = entry["namespace"]
        configs.append(settings.model_copy(update=update))
    return configs


async def run_all(settings: Settings) -> None:
    """Every agent the plant needs, in one process, each with its own
    connection, subscriptions and tag map, feeding one database."""
    configs = agent_configs(settings)
    if len(configs) == 1:
        await run(configs[0])
        return
    log.info("agents starting", count=len(configs), endpoints=[c.opc_endpoint for c in configs])
    await asyncio.gather(*(run(c) for c in configs))
