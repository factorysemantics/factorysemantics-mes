"""The tag map is the wiring diagram between MES equipment and OPC UA objects.

Both the agent (client) and the simulator (server) read the same file, which
is what guarantees the twin exposes exactly the address space the MES expects.
Pointing the agent at real PLCs means editing this file, nothing else.

A machine entry carries everything that differs between sources, because real
servers disagree about all of it:

    object          browse name of the machine's own node (the twin's own
                    simulator puts it straight under Objects/)
    group           the folders the object sits in, outermost first, e.g.
                    ["Area1", "PLC_02"]. Absent or empty means "straight
                    under Objects/", which is one node per machine and is
                    what every map written before this field said. A
                    Kepware or Ignition server almost never looks like
                    that: its tags are organised by channel, device and
                    folder, and several machines share one device node.
                    The group is a browse path only — the manifest, the
                    replay CSVs and the generated tables stay keyed by
                    `object`, because the folders a server happens to use
                    say nothing about which machine a row belongs to.
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
                    The key "*", if present, is the state for every value the
                    map does not list - {"0": "running", "*": "down"} for a
                    downtime code read as the state. Without it an unlisted
                    value is refused, never guessed.
    state_tag       the tag the state is read from; absent means State. A
                    machine whose PLC keeps no state word, only a downtime
                    code that is zero while it may run, names that tag here
                    (and usually as its `reason_tag` too, so the one value
                    both stops the machine and says why). What such a tag
                    cannot say - starved, blocked, idle - it does not say.
    order_tag       the writable tag the MES pushes the order code down to, or
                    null for a read-only source (a CSV replay, a historian, a
                    server the MES has no write rights on). Null means the
                    machine is observed but never commanded.
    counts          false for equipment that makes nothing it could count: a
                    welding robot inside a cell, whose cell counts the bodies.
                    It subscribes to no GoodCount or ScrapCount, and books no
                    production. Absent means true, which is every machine.
    reason_tag      the tag on which the machine says WHY it stopped - a PLC
                    fault number, held while it is down. When State maps to
                    down, the open interval is named from it; when it arrives
                    after the State change, the stop it explains is named
                    then. Absent means the machine does not say, and its stops
                    are named the way every plant's are, by somebody here.
    reason_map      reason_tag value -> the plant's downtime reason code, e.g.
                    {"101": "weld_gun_fault"}. A value the map does not know
                    is still written on the stop as what the machine said
                    ("FaultCode 999") with no code: unknown is not a guess.
    counters        the tags the machine counts on, when it is not one good
                    and one scrap counter: {"good": ["Good_A", "Good_B"],
                    "scrap": ["Scrap_A", "Scrap_B"]}. A press with two tools
                    keeps a counter per tool and no total; a machine with
                    several outfeeds can keep one per outfeed. The machine's good is the sum of what its
                    good tags counted, its scrap the sum of its scrap tags,
                    each tag's own increase taken from its own last value -
                    so one tag resetting or arriving late moves nothing else.
                    Each tag is also recorded under its own name, which is
                    where the per-side split lives. Absent means GoodCount
                    and ScrapCount, which is every machine before this field.
                    A role may be an empty list (a machine counting no scrap)
                    but not both.

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
from fsmes.kernel.tags import MANIFEST_NAME, SEMANTIC_TAGS

DEFAULT_ANALOG = "Temperature"
DEFAULT_ORDER_TAG = "OrderCode"


@dataclass(frozen=True)
class MachineMap:
    equipment: str  # MES equipment code (a work unit)
    object: str  # browse name of the OPC UA object holding this machine's tags
    cycle_seconds: float  # rated cycle time, used by the simulator
    name: str = ""  # display name; empty means "use the code"
    # The folders `object` sits in, outermost first. Empty = under Objects/.
    group: tuple[str, ...] = ()
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
    counts: bool = True  # False: no counters, no production booked
    reason_tag: str | None = None  # the tag naming why it stopped
    reason_map: dict[str, str] = field(default_factory=dict)  # raw -> reason code
    # role ("good" / "scrap") -> the tags summed into it. Empty = GoodCount / ScrapCount.
    counters: dict[str, tuple[str, ...]] = field(default_factory=dict)
    state_tag: str = "State"  # the tag the machine's state is read from

    @property
    def counter_tags(self) -> dict[str, str]:
        """Every tag this machine counts on -> what it counts, `good` or `scrap`.

        Empty for a machine that counts nothing (`counts: false`)."""
        if not self.counts:
            return {}
        if not self.counters:
            return {"GoodCount": "good", "ScrapCount": "scrap"}
        return {tag: role for role in ("good", "scrap") for tag in self.counters.get(role, ())}

    @property
    def tags(self) -> tuple[str, ...]:
        """Every tag the agent subscribes to for this machine.

        With per-tag `nodes` (and no template to fall back on), the listing IS
        the truth: a machine whose worksheet had no scrap counter has no
        ScrapCount node, and subscribing to a tag that does not exist would
        turn one missing counter into a machine that reports nothing at all.
        """
        counters = tuple(self.counter_tags)
        reason = (self.reason_tag,) if self.reason_tag else ()
        canonical = (self.state_tag, *counters, *reason, self.analog, *self.extra_tags)
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
    def browse_path(self) -> tuple[str, ...]:
        """The names to walk from Objects/ to reach this machine's node.

        One element for a map that says nothing about grouping, which is
        every map written before `group` existed and every twin-simulator
        line: the machine's node is a direct child of Objects/.
        """
        return (*self.group, self.object)

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

    def acts_on(self, tag: str) -> bool:
        """True for a tag the agent books from rather than only records:
        the state, the counters, and the tag that says why it stopped."""
        if tag == self.state_tag or (self.reason_tag is not None and tag == self.reason_tag):
            return True
        if self.counters or self.state_tag != "State":
            # A machine that names its counters counts on those alone: a
            # GoodCount it also publishes is recorded, never booked a second
            # time. And one whose state is another tag is not set by a State.
            return tag in self.counter_tags
        return tag in SEMANTIC_TAGS

    def reason_for(self, value) -> tuple[str | None, str | None]:
        """`(reason_code, reason)` for a reason-tag value, or `(None, None)`
        when the machine is saying nothing (no tag, or zero / empty).

        A value the map does not know keeps what the machine said and no
        code, rather than a guessed one."""
        if not self.reason_tag or value is None:
            return None, None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not value:
                return None, None
            key = str(int(value))
        else:
            key = str(value).strip()
            if not key or key == "0":
                return None, None
        return self.reason_map.get(key), f"{self.reason_tag} {key}"

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
            return self.state_map[key] if "*" not in self.state_map else (
                self.state_map.get(key) or self.state_map["*"])
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
            group=tuple(str(part) for part in (m.get("group") or ())),
            extra_tags=_extra_tags(m, tables),
            node_id=m.get("node_id"),
            nodes=dict(m.get("nodes") or {}),
            # `order_tag` absent = the default writable tag; explicit null = read-only.
            order_tag=m.get("order_tag", DEFAULT_ORDER_TAG),
            state_map={str(k): EquipmentStateName(v) for k, v in (m.get("state_map") or {}).items()},
            counts=bool(m.get("counts", True)),
            reason_tag=m.get("reason_tag") or None,
            reason_map={str(k): str(v) for k, v in (m.get("reason_map") or {}).items()},
            counters=_counters(m),
            state_tag=str(m.get("state_tag") or "State"),
        )
        if spec.reason_map and not spec.reason_tag:
            raise ValueError(
                f"tag map entry {spec.equipment!r} has a `reason_map` and no `reason_tag`: "
                f"it says what the fault numbers mean and not which tag carries them.")
        if not spec.tags:
            raise ValueError(
                f"tag map entry {spec.equipment!r} subscribes to nothing — its 'nodes' has none of "
                f"State/{'/'.join(spec.counter_tags) or 'its counters'}/{spec.analog}"
            )
        machines.append(spec)
    return machines


async def folder_for(objects, idx: int, spec: MachineMap, made: dict) -> object:
    """The node a machine's variables hang off, creating its folders once.

    Two machines on the same PLC share one device node, so the folders are
    cached by the path that reached them rather than created per machine —
    creating `Area1/PLC_02` twice would either fail or, worse, leave two
    nodes with the same browse name and a client that reads one of them.
    """
    parent = objects
    walked: tuple[str, ...] = ()
    for part in spec.group:
        walked = (*walked, part)
        if walked not in made:
            made[walked] = await parent.add_object(idx, part)
        parent = made[walked]
    return parent


def _counters(entry: dict) -> dict[str, tuple[str, ...]]:
    """A machine's `counters`, checked: refused rather than half-read, because a
    counter tag booked under the wrong role is production that never happened."""
    block = entry.get("counters")
    if block is None:
        return {}
    who = entry.get("equipment")
    if not isinstance(block, dict) or set(block) - {"good", "scrap"}:
        raise ValueError(
            f"tag map entry {who!r}: `counters` says which tags count good and which count "
            f"scrap - {{\"good\": [...], \"scrap\": [...]}}; it says {block!r}.")
    if entry.get("counts") is False:
        raise ValueError(f"tag map entry {who!r} has `counters` and `counts: false`: it names "
                         f"the tags it counts on and says it counts nothing.")
    out: dict[str, tuple[str, ...]] = {}
    seen: set[str] = set()
    for role in ("good", "scrap"):
        tags = block.get(role) or []
        if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() for t in tags):
            raise ValueError(f"tag map entry {who!r}: `counters.{role}` is a list of tag names; "
                             f"it says {tags!r}.")
        for tag in tags:
            if tag in seen:
                raise ValueError(f"tag map entry {who!r}: {tag!r} is listed twice in `counters`; "
                                 f"a count summed twice is production that did not happen.")
            seen.add(tag)
        out[role] = tuple(tags)
    if not seen:
        raise ValueError(f"tag map entry {who!r}: `counters` names no tag at all. Leave it out "
                         f"for GoodCount and ScrapCount, or write `counts: false`.")
    return out


def _extra_tags(entry: dict, tables: dict) -> tuple[str, ...]:
    """What else this machine subscribes to, beyond the canonical four."""
    if entry.get("extra_tags") is not None:
        return tuple(entry["extra_tags"])                 # an explicit override
    if entry.get("node_id") or entry.get("nodes"):
        return ()                                         # see load_tag_map
    published = tables.get(entry.get("object") or entry.get("equipment"), {})
    counters = entry.get("counters") or {}
    canonical = {"State", entry.get("state_tag"), "GoodCount", "ScrapCount", entry.get("reason_tag"),
                 *(counters.get("good") or ()), *(counters.get("scrap") or ()),
                 entry.get("analog", DEFAULT_ANALOG), "TSec"}
    return tuple(tag for tag in published.get("tags", ()) if tag not in canonical)
