"""The plant in the middle: shape in, a typed plant out, pack files later.

This is deliberately a *model* and not a converter straight to `line.json`.
A shape file says things `line.json` has no column for - which area a station
sits in, what feeds what, how the OPC tags are organised above the machine -
and a plant that threw those away on the way in could never grow a second
routing style without being rewritten. So the shape becomes this object graph
first:

    Plant -> Area* -> Station* (each with its Signal* and its node path)
          -> Routing*  (ordered steps, per material)
          -> flow      (the ordered edges those routings imply)
          -> StopKind*, Disposition*, Order*, Shift*   (the vocabularies and the book)

and `pack.to_pack` writes `line.json`, `tag_map.json`, `floor.json` and
`masterdata/*.json` *out* of it. Adding a routing style means adding to the
shape and to this graph; it does not mean touching the generator.

Two rules this module keeps, because they are the house rules:

**Unknown is not zero.** A section the shape says nothing about becomes a
*stated assumption*, recorded on the plant as a note and printed by
`fsmes shape plant`. Nothing is quietly defaulted.

**Nothing is invented.** Every number here is arithmetic on a number the
shape carries. Where the plant needs something the shape cannot hold, it is
either asked for (the buffer) or listed in `dropped` with one line saying
why - never guessed and never filled in with a plausible-looking figure.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace

from .read import Shape

#: Line seconds the generated replay covers. One eight-hour shift, which is
#: what the lab packs replay and what makes a shift's worth of charts.
SHIFT_SECONDS = 28800

#: The default buffer between two stations, in pieces. This is the one number
#: the shape cannot hold and the one the person is asked for: no MES records
#: the conveyor between two machines, and the whole starving-and-blocking
#: behaviour of a line hangs off it.
BUFFER_DEFAULT = 20

#: The states the shape may report, mapped to what the generator can script.
#: `starved` and `blocked` are deliberately absent: they *emerge* from the
#: buffer the person was asked for, and scripting them as well would be the
#: same loss counted twice.
SCRIPTABLE = ("down", "stopped", "changeover")


def code_of(name: str) -> str:
    """`ST-01` -> `ST_01`: a generic name an OPC browse path can carry.

    The skill names everything generically already (`ST-01`, `AN-02`,
    `AR-01`), which is the point of the shape file - no value of the
    customer's travels. All this does is swap the dash for an underscore,
    because an OPC browse name and an equipment code are both happier
    without it.
    """
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip())
    return cleaned.strip("_").upper() or "X"


@dataclass(frozen=True)
class Signal:
    """One tag on one machine, as the shape measured it."""

    name: str
    kind: str                       # analog | counter | discrete
    unit: str
    base: float
    noise: float
    decimals: int
    running_only: bool
    sample_interval_s: float


@dataclass(frozen=True)
class Station:
    """One machine, where it sits, how fast it goes, and what it publishes."""

    name: str                       # ST_01
    sequence: int
    area: str                       # the Area code it belongs to
    cell: str | None                # a work centre inside the area, if the shape said one
    rate_per_min: float
    scrap_pct: float
    signals: tuple[Signal, ...]
    #: The OPC folders above this machine's own object, outermost first. Empty
    #: is "directly under Objects", which is what every pack written before
    #: grouped nodes existed says and what the lab packs still say.
    node: tuple[str, ...] = ()

    @property
    def analog(self) -> Signal | None:
        """The signal a reader would call this machine's process value.

        Counters and discretes are never it: a chart of a rising counter says
        nothing about the process. Among the analogs, one that holds a level
        whether or not the machine is running - a temperature, a pressure, a
        weight - is preferred over one that collapses to zero when it stops,
        because the second kind is almost always a cycle time or a speed and a
        specification band around a cycle time is a specification about the
        machine rather than about the product. A unit breaks the remaining
        tie: a tag whose unit the source system never recorded is a tag
        nobody can put limits on.
        """
        analogs = [s for s in self.signals if s.kind == "analog"]
        if not analogs:
            return None
        def rank(signal: Signal) -> tuple[int, int, int]:
            return (0 if not signal.running_only else 1,
                    0 if signal.unit else 1,
                    self.signals.index(signal))
        return sorted(analogs, key=rank)[0]

    @property
    def cycle_seconds(self) -> float:
        return round(60.0 / self.rate_per_min, 3) if self.rate_per_min > 0 else 0.0


@dataclass(frozen=True)
class Area:
    code: str
    name: str
    parent: str | None = None
    #: work centres inside it, if the shape named any
    cells: tuple[str, ...] = ()


@dataclass(frozen=True)
class Routing:
    code: str
    name: str
    material: str
    steps: tuple[str, ...]          # station names, in order


@dataclass(frozen=True)
class TagNode:
    """One folder in the OPC address space, and the machines under it.

    A Kepware or Ignition plant does not publish one node per machine: it
    publishes a channel per PLC and a device under it, with every machine on
    that PLC inside. This is that, said as data.
    """

    name: str
    path: tuple[str, ...]           # browse names, outermost first
    stations: tuple[str, ...]
    tags_total: int = 0


@dataclass(frozen=True)
class StopKind:
    """One reason category an operator may pick, with how often it was picked."""

    code: str
    name: str
    description: str
    stops: int
    share_of_stops: float


@dataclass(frozen=True)
class Disposition:
    """What the source MES did with a finding, and how often."""

    code: str
    name: str
    share: float


@dataclass(frozen=True)
class Order:
    code: str
    material: str
    quantity: int
    priority: int
    release: bool
    due_in_hours: float


@dataclass(frozen=True)
class Shift:
    code: str
    name: str
    starts: str
    ends: str


@dataclass(frozen=True)
class StateShare:
    state: str
    share: float
    p50_seconds: float
    max_seconds: float


@dataclass(frozen=True)
class Plant:
    """A plant, shaped like somebody else's, with nothing of theirs in it."""

    name: str                       # acme
    label: str
    timezone: str
    enterprise: str
    site: str
    areas: tuple[Area, ...]
    stations: tuple[Station, ...]
    #: ordered edges: (upstream station, downstream station)
    flow: tuple[tuple[str, str], ...]
    routings: tuple[Routing, ...]
    nodes: tuple[TagNode, ...]
    stops: tuple[StopKind, ...]
    states: tuple[StateShare, ...]
    dispositions: tuple[Disposition, ...]
    orders: tuple[Order, ...]
    shifts: tuple[Shift, ...]
    buffer: int
    seed: int
    duration_s: int
    #: the share of stops the source MES had no reason recorded against
    unlabelled_share: float
    #: how wide a specification band is, in sigma, and why that many
    spec_sigma: float
    #: ("[section] field", "one line saying why the plant does not use it")
    dropped: tuple[tuple[str, str], ...] = ()
    #: things the shape could not say that the plant had to state
    notes: tuple[str, ...] = ()
    #: provenance, straight off the shape's own [meta]
    source: dict = field(default_factory=dict)

    @property
    def shape_format(self) -> int:
        return int(self.source.get("shape_format", 1))

    def station(self, name: str) -> Station | None:
        return next((s for s in self.stations if s.name == name), None)

    def chain(self) -> tuple[str, ...]:
        """The stations in the order the simulated line will work them.

        The model holds a graph; `line.json` holds a chain, because the
        generator's physics is a serial line with a buffer between each pair.
        So the graph is walked in topological order and that order is what is
        simulated - stated here and written into the generated `plant.toml`,
        because on a plant with two cells feeding one station it matters: the
        two cells are simulated one after the other rather than side by side,
        while the *routings* the MES reads keep the real shape.
        """
        waiting = {s.name: 0 for s in self.stations}
        after: dict[str, list[str]] = {s.name: [] for s in self.stations}
        for up, down in self.flow:
            if up in waiting and down in waiting:
                after[up].append(down)
                waiting[down] += 1
        order_of = {s.name: (s.sequence, s.name) for s in self.stations}
        ready = sorted([n for n, count in waiting.items() if count == 0], key=lambda n: order_of[n])
        out: list[str] = []
        while ready:
            name = ready.pop(0)
            out.append(name)
            for nxt in after[name]:
                waiting[nxt] -= 1
                if waiting[nxt] == 0:
                    ready.append(nxt)
            ready.sort(key=lambda n: order_of[n])
        if len(out) != len(self.stations):
            # A cycle. Rework loops exist on real plants and a serial line
            # cannot hold one, so the sequence order is used and said so.
            return tuple(s.name for s in sorted(self.stations, key=lambda s: (s.sequence, s.name)))
        return tuple(out)

    @property
    def is_one_straight_line(self) -> bool:
        chain = self.chain()
        wanted = {(chain[i], chain[i + 1]) for i in range(len(chain) - 1)}
        return set(self.flow) == wanted


# --------------------------------------------------------------- conversion


def _spec_sigma(fail_share: float) -> float:
    """How many sigma wide a specification band is, from the observed fail share.

    The shape says what share of recorded checks failed. A specification band
    is then the band that would fail that often on a normal process, which is
    the one honest way to set limits from a shape: the plant's own reject rate
    decides them, not a round number somebody liked. Solved by bisection on
    the error function, because this must not pull in a numerical library.
    """
    if not 0.0 < fail_share < 0.5:
        return 3.0
    low, high = 0.5, 6.0
    for _ in range(60):
        mid = (low + high) / 2.0
        outside = math.erfc(mid / math.sqrt(2.0))      # both tails
        if outside > fail_share:
            low = mid
        else:
            high = mid
    return round((low + high) / 2.0, 3)


def _signals(rows: list[dict]) -> tuple[Signal, ...]:
    out = []
    for row in rows:
        name = code_of(str(row.get("name") or ""))
        if not name:
            continue
        out.append(Signal(
            name=name,
            kind=str(row.get("kind") or "analog"),
            unit=str(row.get("unit") or ""),
            base=float(row.get("base") or 0.0),
            noise=float(row.get("noise") or 0.0),
            decimals=int(row.get("decimals") or 2),
            running_only=bool(row.get("running_only")),
            sample_interval_s=float(row.get("sample_interval_s") or 0.0),
        ))
    return tuple(out)


def _areas(shape: Shape, stations: list[dict]) -> tuple[tuple[Area, ...], dict[str, str]]:
    """The areas, and which area each station sits in.

    Format 1 has no hierarchy at all, so the plant is one area - which is the
    truth about a format-1 shape and is said as such, rather than pretending
    the source MES was asked and answered "one".
    """
    rows = shape.rows("hierarchy", "areas")
    if not shape.found("hierarchy") or not rows:
        area = Area(code="AR_01", name="Area 1")
        return (area,), {code_of(str(s.get("name") or "")): area.code for s in stations}

    areas: list[Area] = []
    for row in rows:
        code = code_of(str(row.get("name") or ""))
        if not code:
            continue
        # An empty parent means "straight under the site". `code_of("")` is
        # the placeholder "X", which would hang the area off nothing.
        named_parent = str(row.get("parent") or "").strip()
        parent = code_of(named_parent) if named_parent else None
        cells = tuple(code_of(str(c)) for c in (row.get("cells") or []) if str(c).strip())
        areas.append(Area(code=code, name=f"Area {len(areas) + 1}", parent=parent, cells=cells))
    if not areas:
        areas = [Area(code="AR_01", name="Area 1")]
    known = {a.code for a in areas}
    first = areas[0].code
    where: dict[str, str] = {}
    for row in stations:
        name = code_of(str(row.get("name") or ""))
        said = code_of(str(row.get("area") or ""))
        where[name] = said if said in known else first
    return tuple(areas), where


def _nodes(shape: Shape, stations: tuple[Station, ...]) -> tuple[TagNode, ...]:
    """How the tags are organised above the machines.

    Format 1, and a format-2 shape whose `[tag_nodes] found = false`, both
    mean *one node per machine* - which is what every pack written so far
    says and what a plant whose MES reads tags straight off a PLC looks
    like. A shape that measured the grouping says it, and then several
    machines sit under one node.
    """
    rows = shape.rows("tag_nodes", "nodes")
    if not shape.found("tag_nodes") or not rows:
        return tuple(
            TagNode(name=s.name, path=(), stations=(s.name,),
                    tags_total=len(s.signals))
            for s in stations
        )
    known = {s.name for s in stations}
    out: list[TagNode] = []
    for row in rows:
        name = code_of(str(row.get("name") or ""))
        path = tuple(code_of(str(p)) for p in (row.get("path") or [name]) if str(p).strip())
        under = tuple(code_of(str(s)) for s in (row.get("stations") or []))
        under = tuple(s for s in under if s in known)
        if not under:
            continue
        out.append(TagNode(name=name or path[-1], path=path, stations=under,
                           tags_total=int(row.get("tags") or 0)))
    placed = {s for node in out for s in node.stations}
    for station in stations:
        if station.name not in placed:
            out.append(TagNode(name=station.name, path=(), stations=(station.name,),
                               tags_total=len(station.signals)))
    return tuple(out)


def _routings(shape: Shape, chain: tuple[str, ...]) -> tuple[tuple[Routing, ...], tuple[str, ...]]:
    """The routings, and the notes needed to explain where they came from."""
    rows = shape.rows("routings", "routings")
    basis = str(shape.table("routings").get("basis") or "")
    if not shape.found("routings") or not rows:
        routing = Routing(code="RT_01", name="Routing 1", material="FG_01", steps=chain)
        note = ("The shape carries no routing table, so the plant has one routing: "
                "every station in sequence order. That is the shape's own station "
                "order, not a measured routing - the source MES was not asked, or "
                "had nothing to answer with.")
        return (routing,), (note,)
    out: list[Routing] = []
    known = set(chain)
    for row in rows:
        code = code_of(str(row.get("name") or ""))
        steps = tuple(code_of(str(s)) for s in (row.get("steps") or []))
        steps = tuple(s for s in steps if s in known)
        if not code or not steps:
            continue
        out.append(Routing(code=code, name=f"Routing {len(out) + 1}",
                           material=f"FG_{len(out) + 1:02d}", steps=steps))
    if not out:
        out = [Routing(code="RT_01", name="Routing 1", material="FG_01", steps=chain)]
    notes = []
    if basis:
        notes.append(f"The routings came from the source MES: {basis}.")
    missed = known - {s for r in out for s in r.steps}
    if missed:
        notes.append(
            f"{len(missed)} of {len(known)} stations appear on no routing "
            f"({', '.join(sorted(missed))}). They are in the plant and on the floor "
            "page, and no order runs through them."
        )
    return tuple(out), tuple(notes)


def _orders(shape: Shape, routings: tuple[Routing, ...], prefix: str) -> tuple[Order, ...]:
    """The order book, from what the shape says an order looks like here.

    Ten orders, which is what the lab packs carry and about a day and a half
    of line time: enough that the floor's planner has a pattern to continue
    and the book does not empty while somebody is looking at it. Sizes
    alternate between the shape's own median and its ninetieth percentile,
    because those are the two sizes it measured.
    """
    table = shape.table("orders")
    p50 = float(table.get("qty_p50") or 0.0)
    p90 = float(table.get("qty_p90") or p50)
    sizes = [round(p50), round(p90)]
    sizes = [s for s in sizes if s > 0] or [0]
    out: list[Order] = []
    for i in range(10):
        routing = routings[i % len(routings)]
        quantity = sizes[i % len(sizes)]
        out.append(Order(
            code=f"WO-{prefix}-{4711 + i}",
            material=routing.material,
            quantity=quantity,
            priority=10,
            release=i == 0,
            due_in_hours=2.0 + 3.0 * i,
        ))
    return tuple(out)


def _shifts(shape: Shape) -> tuple[tuple[Shift, ...], tuple[str, ...]]:
    rows = shape.rows("shifts", "pattern")
    if not shape.found("shifts") or not rows:
        note = ("The shape carries no shift pattern, so the plant works one "
                "twenty-four-hour shift. The source MES was not asked, or had no "
                "shift table: this is not a measured calendar.")
        return (Shift(code="DAY", name="Day shift", starts="00:00", ends="00:00"),), (note,)
    out: list[Shift] = []
    for i, row in enumerate(rows, start=1):
        starts = str(row.get("starts_at_utc") or "06:00")
        hours = float(row.get("hours") or 8.0)
        hour, _, minute = starts.partition(":")
        total = (int(hour) * 60 + int(minute or 0) + round(hours * 60)) % (24 * 60)
        ends = f"{total // 60:02d}:{total % 60:02d}"
        out.append(Shift(code=f"S{i}", name=f"Shift {i}", starts=starts, ends=ends))
    return tuple(out), ()


def _stops(shape: Shape) -> tuple[tuple[StopKind, ...], tuple[StateShare, ...], float]:
    reasons = shape.rows("stops", "reasons")
    total = sum(int(r.get("stops") or 0) for r in reasons) or 1
    kinds: list[StopKind] = []
    unlabelled = float(shape.table("stops").get("unlabelled_share") or 0.0)
    for row in reasons:
        category = str(row.get("category") or "").strip()
        if not category or category == "unlabelled":
            continue
        kinds.append(StopKind(
            code=re.sub(r"[^a-z0-9_]+", "_", category.lower()).strip("_") or "other",
            name=category.replace("_", " ").title(),
            description=str(row.get("generic_wording") or "").strip()
            or f"A stop the source system filed as {category}.",
            stops=int(row.get("stops") or 0),
            share_of_stops=float(row.get("share_of_stops") or 0.0),
        ))
    states = tuple(
        StateShare(
            state=str(row.get("state") or ""),
            share=float(row.get("share") or 0.0),
            p50_seconds=float(row.get("p50_seconds") or 0.0),
            max_seconds=float(row.get("max_seconds") or 0.0),
        )
        for row in shape.rows("stops", "states")
        if row.get("state")
    )
    if not kinds:
        kinds = [StopKind(code="unexplained", name="Unexplained",
                          description="The machine stopped and nobody has said why. The "
                                      "shape carried no reason categories, so this plant "
                                      "has the one word that is always true.",
                          stops=total, share_of_stops=1.0)]
    return tuple(kinds), states, unlabelled


def _dispositions(shape: Shape) -> tuple[Disposition, ...]:
    out: list[Disposition] = []
    for row in shape.rows("quality", "dispositions"):
        word = str(row.get("disposition") or "").strip()
        if not word or word.startswith("none"):
            continue
        out.append(Disposition(
            code=re.sub(r"[^a-z0-9_]+", "_", word.lower()).strip("_") or "other",
            name=word.replace("_", " ").title(),
            share=float(row.get("share") or 0.0),
        ))
    return tuple(out)


#: Fields the shape carries that the generated plant does not use, and the one
#: line each gets in the plant's own `[from_shape].dropped`. Checked against
#: the shape so a plant only lists what its own shape actually held.
DROPPABLE: tuple[tuple[str, str, str], ...] = (
    ("stations.analogs[kind=counter]", "counter",
     "Counter tags. Every station already publishes its own Good, Scrap and "
     "Counter columns, generated from its rate; a second counter whose only "
     "description is a base and a noise would be a number with no arithmetic "
     "behind it."),
    ("stations.analogs[kind=discrete]", "discrete",
     "Discrete (on/off) tags. The generated line publishes a State word and an "
     "alarm word, and a boolean with a mean of 0.9 is not a signal anything on "
     "the dashboard can read."),
    ("stations.analogs.state_correlation", "state_correlation",
     "How closely each tag tracked the running state. Used only to decide "
     "`running_only`, which the shape already states outright, so the "
     "coefficient itself is not carried into the plant."),
    ("stations.analogs.observed_min / observed_max", "observed_min",
     "The range each tag was seen in. The generated tag is built from its base "
     "and its noise; clamping it to somebody else's observed range would make "
     "the simulated spread narrower than the one measured."),
    ("stations.analogs.modal_state / modal_state_share", "modal_state",
     "Which state each tag was mostly sampled in. That is a property of when "
     "the sampler ran, not of the plant."),
    ("plant.naming_grammar", "naming_grammar",
     "The patterns the source system's order, lot and document numbers follow. "
     "Deliberately not used: the generated plant names its own orders and lots "
     "in the product's own generic form, so nothing shaped like a customer's "
     "numbering appears on a screen anybody demonstrates."),
    ("documents", "documents",
     "How many controlled documents there are and what their numbers look "
     "like. A pack has no document register to put them in."),
    ("erp", "erp",
     "The ERP touchpoints and their call rates. The generated plant runs with "
     "`[erp] mode = \"off\"`: an ERP it cannot reach is not an ERP, and a "
     "simulated one would be two simulators arguing."),
    ("also_tracked", "also_tracked",
     "The tables the skill could not map. Each is a candidate feature for this "
     "product, which is why the skill carries them; none of them is a thing a "
     "plant can be built out of."),
    ("stops.waiting_intervals / waiting_seconds", "waiting_intervals",
     "How long machines spent starved or blocked. Not scripted: starving and "
     "blocking emerge from the buffer size you were asked for, and scripting "
     "them as well would count the same loss twice."),
    ("orders.open_hours_p50 / open_hours_p90 / still_open", "open_hours_p50",
     "How long an order stayed open. The generated book's due dates are spaced "
     "by the line's own rate instead, because an order that is due before the "
     "line can make it is a planning fault rather than a shape."),
    ("quality.nonconformances_per_1000_checks", "nonconformances_per_1000_checks",
     "The non-conformance rate. The plant raises findings from its own control "
     "rules on its own readings, so a rate imposed on top would overwrite what "
     "the charts actually find."),
)


def _dropped(shape: Shape) -> tuple[tuple[str, str], ...]:
    """Which of the droppable fields this particular shape actually carried."""
    text = _flat_keys(shape.raw)
    out = []
    for label, probe, why in DROPPABLE:
        if probe in text:
            out.append((label, why))
    return tuple(out)


def _flat_keys(value, into: set[str] | None = None) -> set[str]:
    """Every key name and every string value anywhere in the shape.

    Used only to answer "did this shape carry that field at all", so a plant
    built from a shape with no `[erp]` section does not claim to have dropped
    one.
    """
    into = set() if into is None else into
    if isinstance(value, dict):
        for key, item in value.items():
            into.add(str(key))
            _flat_keys(item, into)
    elif isinstance(value, list):
        for item in value:
            _flat_keys(item, into)
    elif isinstance(value, str):
        into.add(value)
    return into


def from_shape(
    shape: Shape,
    *,
    name: str,
    buffer: int = BUFFER_DEFAULT,
    label: str | None = None,
    timezone: str = "UTC",
    seed: int = 7,
    duration_s: int = SHIFT_SECONDS,
) -> Plant:
    """One shape file -> one plant. The only entry point into the model."""
    rows = [row for row in shape.rows("stations") if row.get("name")]
    rows.sort(key=lambda r: (int(r.get("sequence") or 0), str(r.get("name"))))
    areas, where = _areas(shape, rows)

    stations: list[Station] = []
    for i, row in enumerate(rows, start=1):
        station_name = code_of(str(row["name"]))
        stations.append(Station(
            name=station_name,
            sequence=int(row.get("sequence") or i),
            area=where.get(station_name, areas[0].code),
            cell=code_of(str(row.get("cell") or "")) or None,
            rate_per_min=float(row.get("rate_per_min") or 0.0),
            scrap_pct=float(row.get("scrap_pct") or 0.0),
            signals=_signals([a for a in (row.get("analogs") or []) if isinstance(a, dict)]),
        ))

    chain = tuple(s.name for s in stations)
    routings, routing_notes = _routings(shape, chain)
    flow: list[tuple[str, str]] = []
    for routing in routings:
        for a, b in zip(routing.steps, routing.steps[1:], strict=False):
            if (a, b) not in flow:
                flow.append((a, b))

    nodes = _nodes(shape, tuple(stations))
    under = {s: node.path for node in nodes for s in node.stations}
    stations = [replace(s, node=under.get(s.name, ())) for s in stations]

    stops, states, unlabelled = _stops(shape)
    shifts, shift_notes = _shifts(shape)
    prefix = re.sub(r"[^A-Z0-9]+", "", name.upper())[:6] or "PLANT"
    orders = _orders(shape, routings, prefix)
    quality = shape.table("quality")
    sigma = _spec_sigma(float(quality.get("fail_share") or 0.0))

    notes = list(routing_notes) + list(shift_notes)
    notes.append(
        f"The buffer between each pair of machines is {buffer} pieces, half full at "
        "the start. No MES records the conveyor between two machines, so this is the "
        "one number that was asked for rather than measured."
    )
    if float(quality.get("fail_share") or 0.0) > 0:
        notes.append(
            f"Specification bands are {sigma} sigma wide, which is the band that "
            f"would fail as often as the shape says checks failed here "
            f"({quality.get('fail_share')})."
        )
    else:
        notes.append(
            "The shape reports no failed checks, so specification bands are the "
            "conventional three sigma. That is a convention, not a measurement."
        )
    notes.append(
        "No fault, drift or scrap burst is scripted. The shape describes how the "
        "plant behaves, not a story to tell about it, so the charts show this "
        "plant's own noise and its own stop mix and nothing planted."
    )

    plant = Plant(
        name=name,
        label=label or f"{name} (from a shape file)",
        timezone=timezone,
        enterprise=prefix,
        site=f"{prefix}1",
        areas=areas,
        stations=tuple(stations),
        flow=tuple(flow),
        routings=routings,
        nodes=nodes,
        stops=stops,
        states=states,
        dispositions=_dispositions(shape),
        orders=orders,
        shifts=shifts,
        buffer=int(buffer),
        seed=seed,
        duration_s=int(duration_s),
        unlabelled_share=unlabelled,
        spec_sigma=sigma,
        dropped=_dropped(shape),
        notes=tuple(notes),
        source={**shape.table("meta"), "shape_format": shape.format,
                "shape_file": shape.path.name},
    )
    if not plant.is_one_straight_line:
        plant = replace(plant, notes=(
            *plant.notes,
            "This plant is not one straight line. The routings the MES reads keep "
            f"its real shape ({len(plant.routings)} routings over "
            f"{len(plant.stations)} stations); the simulated flow underneath walks "
            "them in one topological order, so two cells that feed the same station "
            "are simulated one after the other rather than side by side.",
        ))
    return plant
