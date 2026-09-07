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
