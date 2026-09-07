"""Replay the KepSim line's CSVs as an OPC UA server.

`labs/kepsim` generates an hour of a six-station line, one row per second, for
KEPServerEX's Advanced Simulator driver to play back as live tags. This module
plays exactly the same tables into exactly the same shape of address space,
inside the twin — so the whole scenario (the breakdown that starves everything
downstream, the scrap burst, the changeover, the counter reset) can be run and
tested on any machine, with no Kepware, no ODBC and no licence window.

The only difference from the Kepware path is addressing: here the tags sit in
a browsable object tree, there they are flat string node ids. That difference
lives entirely in the tag map — config/tag_map_kepsim.json versus
config/tag_map_kepware.json — which is what lets the agent, the MES and every
test above it stay identical across both.

Fidelity matters more than convenience here, so the replay reproduces the
awkward parts of the real source too: State is served as the raw integer the
PLC would send, counters wrap back to zero when the file loops (a free
counter-reset drill every hour, and a genuine test of the agent's reset
handling).

Readings are replayed and nothing may write them. **Setpoints are the
exception, and they are the point**: a setpoint is what a plant is *told*,
so it lives here as a writable node with no column behind it, and the
reading it drives moves relative to it with the process's own lag. That is
what lets an approved recommendation - from an engineer or an agent - reach
the simulated PLC and have the physics answer.
"""

import asyncio
import csv
import os
import random
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from asyncua import Server, ua

from fsmes.config import Settings
from fsmes.integrations.opc.tag_map import MachineMap, load_manifest, load_tag_map
from fsmes.kernel.tags import COUNTER_TAGS as COUNTERS
from fsmes.kernel.tags import INSPECTION_PREFIX, INSPECTION_TAGS, INTEGER_TAGS, MANIFEST_NAME

log = structlog.get_logger("opc.replay")

# Written by the generator alongside the per-station tables.
LINE_TABLE = "Line"

# How often a replay that has fallen behind says so again.
BEHIND_REPORT_SECONDS = 5.0

class Setpoint:
    """A live value the plant is *told*, and the reading that chases it.

    Everything else in this module is replayed from a file. A setpoint must
    not be: it is the one thing an engineer - or an approved recommendation
    from an agent - writes into the plant, so it lives here as a writable
    node with no column behind it.

    The process value it drives is served as the replayed reading plus the
    distance the setpoint has moved from where the line was generated. So
    the scripted physics still happen (a drift still ramps, noise still
    wobbles) and they happen *around the new setpoint*. Nobody writing
    anything leaves the offset at zero and the served value byte-identical
    to before - which is why the scorer keeps scoring the same line.

    The approach is first-order with the tag's own time constant: a washer
    takes minutes to reach a new temperature, and a demo where the reading
    snaps to the setpoint would be a lie about the physics that the whole
    write-back story rests on.
    """

    def __init__(self, name: str, meta: dict, node, period_s: float):
        self.name = name
        self.drives = meta["drives"]
        self.initial = float(meta["initial"])
        self.minimum = float(meta["min"])
        self.maximum = float(meta["max"])
        self.lag_s = max(float(meta.get("lag_s", 60.0)), 1e-6)
        self.decimals = int(meta.get("decimals", 2))
        self.node = node
        self.period_s = period_s
        # What the process has actually reached, as opposed to what it was
        # told. These are equal until somebody writes.
        self.actual_offset = 0.0

    async def target_offset(self) -> float:
        """How far the commanded setpoint sits from the generated one."""
        try:
            commanded = float(await self.node.read_value())
        except Exception:                      # a node read should never stop the line
            return self.actual_offset
        # Nothing upstream enforces these bounds yet: the MES has no setpoint
        # write path (that is the recommendation queue in the OPC plan, with
        # the API and the agent each checking the manifest independently).
        # Until then this clamp is the only guard, and it means a value poked
        # in by hand cannot drive the simulation somewhere impossible.
        commanded = min(max(commanded, self.minimum), self.maximum)
        return commanded - self.initial

    async def step(self) -> float:
        """Advance one tick towards the commanded value; return the offset."""
        target = await self.target_offset()
        alpha = min(self.period_s / self.lag_s, 1.0)
        self.actual_offset += (target - self.actual_offset) * alpha
        return self.actual_offset


def load_table(directory: Path, name: str) -> list[dict[str, float | int]]:
    """Read one generated table, converting every column to a number.

    Values are typed by content rather than by a schema: the generator writes
    integers for counters and states and decimals for process values, and
    keeping that distinction is what makes State readable as an int downstream.
    """
    path = Path(directory) / f"{name}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"No replay data at {path}. Generate it first:\n"
            f"    fsmes sim-generate labs/kepsim/line.json"
        )
    rows: list[dict[str, float | int]] = []
    with path.open(encoding="ascii", newline="") as f:
        for raw in csv.DictReader(f):
            rows.append({k: (int(v) if v.lstrip("-").isdigit() else float(v)) for k, v in raw.items()})
    if not rows:
        raise ValueError(f"{path} has a header but no rows")
    return rows


def _is_inspection_tag(tag: str) -> bool:
    return tag in INSPECTION_TAGS or tag.startswith(INSPECTION_PREFIX)


class Flow:
    """What has been made and not yet taken, by material and by cell - the
    one thing the per-line simulation cannot know, kept here so a stack only
    ever contains pieces a marker judged good, a wrap a stack a stacker
    built, and a pallet the wraps a wrapper wrapped.

    Queues are FIFO, so genealogy follows the order things were made, and a
    station that finds its queue empty is starved: it emits nothing that
    tick and says so, which is the flow between lines the generator does
    not simulate showing up as the gap it is."""

    def __init__(self) -> None:
        self.pieces: dict[str, deque] = {}            # material -> good piece serials
        self.stacks: dict[int, deque] = {}            # cell -> stack serials
        self.wraps: dict[int, deque] = {}             # group -> wrap serials
        self.starved: dict[str, int] = {}             # station -> times it found nothing to take

    def take_pieces(self, materials: list[str]) -> list[str] | None:
        queues = [self.pieces.get(m) for m in materials]
        if any(q is None or not q for q in queues):
            return None
        return [q.popleft() for q in queues]

    def take(self, queues: dict, key, n: int) -> list[str] | None:
        q = queues.get(key)
        if q is None or len(q) < n:
            return None
        return [q.popleft() for _ in range(n)]


class Inspector:
    """A station's vision system: for every unit the counters say it made,
    one judged event on the station's inspection group, every tag of it
    stamped with the same source time so the agent can take them as one.

    Serials are the station's own, sequential from a start the plant chose;
    attributes are drawn around their nominal with the spec's sigma; a unit
    the line scrapped fails on one attribute, so the inspection agrees with
    the count that was already booked. Nothing here invents production: the
    number of events per tick is exactly the counter delta in the row."""

    def __init__(self, spec: MachineMap, inspection: dict, nodes: dict, flow: Flow, seed: int) -> None:
        self.spec = spec
        self.kind = inspection["kind"]
        self.prefix = inspection.get("prefix", spec.equipment)
        self.material = inspection.get("material", "")
        self.attributes = list(inspection.get("attributes", []))
        self.members = int(inspection.get("members", 0))
        self.takes = list(inspection.get("takes", []))
        self.cell = inspection.get("cell")
        self.group = inspection.get("group")
        self.plate = inspection.get("plate")
        self.nodes = nodes
        self.flow = flow
        self.rng = random.Random(seed)
        self.seq = 0
        self.serial_no = int(inspection.get("start", 1))
        self.plate_no = int(inspection.get("start", 1))
        self.last_good: int | None = None
        self.last_scrap: int | None = None
        self.emitted = 0
        self.failed = 0

    def _serial(self) -> str:
        n = self.serial_no
        self.serial_no += 1
        return f"{self.prefix}-{n:09d}"

    def _values(self, fail: bool) -> tuple[list[float], int]:
        values, mask = [], 0
        culprit = self.rng.randrange(len(self.attributes)) if (fail and self.attributes) else -1
        for i, a in enumerate(self.attributes):
            nominal, lo, hi = float(a["nominal"]), float(a["min"]), float(a["max"])
            sigma = float(a.get("sigma", (hi - lo) / 8))
            if i == culprit:
                excess = abs(self.rng.gauss(0, sigma)) + sigma
                v = hi + excess if self.rng.random() < 0.5 else lo - excess
            else:
                v = min(max(self.rng.gauss(nominal, sigma), lo), hi)
            v = round(v, 4)
            if not lo <= v <= hi:
                mask |= 1 << i
            values.append(v)
        return values, mask

    def deltas(self, row: dict) -> tuple[int, int]:
        good, scrap = int(row.get("GoodCount", 0)), int(row.get("ScrapCount", 0))
        d_good = 0 if self.last_good is None or good < self.last_good else good - self.last_good
        d_scrap = 0 if self.last_scrap is None or scrap < self.last_scrap else scrap - self.last_scrap
        self.last_good, self.last_scrap = good, scrap
        return d_good, d_scrap

    async def emit(self, server, row: dict, stamp: datetime) -> int:
        """Emit this tick's events; return how many."""
        d_good, d_scrap = self.deltas(row)
        count = 0
        for fail in ([False] * d_good + [True] * d_scrap):
            members: list[str] | None = []
            plate_serial = None
            if self.kind == "stack":
                members = self.flow.take_pieces(self.takes)
            elif self.kind == "wrap":
                members = self.flow.take(self.flow.stacks, self.cell, 1)
            elif self.kind == "pallet":
                members = self.flow.take(self.flow.wraps, self.group, self.members or 1)
            if members is None:
                self.flow.starved[self.spec.equipment] = self.flow.starved.get(self.spec.equipment, 0) + 1
                break
            serial = self._serial()
            if self.kind == "wrap" and self.plate:
                plate_serial = f"{self.plate['prefix']}-{self.plate_no:09d}"
                self.plate_no += 1
                members = [*members, plate_serial]
            values, mask = self._values(fail)
            self.seq += 1
            ts = stamp + timedelta(microseconds=count)
            await self._write(server, "InspSerial", ua.Variant(serial, ua.VariantType.String), ts)
            for a, v in zip(self.attributes, values, strict=True):
                await self._write(server, f"{INSPECTION_PREFIX}{a['name']}",
                                  ua.Variant(float(v), ua.VariantType.Double), ts)
            await self._write(server, "InspMembers", ua.Variant(",".join(members), ua.VariantType.String), ts)
            await self._write(server, "InspPass", ua.Variant(int(mask), ua.VariantType.Int32), ts)
            await self._write(server, "InspSeq", ua.Variant(int(self.seq), ua.VariantType.Int64), ts)
            count += 1
            self.emitted += 1
            if mask:
                self.failed += 1
            elif self.kind == "piece":
                self.flow.pieces.setdefault(self.material, deque()).append(serial)
            elif self.kind == "stack":
                self.flow.stacks.setdefault(self.cell, deque()).append(serial)
            elif self.kind == "wrap":
                self.flow.wraps.setdefault(self.group, deque()).append(serial)
        return count

    async def _write(self, server, tag: str, variant, ts: datetime) -> None:
        node = self.nodes.get(tag)
        if node is None:
            return
        await server.write_attribute_value(node.nodeid, ua.DataValue(variant, SourceTimestamp=ts, ServerTimestamp=ts))


class ReplayMachine:
    """One station: writes its row of the table to its tags, once per tick."""

    def __init__(self, spec: MachineMap, rows: list[dict], nodes: dict,
                 setpoints: dict | None = None, follows: dict | None = None):
        self.spec = spec
        self.rows = rows
        self.nodes = nodes  # tag name -> node
        # tag -> Setpoint, and pv tag -> the setpoint that drives it.
        self.setpoints = setpoints or {}
        self.follows = follows or {}
        commanded = set(setpoints or ())
        missing = [tag for tag in spec.tags
                   if tag not in rows[0] and tag not in commanded and not _is_inspection_tag(tag)]
        if missing:
            raise ValueError(
                f"{spec.equipment}: table {spec.object}.csv has no column(s) {missing}. "
                f"The tag map and the generated data disagree — columns present: {sorted(rows[0])}"
            )

    async def write_row(self, tick: int) -> None:
        row = self.rows[tick % len(self.rows)]

        # Advance each setpoint one tick first, so every reading it drives
        # sees the same offset within this tick.
        offsets = {name: await sp.step() for name, sp in self.setpoints.items()}

        for tag, node in self.nodes.items():
            if tag in self.setpoints or _is_inspection_tag(tag):
                continue          # commanded or event-driven, never replayed from a column
            value = row[tag]
            if tag in self.follows:
                sp = self.setpoints.get(self.follows[tag])
                if sp is not None:
                    value = round(float(value) + offsets[sp.name], sp.decimals)
            # Counters are Int64 to match the twin's own simulator, so the
            # agent's counter handling sees the same types on both paths.
            if tag in COUNTERS:
                await node.write_value(ua.Variant(int(value), ua.VariantType.Int64))
            elif tag in INTEGER_TAGS:
                await node.write_value(ua.Variant(int(value), ua.VariantType.Int32))
            else:
                await node.write_value(float(value))


async def build_server(settings: Settings, machines: list[MachineMap], directory: Path,
                       period_s: float = 1.0):
    """Stand up the server and its address space, without starting the clock."""
    server = Server()
    await server.init()
    server.set_endpoint(settings.opc_endpoint)
    server.set_server_name("MES-TWIN KepSim Replay")
    idx = await server.register_namespace(settings.opc_namespace)

    manifest = load_manifest(directory)
    replays: list[ReplayMachine] = []
    inspectors: list[Inspector] = []
    flow = Flow()
    for spec in machines:
        rows = load_table(directory, spec.object)
        obj = await server.nodes.objects.add_object(idx, spec.object)
        tag_meta = (manifest.get("tables", {}).get(spec.object, {}).get("tags", {}))
        nodes: dict = {}
        setpoints: dict = {}
        follows: dict = {}

        for tag in spec.tags:
            meta = tag_meta.get(tag, {})
            if _is_inspection_tag(tag):
                # An event tag: no column behind it, written per judged unit.
                if tag in ("InspSerial", "InspMembers"):
                    nodes[tag] = await obj.add_variable(idx, tag, ua.Variant("", ua.VariantType.String))
                elif tag == "InspSeq":
                    nodes[tag] = await obj.add_variable(idx, tag, ua.Variant(0, ua.VariantType.Int64))
                elif tag == "InspPass":
                    nodes[tag] = await obj.add_variable(idx, tag, ua.Variant(0, ua.VariantType.Int32))
                else:
                    nodes[tag] = await obj.add_variable(idx, tag, 0.0)
                continue
            if meta.get("kind") == "sp":
                # A commanded value: writable, and no column behind it.
                node = await obj.add_variable(idx, tag, float(meta["initial"]))
                await node.set_writable()
                nodes[tag] = node
                setpoints[tag] = Setpoint(tag, meta, node, period_s)
                continue
            if tag not in rows[0]:
                raise ValueError(
                    f"{spec.equipment}: {spec.object}.csv has no column {tag!r}, "
                    f"and {MANIFEST_NAME} does not describe it as a setpoint. "
                    f"Regenerate the line (`fsmes sim-generate <line.json>`) so "
                    f"the data and its manifest agree.")
            initial = rows[0][tag]
            if tag in COUNTERS:
                nodes[tag] = await obj.add_variable(
                    idx, tag, ua.Variant(int(initial), ua.VariantType.Int64))
            elif tag in INTEGER_TAGS:
                nodes[tag] = await obj.add_variable(
                    idx, tag, ua.Variant(int(initial), ua.VariantType.Int32))
            else:
                nodes[tag] = await obj.add_variable(idx, tag, float(initial))
            if meta.get("follows"):
                follows[tag] = meta["follows"]

        replays.append(ReplayMachine(spec, rows, nodes, setpoints, follows))
        inspection = manifest.get("tables", {}).get(spec.object, {}).get("inspection")
        if inspection:
            inspectors.append(Inspector(spec, inspection, nodes, flow, seed=len(inspectors) + 1))

    # The Line table is context, not a machine: order id, whether an order is
    # active, and the line's own good count. Exposed so it can be browsed and
    # trended even though no MES equipment maps to it.
    line_nodes = {}
    try:
        line_rows = load_table(directory, LINE_TABLE)
    except FileNotFoundError:
        line_rows = []
    if line_rows:
        line_obj = await server.nodes.objects.add_object(idx, LINE_TABLE)
        for tag, value in line_rows[0].items():
            if tag == "TSec":
                continue
            line_nodes[tag] = await line_obj.add_variable(idx, tag, ua.Variant(int(value), ua.VariantType.Int64))

    # Markers judge first, stackers second, wrappers third, palletizers last,
    # so within a tick a stack can take pieces marked in that same tick.
    order = {"piece": 0, "stack": 1, "wrap": 2, "pallet": 3}
    inspectors.sort(key=lambda i: order.get(i.kind, 9))
    return server, replays, line_rows, line_nodes, inspectors


async def _wait_for_tick(started: float, tick: int, period: float) -> float:
    """Hold until tick `tick` is due; return how far behind schedule it is.

    Absolute schedule, not a fixed sleep between ticks. `await
    asyncio.sleep(period)` sleeps a whole period *after* the writes, so every
    tick costs period + work and the error accumulates. Measured at 60x it
    put simulated second 2700 at 2907 - an 8% drift - which silently
    invalidates any comparison against a scripted timeline. Scheduling each
    tick against started + tick*period keeps the line's clock honest at any
    speed, because a tick that runs late does not push the next one.

    A tick that is already late still yields once. Writing a node is pure
    memory work, so a loop that is behind and never sleeps never gives the
    event loop a turn - and the OPC server's own tasks live on that loop.
    At 150x with 27 stations the replay fell behind, stopped publishing
    altogether, the client's subscriptions timed out, and the agent received
    nothing for the rest of the hour: "running slow" had quietly become
    "serving nobody".
    """
    delay = (started + tick * period) - asyncio.get_running_loop().time()
    if delay > 0:
        await asyncio.sleep(delay)
        return 0.0
    await asyncio.sleep(0)
    return -delay


async def run(settings: Settings, directory: Path | None = None, speed: float | None = None) -> None:
    directory = Path(directory or settings.replay_dir)
    speed = speed or settings.sim_speed
    machines = load_tag_map(settings.tag_map_file, load_manifest(directory))
    period = 1.0 / speed if speed > 0 else 1.0
    # The setpoint lag is in line seconds, so at 60x a 60-second time
    # constant must still take 60 *simulated* seconds, not 60 wall ones.
    server, replays, line_rows, line_nodes, inspectors = await build_server(
        settings, machines, directory, period_s=1.0)
    log.info(
        "replay online",
        endpoint=settings.opc_endpoint,
        source=str(directory),
        machines=[m.spec.equipment for m in replays],
        rows=len(replays[0].rows) if replays else 0,
        seconds_per_row=round(period, 4),
    )
    async with server:
        loop = asyncio.get_running_loop()
        # A plant that starts producing before the MES is watching makes ids
        # nobody recorded - a stack naming pieces no marker ever showed the
        # agent. A scored run holds the line still for the seconds the agent
        # needs to subscribe; a standing plant has no such gap and no hold.
        hold = float(os.environ.get("MES_REPLAY_HOLD_S") or 0)
        if hold > 0:
            await asyncio.sleep(hold)
        log.info("replay ticking", held_seconds=hold, seconds_per_row=round(period, 4))
        started = loop.time()
        tick = 0
        worst = 0.0
        last_said: float | None = None
        inspected = 0
        last_inspection_report = loop.time()
        while True:
            tick += 1
            behind = await _wait_for_tick(started, tick, period)
            if behind > period:
                # Say so rather than quietly running slow: a replay that
                # cannot keep up produces data whose timestamps mean something
                # different from what was asked for. Said again every few
                # seconds while it lasts, with the worst it has been, so the
                # scored run can report how far the line's clock drifted
                # rather than only that it did.
                worst = max(worst, behind)
                now = loop.time()
                if last_said is None or now - last_said >= BEHIND_REPORT_SECONDS:
                    last_said = now
                    log.warning(
                        "replay cannot sustain the requested speed",
                        requested_speed=round(1.0 / period, 2),
                        behind_seconds=round(behind, 2),
                        worst_seconds=round(worst, 2),
                        tick=tick,
                    )

            for machine in replays:
                await machine.write_row(tick)
            if inspectors:
                stamp = datetime.now(UTC).replace(tzinfo=None)
                events = 0
                for inspector in inspectors:
                    events += await inspector.emit(server, inspector_row(inspector, replays, tick), stamp)
                inspected += events
                now = loop.time()
                if now - last_inspection_report >= BEHIND_REPORT_SECONDS:
                    last_inspection_report = now
                    log.info("inspection groups emitted", events=inspected,
                             failed=sum(i.failed for i in inspectors),
                             starved={k: v for k, v in flow_of(inspectors).starved.items() if v})
            if line_rows:
                row = line_rows[tick % len(line_rows)]
                for tag, node in line_nodes.items():
                    await node.write_value(ua.Variant(int(row[tag]), ua.VariantType.Int64))


def inspector_row(inspector: Inspector, replays: list[ReplayMachine], tick: int) -> dict:
    """The row this tick wrote for the inspector's station."""
    for machine in replays:
        if machine.spec.equipment == inspector.spec.equipment:
            return machine.rows[tick % len(machine.rows)]
    return {}


def flow_of(inspectors: list[Inspector]) -> Flow:
    return inspectors[0].flow if inspectors else Flow()
