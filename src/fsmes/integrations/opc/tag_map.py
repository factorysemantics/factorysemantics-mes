"""The tag map is the wiring diagram between MES equipment and OPC UA objects.

Both the agent (client) and the simulator (server) read the same file, which
is what guarantees the twin exposes exactly the address space the MES expects.
Pointing the agent at real PLCs means editing this file, nothing else.

A machine entry carries everything that differs between sources, because real
servers disagree about all of it:

    object          browse name under Objects/ (the twin's own simulator)
    name            display name for humans; defaults to the equipment code
    node_id         a printf-style node-id template, e.g.
                    "ns=2;s=SimLine.LD.{tag}" — used INSTEAD of browsing when
                    present. Kepware and most vendor servers address tags by
                    flat string id, not by a browsable object tree.
    nodes           per-tag node ids, e.g. {"State": "ns=2;s=Plant.LD.MachStatus"}.
                    The template above assumes a machine's tags share one naming
                    pattern; a real plant's tags were named by whoever wrote the
                    PLC program, so GoodCount might live at ...PartsOK while
                    State lives at ...MachStatus. `nodes` says exactly where
                    each one is, and — equally important — which ones EXIST:
                    a machine with no scrap counter simply has no ScrapCount
                    entry, and the agent subscribes to what is listed rather
                    than failing on what is not.
    analog          the name of this machine's primary process value. The
                    twin's own simulator calls it Temperature; a real line
                    calls it MotorTemp, WashTemp, FillWeight...
    extra_tags      every other tag on the machine: further process values,
                    setpoints, TotalCount, AlarmWord. Usually left out and
                    filled in from the line's generated manifest (see
                    `load_tag_map`); set it explicitly to subscribe to a
                    hand-picked subset, which is what a real server needs.
    state_map       raw tag value -> MES equipment state. Absent means the tag
                    already carries the MES state name. A PLC almost never does:
                    it sends an integer, and only the site knows that 3 means
                    "blocked" and that blocked is idle time, not downtime.
    order_tag       the writable tag the MES pushes the order code down to, or
                    null for a read-only source (a CSV replay, a historian, a
                    server the MES has no write rights on). Null means the
                    machine is observed but never commanded.

Only `equipment` and one of `object`/`node_id`/`nodes` are required; the rest
default to the twin's own simulator conventions, so existing maps keep working.

Beside `machines` a map may carry one `line` block, which is about the line
rather than any machine on it:

    "line": {
      "object": "Line",                  the object the line's own tags sit on
      "publishes_order": "OrderId",      the tag carrying the order it is running
      "order_code": "WO-ACME-{value}"    how that value names an order here
    }

`publishes_order` is **read**, and that is the whole difference between it and
a machine's `order_tag`, which is written. A machine's `order_tag` is the MES
telling the line which order to stamp its counts with; `publishes_order` is the
line telling the MES which order it is already running - a historian, a
line-control PLC or a CSV replay that nobody may write to can still answer that
question, and until this block existed nothing in the MES could ask it.

`order_code` exists because the two sides name the same order differently and
always will: a PLC publishes `4711` in an integer register and the MES holds
`WO-ACME-4711`. Which is a plant-boundary translation, so it is config and not
code - `{value}` is what the line published, and a plant whose line publishes
the code outright writes `"{value}"`.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from fsmes.domain import EquipmentStateName
from fsmes.kernel.tags import MANIFEST_NAME

DEFAULT_ANALOG = "Temperature"
DEFAULT_ORDER_TAG = "OrderCode"


@dataclass(frozen=True)
class MachineMap:
    equipment: str  # MES equipment code (a work unit)
    object: str  # browse name of the OPC UA object under Objects/
    cycle_seconds: float  # rated cycle time, used by the simulator
    name: str = ""  # display name; empty means "use the code"
    analog: str = DEFAULT_ANALOG  # name of the primary process value
    # Everything else this machine exposes: further process values, its
    # setpoints, its counters, its alarm word. `analog` stays the primary
    # one because the OEE and trend paths speak of "the" process value; a
    # real machine has a dozen, and an MES that subscribes to one of them
    # is not integrating machine data, it is sampling it.
    extra_tags: tuple[str, ...] = ()
    node_id: str | None = None  # "ns=2;s=Chan.Dev.{tag}" — bypasses browsing
    nodes: dict[str, str] = field(default_factory=dict)  # tag -> full node id
    order_tag: str | None = DEFAULT_ORDER_TAG  # None = read-only source
    state_map: dict[str, EquipmentStateName] = field(default_factory=dict)

    @property
    def tags(self) -> tuple[str, ...]:
        """Every tag the agent subscribes to for this machine.

        With per-tag `nodes` (and no template to fall back on), the listing IS
        the truth: a machine whose worksheet had no scrap counter has no
        ScrapCount node, and subscribing to a tag that does not exist would
        turn one missing counter into a machine that reports nothing at all.
        """
        canonical = ("State", "GoodCount", "ScrapCount", self.analog, *self.extra_tags)
        # Dedupe while keeping order: a map that lists its primary analog
        # again under extras should not subscribe to it twice.
        seen: dict[str, None] = {}
        for tag in canonical:
            seen.setdefault(tag, None)
        ordered = tuple(seen)
        if self.nodes and not self.node_id:
            return tuple(tag for tag in ordered if tag in self.nodes)
        return ordered

    @property
    def by_node_id(self) -> bool:
        """True when tags are addressed directly instead of browsed."""
        return bool(self.nodes or self.node_id)

    def node(self, tag: str) -> str | None:
        """The flat node id for a tag: an explicit `nodes` entry wins, the
        `node_id` template fills in the rest, None means browse for it."""
        if tag in self.nodes:
            return self.nodes[tag]
        return self.node_id.format(tag=tag) if self.node_id else None

    def to_state(self, value) -> EquipmentStateName:
        """Translate a raw State tag value into an MES equipment state.

        Raises ValueError on an unmapped value rather than guessing: an
        unrecognised state code means the map is wrong, and silently calling it
        'idle' would quietly corrupt every availability figure downstream.
        """
        if not self.state_map:
            return EquipmentStateName(str(value).strip().lower())
        key = str(int(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)
        try:
            return self.state_map[key]
        except KeyError:
            raise ValueError(
                f"{self.equipment}: State={value!r} is not in the tag map's state_map "
                f"(known: {sorted(self.state_map)})"
            ) from None


def load_manifest(directory: Path | None) -> dict:
    """Every tag a generated line publishes, and what each one is.

    Written by `fsmes.sim.generate` beside the CSVs. Absent for a line
    generated before manifests existed, or for a real server that no
    generator produced - both of which simply mean "no manifest", not an
    error.
    """
    if directory is None:
        return {"tables": {}}
    path = Path(directory) / MANIFEST_NAME
    if not path.is_file():
        return {"tables": {}}
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class LineMap:
    """Where the line itself publishes what it is doing, and what it means.

    A line is not a machine and the MES holds no equipment row for one, so this
    is deliberately not another `MachineMap`: nothing subscribes to it as a
    machine, nothing books against it, and it carries no state map. It answers
    one question - *which order does the line say it is running?* - and it is
    the only place that question has an answer that was not inferred.
    """

    object: str
    publishes_order: str | None = None
    #: A format with one field, `{value}`: what the line published becomes the
    #: MES's own code for the same order. Empty means the map does not say, and
    #: a caller must treat the two numbering schemes as unmatched rather than
    #: guessing that they are the same.
    order_code: str = ""

    def code_for(self, value) -> str | None:
        """The MES's code for the order the line published, or None.

        None rather than a guess: a map that does not say how the line's value
        names an order here has not told us, and an order code invented from a
        raw register value would be a join nobody could check.
        """
        if value is None or not self.order_code:
            return None
        text = str(value).strip()
        if not text:
            return None
        return self.order_code.format(value=text)


def load_line_map(path: Path) -> LineMap | None:
    """The map's `line` block, checked, or None when it has none.

    Refused rather than half-read: a block naming a tag but no `order_code`
    would silently produce a run in which nothing is tied and nothing says why,
    which is exactly the failure this block exists to end.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return read_line_map(data)


def read_line_map(data: dict) -> LineMap | None:
    """`load_line_map`, for a map already in memory."""
    block = data.get("line")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ValueError("tag map: `line` is a table - the object the line's own tags sit on, "
                         "the tag carrying the order it is running, and how that value names "
                         "an order in this MES.")
    obj = block.get("object")
    if not obj or not isinstance(obj, str):
        raise ValueError("tag map: `line` names no `object`, so there is nothing to read "
                         "the line's own tags from.")
    publishes = block.get("publishes_order")
    if publishes is not None and (not isinstance(publishes, str) or not publishes.strip()):
        raise ValueError("tag map: `line.publishes_order` is the name of the tag the line "
                         "publishes its current order on, as text.")
    order_code = block.get("order_code") or ""
    if publishes and not order_code:
        raise ValueError(
            f"tag map: `line.publishes_order` is {publishes!r} and `line.order_code` is missing. "
            f"The line publishes a value and this MES holds a code; without the rule that turns "
            f"one into the other the tag is read and nothing can be done with it. Write "
            f"`\"order_code\": \"{{value}}\"` if the line publishes the code outright.")
    if order_code and "{value}" not in str(order_code):
        raise ValueError(
            f"tag map: `line.order_code` is {order_code!r}, which has no `{{value}}` in it. It is "
            f"a format for what the line published, not a constant - a plant whose every order "
            f"has one code has one order.")
    return LineMap(object=str(obj), publishes_order=publishes, order_code=str(order_code))


def load_tag_map(path: Path, manifest: dict | None = None) -> list[MachineMap]:
    """The wiring diagram, optionally completed from the line's manifest.

    A machine's extra tags used to be transcribed into this file by hand from
    the generated `tags.json`. That is a duplicate of something a program
    already knows, and it drifted within a day of existing: two of the four
    maps in this repository were updated and two were not, leaving maps that
    claim to be identical differing by nine tags a machine. So when a
    manifest is available, a browsable machine subscribes to everything its
    line publishes, and `extra_tags` becomes an override rather than a
    transcription.

    **Template-addressed machines are deliberately excluded.** A Kepware-style
    map reaches tags by formatting `node_id`, and `tags` cannot filter those
    against anything (there is no `nodes` listing to check). Subscribing to a
    node that does not exist fails the *whole* subscription on most servers,
    not the one tag - so a real server only ever gets what its map states
    outright.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tables = (manifest or {}).get("tables", {})
    machines = []
    for m in data["machines"]:
        if not m.get("object") and not m.get("node_id") and not m.get("nodes"):
            raise ValueError(f"tag map entry {m.get('equipment')!r} needs 'object', 'node_id', or 'nodes'")
        spec = MachineMap(
            equipment=m["equipment"],
            object=m.get("object") or m["equipment"],
            cycle_seconds=float(m.get("cycle_seconds", 5.0)),
            name=m.get("name", ""),
            analog=m.get("analog", DEFAULT_ANALOG),
            extra_tags=_extra_tags(m, tables),
            node_id=m.get("node_id"),
            nodes=dict(m.get("nodes") or {}),
            # `order_tag` absent = the default writable tag; explicit null = read-only.
            order_tag=m.get("order_tag", DEFAULT_ORDER_TAG),
            state_map={str(k): EquipmentStateName(v) for k, v in (m.get("state_map") or {}).items()},
        )
        if not spec.tags:
            raise ValueError(
                f"tag map entry {spec.equipment!r} subscribes to nothing — its 'nodes' has none of "
                f"State/GoodCount/ScrapCount/{spec.analog}"
            )
        machines.append(spec)
    return machines


def _extra_tags(entry: dict, tables: dict) -> tuple[str, ...]:
    """What else this machine subscribes to, beyond the canonical four."""
    if entry.get("extra_tags") is not None:
        return tuple(entry["extra_tags"])                 # an explicit override
    if entry.get("node_id") or entry.get("nodes"):
        return ()                                         # see load_tag_map
    published = tables.get(entry.get("object") or entry.get("equipment"), {})
    canonical = {"State", "GoodCount", "ScrapCount",
                 entry.get("analog", DEFAULT_ANALOG), "TSec"}
    return tuple(tag for tag in published.get("tags", ()) if tag not in canonical)
