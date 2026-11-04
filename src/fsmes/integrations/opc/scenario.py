"""Scenarios: timed tag values written into an OPC UA server.

A scenario file says "at this time, this tag takes this value", with two
shorthands: a ramp ("climbs from A to B over N seconds") and a product ("goes
through these stations in order, each N seconds apart"). `fsmes scenario play`
writes it into any OPC UA server — Kepware first — so every system reading that
server, this MES or another one, sees the same product go through.

The file is JSON, read here with the line of every object kept, so a mistake is
reported as "line 14: ..." rather than as a path into a tree.

The safety rule is in this module, not in the docs. A write to a tag on a real
PLC driver reaches the PLC, so before anything is written every target is read
and judged:

- its own `UserAccessLevel` must allow a write, or it is reported read-only;
- its channel (the first segment of its address) must be declared in the file's
  `drivers:` block as `Simulator` or `Memory Based`, because Kepware's address
  space does not name a channel's driver (its System Tags list none) — and
  any sign the server gives of a network or serial device behind that channel
  refuses it whatever the file says. There is no override.

`play` writes nothing unless every target passes.
"""

from __future__ import annotations

import asyncio
import json
import json.decoder
import json.scanner
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from asyncua import Client, Server, ua

from fsmes import shadow

SAFE_DRIVERS = {"simulator": "Simulator", "memory based": "Memory Based"}
NODE_ID = re.compile(r"^(ns=\d+;|nsu=[^;]+;)?[isgb]=")


class ScenarioError(Exception):
    """A scenario file that cannot be played, with every problem found."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


# ------------------------------------------------------------------ the file


class _Obj(dict):
    """A JSON object that remembers the line it started on."""

    line = 0


def _decoder() -> json.JSONDecoder:
    decoder = json.JSONDecoder()

    def parse_object(s_and_end, strict, scan_once, object_hook, object_pairs_hook, memo=None):
        s, end = s_and_end
        line = s.count("\n", 0, end) + 1
        pairs, end = json.decoder.JSONObject(s_and_end, strict, scan_once, None, list, memo)
        obj = _Obj()
        for key, value in pairs:
            if key in obj:
                raise ScenarioError([f"line {line}: {key!r} is given twice in the same block"])
            obj[key] = value
        obj.line = line
        return obj, end

    decoder.parse_object = parse_object
    decoder.scan_once = json.scanner.py_make_scanner(decoder)
    return decoder


@dataclass
class Tag:
    alias: str
    address: str
    line: int
    type: str | None = None  # only `scenario serve` reads it

    @property
    def is_node_id(self) -> bool:
        return bool(NODE_ID.match(self.address))

    @property
    def channel(self) -> str | None:
        """The first segment of the address: Kepware's channel name for
        `ns=2;s=<channel>.<device>.<tag>`, the top object for a browse path.
        A numeric or GUID node id has none."""
        if self.is_node_id:
            _, _, ident = self.address.rpartition(";")
            if not ident.startswith("s="):
                return None
            return ident[2:].split(".", 1)[0] or None
        return self.address.split("/", 1)[0].split(":", 1)[-1] or None


@dataclass
class Write:
    at: float
    alias: str
    line: int
    value: Any = None
    increment: float | None = None   # a count that goes up by this much
    why: str = ""                    # "ramp", "CAN-0001 at LD", ...

    def describe(self) -> str:
        return f"+{_num(self.increment)}" if self.increment is not None else _show(self.value)


@dataclass
class Scenario:
    source: str
    name: str
    namespace: str | None
    tags: dict[str, Tag]
    drivers: dict[str, str]
    writes: list[Write] = field(default_factory=list)

    @property
    def end(self) -> float:
        return max((w.at for w in self.writes), default=0.0)

    def used(self) -> list[Tag]:
        seen = {w.alias for w in self.writes}
        return [tag for alias, tag in self.tags.items() if alias in seen]


def load(path: Path) -> Scenario:
    """Read and check a scenario file. Raises ScenarioError naming every
    problem with its line."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScenarioError([f"cannot read {path}: {exc.strerror or exc}"]) from None
    return parse(text, str(path))


def parse(text: str, source: str = "<scenario>") -> Scenario:
    try:
        doc = _decoder().decode(text)
    except json.JSONDecodeError as exc:
        raise ScenarioError([f"line {exc.lineno}: not valid JSON — {exc.msg}"]) from None
    if not isinstance(doc, _Obj):
        raise ScenarioError(["line 1: a scenario is one JSON object, { ... }"])
    problems: list[str] = []
    _known(doc, {"name", "namespace", "tags", "drivers", "steps", "product"}, problems)

    tags: dict[str, Tag] = {}
    raw_tags = doc.get("tags")
    if not isinstance(raw_tags, _Obj) or not raw_tags:
        problems.append(f"line {doc.line}: `tags` is missing — name each tag once, "
                        f'"alias": "ns=2;s=Channel.Device.Tag"')
        raw_tags = _Obj()
    for alias, spec in raw_tags.items():
        if alias.startswith("_"):
            continue
        if isinstance(spec, str):
            tags[alias] = Tag(alias, spec.strip(), raw_tags.line)
        elif isinstance(spec, _Obj) and isinstance(spec.get("node"), str):
            _known(spec, {"node", "type"}, problems)
            kind = spec.get("type")
            if kind is not None and kind not in SERVE_TYPES:
                problems.append(f"line {spec.line}: tag {alias!r} type {kind!r} is not one of "
                                f"{', '.join(SERVE_TYPES)}")
            tags[alias] = Tag(alias, spec["node"].strip(), spec.line, kind)
        else:
            problems.append(f"line {raw_tags.line}: tag {alias!r} must be a node id or a "
                            f'browse path ("LD/GoodCount"), or {{"node": ..., "type": ...}}')

    drivers: dict[str, str] = {}
    raw_drivers = doc.get("drivers", _Obj())
    if not isinstance(raw_drivers, _Obj):
        problems.append(f"line {doc.line}: `drivers` is a block of channel: driver name")
    else:
        for channel, driver in raw_drivers.items():
            if channel.startswith("_"):
                continue
            if not isinstance(driver, str):
                problems.append(f"line {raw_drivers.line}: driver for channel {channel!r} must be text")
                continue
            drivers[channel] = driver

    scenario = Scenario(source, str(doc.get("name") or Path(source).stem),
                        doc.get("namespace"), tags, drivers)

    steps = doc.get("steps", [])
    if not isinstance(steps, list):
        problems.append(f"line {doc.line}: `steps` is a list, [ ... ]")
        steps = []
    for step in steps:
        _step(step, scenario, problems)

    products = doc.get("product", [])
    if isinstance(products, _Obj):
        products = [products]
    if not isinstance(products, list):
        problems.append(f"line {doc.line}: `product` is a block or a list of blocks")
        products = []
    for product in products:
        _product(product, scenario, problems)

    if not scenario.writes and not problems:
        problems.append(f"line {doc.line}: nothing to write — add `steps` or a `product`")
    if problems:
        raise ScenarioError(problems)
    # Stable sort: two writes at the same second go in the order the file gives.
    scenario.writes.sort(key=lambda w: w.at)
    return scenario


def _known(obj: _Obj, allowed: set[str], problems: list[str]) -> None:
    for key in obj:
        if key not in allowed and not key.startswith("_"):
            problems.append(f"line {obj.line}: unknown key {key!r} (expected one of "
                            f"{', '.join(sorted(allowed))}; a key starting with _ is a note)")


def _alias(obj: _Obj, key: str, scenario: Scenario, problems: list[str], required: bool = True):
    alias = obj.get(key)
    if alias is None:
        if required:
            problems.append(f"line {obj.line}: `{key}` is missing")
        return None
    if alias not in scenario.tags:
        problems.append(f"line {obj.line}: tag {alias!r} is not in `tags`")
        return None
    return alias


def _value_ok(value: Any) -> bool:
    if isinstance(value, bool | int | float | str):
        return True
    return isinstance(value, list) and bool(value) and all(
        isinstance(v, bool | int | float) for v in value)


def _step(step: Any, scenario: Scenario, problems: list[str]) -> None:
    if not isinstance(step, _Obj):
        problems.append("a step is a block: {\"at\": \"00:10\", \"tag\": ..., \"value\": ...}")
        return
    _known(step, {"at", "tag", "value", "ramp"}, problems)
    at = _time(step, "at", problems)
    alias = _alias(step, "tag", scenario, problems)
    if ("value" in step) == ("ramp" in step):
        problems.append(f"line {step.line}: a step has a `value` or a `ramp`, one of the two")
        return
    if at is None or alias is None:
        return
    if "value" in step:
        if not _value_ok(step["value"]):
            problems.append(f"line {step.line}: value {step['value']!r} is not a number, "
                            f"true/false, text, or a list of numbers")
            return
        scenario.writes.append(Write(at, alias, step.line, value=step["value"]))
        return
    ramp = step["ramp"]
    if not isinstance(ramp, _Obj):
        problems.append(f"line {step.line}: `ramp` is a block: {{\"from\": 0, \"to\": 400, \"over\": 120}}")
        return
    _known(ramp, {"from", "to", "over", "every"}, problems)
    start, stop = ramp.get("from"), ramp.get("to")
    if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in (start, stop)):
        problems.append(f"line {ramp.line}: a ramp needs numbers for `from` and `to`")
        return
    over = _time(ramp, "over", problems)
    every = _time(ramp, "every", problems, default=1.0)
    if over is None or every is None:
        return
    if over <= 0 or every <= 0:
        problems.append(f"line {ramp.line}: `over` and `every` must be more than zero seconds")
        return
    whole = isinstance(start, int) and isinstance(stop, int)
    n = max(1, round(over / every))
    for i in range(n + 1):
        value = start + (stop - start) * i / n
        value = round(value) if whole else round(value, 6)
        scenario.writes.append(Write(at + over * i / n, alias, ramp.line, value=value, why="ramp"))


def _product(product: Any, scenario: Scenario, problems: list[str]) -> None:
    if not isinstance(product, _Obj):
        problems.append("a product is a block with `stations`")
        return
    _known(product, {"start", "advance_s", "units", "every_s", "id", "first", "stations"}, problems)
    start = _time(product, "start", problems, default=0.0)
    advance = _time(product, "advance_s", problems)
    every = _time(product, "every_s", problems) if "every_s" in product else advance
    units, first = product.get("units", 1), product.get("first", 1)
    template = product.get("id", "")
    stations = product.get("stations")
    if not isinstance(units, int) or isinstance(units, bool) or units < 1:
        problems.append(f"line {product.line}: `units` is a whole number, 1 or more")
        return
    if not isinstance(first, int) or isinstance(first, bool):
        problems.append(f"line {product.line}: `first` is a whole number")
        return
    if not isinstance(template, str):
        problems.append(f"line {product.line}: `id` is text, like \"CAN-{{n:04d}}\"")
        return
    try:
        template.format(n=first)
    except (KeyError, IndexError, ValueError) as exc:
        problems.append(f"line {product.line}: `id` {template!r} cannot be filled in ({exc}); "
                        f"{{n}} is the unit's number")
        return
    if not isinstance(stations, list) or not stations:
        problems.append(f"line {product.line}: a product needs `stations`, in the order it visits them")
        return
    if start is None or advance is None or every is None:
        return
    if advance < 0 or every < 0:
        problems.append(f"line {product.line}: `advance_s` and `every_s` cannot be negative")
        return

    plan = []
    before = len(problems)
    for station in stations:
        if not isinstance(station, _Obj):
            problems.append(f"line {product.line}: each station is a block with a `station` name")
            return
        _known(station, {"station", "count", "by", "state", "running", "after", "id", "set",
                         "advance_s"}, problems)
        name = station.get("station")
        if not isinstance(name, str) or not name:
            problems.append(f"line {station.line}: a station needs a `station` name")
            continue
        count = _alias(station, "count", scenario, problems, required=False)
        state = _alias(station, "state", scenario, problems, required=False)
        id_tag = _alias(station, "id", scenario, problems, required=False)
        by = station.get("by", 1)
        if not isinstance(by, int | float) or isinstance(by, bool):
            problems.append(f"line {station.line}: `by` is a number")
            continue
        extra = station.get("set", _Obj())
        if not isinstance(extra, _Obj):
            problems.append(f"line {station.line}: `set` is a block of tag: value")
            continue
        sets = []
        for alias, value in extra.items():
            if alias.startswith("_"):
                continue
            if alias not in scenario.tags:
                problems.append(f"line {extra.line}: tag {alias!r} is not in `tags`")
            elif not _value_ok(value):
                problems.append(f"line {extra.line}: value {value!r} for {alias!r} is not a number, "
                                f"true/false, text, or a list of numbers")
            else:
                sets.append((alias, value))
        for key in ("running", "after"):
            if key in station and not _value_ok(station[key]):
                problems.append(f"line {station.line}: `{key}` {station[key]!r} is not a value")
        if ("running" in station or "after" in station) and "state" not in station:
            problems.append(f"line {station.line}: `running`/`after` need a `state` tag")
        dwell = _time(station, "advance_s", problems, default=advance)
        if not (count or state or id_tag or sets):
            problems.append(f"line {station.line}: station {name!r} writes nothing — give it a "
                            f"`count`, `state`, `id` or `set`")
        plan.append((station, name, count, by, state, id_tag, sets, dwell))
    if len(problems) > before:
        return

    for k in range(units):
        n = first + k
        label = template.format(n=n) if template else f"unit {n}"
        t = start + k * every
        for station, name, count, by, state, id_tag, sets, dwell in plan:
            why = f"{label} at {name}"
            line = station.line
            if id_tag:
                value = label if template else str(n)
                scenario.writes.append(Write(t, id_tag, line, value=value, why=why))
            if state:
                scenario.writes.append(Write(t, state, line, value=station.get("running", 1), why=why))
            for alias, value in sets:
                scenario.writes.append(Write(t, alias, line, value=value, why=why))
            if count:
                scenario.writes.append(Write(t, count, line, increment=by, why=why))
            if state and "after" in station:
                scenario.writes.append(Write(t + (dwell or 0), state, line, value=station["after"],
                                             why=f"{label} leaves {name}"))
            t += dwell or 0


def _time(obj: _Obj, key: str, problems: list[str], default: float | None = None) -> float | None:
    if key not in obj:
        if default is None:
            problems.append(f"line {obj.line}: `{key}` is missing")
        return default
    try:
        return parse_time(obj[key])
    except ValueError as exc:
        problems.append(f"line {obj.line}: `{key}` {exc}")
        return None


def parse_time(value: Any) -> float:
    """Seconds, or "mm:ss", or "h:mm:ss"."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        if value < 0:
            raise ValueError(f"{value!r} is before the start")
        return float(value)
    if isinstance(value, str) and re.fullmatch(r"\d+(:\d{1,2}){0,2}(\.\d+)?", value.strip()):
        seconds = 0.0
        for part in value.strip().split(":"):
            seconds = seconds * 60 + float(part)
        return seconds
    raise ValueError(f"{value!r} is not a time: seconds (90) or minutes:seconds (\"01:30\")")


def clock(seconds: float) -> str:
    whole = int(seconds)
    text = f"{whole // 60:02d}:{whole % 60:02d}" if whole < 3600 else (
        f"{whole // 3600}:{whole % 3600 // 60:02d}:{whole % 60:02d}")
    frac = seconds - whole
    return text + (f".{round(frac * 10)}" if frac >= 0.05 else "")


def _num(value) -> str:
    return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)


def _show(value) -> str:
    return json.dumps(value) if isinstance(value, str | bool | list) else _num(value)


# ------------------------------------------------------------- the server side


INT_RANGES = {
    ua.VariantType.SByte: (-2**7, 2**7 - 1), ua.VariantType.Byte: (0, 2**8 - 1),
    ua.VariantType.Int16: (-2**15, 2**15 - 1), ua.VariantType.UInt16: (0, 2**16 - 1),
    ua.VariantType.Int32: (-2**31, 2**31 - 1), ua.VariantType.UInt32: (0, 2**32 - 1),
    ua.VariantType.Int64: (-2**63, 2**63 - 1), ua.VariantType.UInt64: (0, 2**64 - 1),
}
FLOATS = {ua.VariantType.Float, ua.VariantType.Double}


@dataclass
class Target:
    """What the server says about one tag the scenario writes."""

    tag: Tag
    node: Any = None
    node_id: str = ""
    variant: ua.VariantType | None = None
    array_length: int | None = None    # None: a scalar
    writable: bool = False
    problem: str = ""                  # empty when the tag may be written
    verdict: str = "ok"                # ok | missing | read-only | refused | type

    def type_name(self) -> str:
        if self.variant is None:
            return "?"
        name = self.variant.name
        return f"{name}[{self.array_length}]" if self.array_length is not None else name


def coerce(value: Any, target: Target) -> ua.Variant:
    """The value as the node's own type, or ValueError saying why it cannot be.
    Kepware answers a wrong variant with BadTypeMismatch, so nothing is left to
    the library's guess."""
    vt = target.variant
    if vt is None:
        raise ValueError("the server did not say this tag's type")
    if target.array_length is not None:
        if not isinstance(value, list):
            raise ValueError(f"this tag is an array of {target.array_length}; give a list")
        if target.array_length and len(value) != target.array_length:
            raise ValueError(f"this tag holds {target.array_length} values; the list has {len(value)}")
        return ua.Variant([_scalar(v, vt) for v in value], vt)
    if isinstance(value, list):
        raise ValueError(f"this tag holds one {vt.name}, not a list")
    return ua.Variant(_scalar(value, vt), vt)


def _scalar(value: Any, vt: ua.VariantType) -> Any:
    if vt == ua.VariantType.Boolean:
        if isinstance(value, bool):
            return value
        if value in (0, 1):
            return bool(value)
        raise ValueError(f"{value!r} is not true/false")
    if vt == ua.VariantType.String:
        if isinstance(value, bool):
            raise ValueError(f"{value!r} is not text")
        return value if isinstance(value, str) else _num(value)
    if vt in INT_RANGES:
        if isinstance(value, bool) or not isinstance(value, int | float) or float(value) != int(value):
            raise ValueError(f"{value!r} is not a whole number, and this tag is {vt.name}")
        low, high = INT_RANGES[vt]
        if not low <= int(value) <= high:
            raise ValueError(f"{value!r} does not fit {vt.name} ({low} to {high})")
        return int(value)
    if vt in FLOATS:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{value!r} is not a number")
        return float(value)
    raise ValueError(f"this tag's type, {vt.name}, is not one a scenario writes")


async def _node_for(client: Client, tag: Tag, namespace: str | None):
    if tag.is_node_id:
        return client.get_node(tag.address)
    idx = None
    path = []
    for part in tag.address.split("/"):
        if re.match(r"^\d+:", part):
            path.append(part)
            continue
        if idx is None:
            if namespace:
                idx = await client.get_namespace_index(namespace)
            else:
                idx = 2
        path.append(f"{idx}:{part}")
    return await client.nodes.objects.get_child(path)


async def _is_kepware(client: Client) -> bool:
    try:
        names = await client.get_namespace_array()
    except Exception:
        return False
    return any("kepserver" in str(name).lower() or "kepware" in str(name).lower() for name in names)


async def _real_device_sign(client: Client, tag: Tag) -> str | None:
    """Ask Kepware for evidence of real equipment behind this tag's channel.

    Kepware names no driver in its address space, but its `_System` tags differ
    by driver class: a serial driver's channel has `_ComId`, an Ethernet
    driver's has `_NetworkAdapter`, and an Ethernet device's `_DeviceId` is an
    IP address. A Simulator or Memory Based channel is neither serial nor
    Ethernet. Any such sign refuses the channel, whatever the file declares.
    """
    head, _, ident = tag.address.rpartition(";")
    parts = ident[2:].split(".")
    ns = head + ";" if head else ""
    probes = [f"{ns}s={parts[0]}._System._ComId", f"{ns}s={parts[0]}._System._NetworkAdapter"]
    for probe in probes:
        try:
            await client.get_node(probe).read_value()
        except Exception:
            continue
        return f"the server shows {probe.split('s=', 1)[1]}, which only a serial or Ethernet driver has"
    if len(parts) >= 3:
        probe = f"{ns}s={parts[0]}.{parts[1]}._System._DeviceId"
        try:
            device_id = await client.get_node(probe).read_value()
        except Exception:
            return None
        if isinstance(device_id, str) and re.search(r"\d+\.\d+\.\d+\.\d+|[A-Za-z].*\.", device_id):
            return f"device {parts[1]} is addressed as {device_id!r}, a network address"
    return None


def judge_driver(tag: Tag, drivers: dict[str, str]) -> str:
    """Empty when the file says this tag's channel is a simulator; else why not."""
    channel = tag.channel
    if channel is None:
        return ("its address has no channel name (a numeric node id), so nobody can say "
                "what is behind it; address it by name")
    declared = drivers.get(channel)
    if declared is None:
        return (f"channel {channel!r} is not declared in `drivers`; say which driver it uses — "
                f"only Simulator or Memory Based channels are written")
    normal = " ".join(declared.lower().replace("-", " ").split())
    if normal in SAFE_DRIVERS:
        return ""
    if normal == "advanced simulator":
        return (f"channel {channel!r} is an Advanced Simulator: its tags are read from a database "
                f"over ODBC and cannot be written")
    return f"channel {channel!r} uses {declared!r}, which reaches real equipment"


async def inspect(client: Client, scenario: Scenario) -> dict[str, Target]:
    """Resolve and judge every tag the scenario writes. Reads only."""
    kepware = await _is_kepware(client)
    targets: dict[str, Target] = {}
    for tag in scenario.used():
        target = Target(tag)
        targets[tag.alias] = target
        try:
            node = await _node_for(client, tag, scenario.namespace)
            node_class = await node.read_node_class()
        except Exception as exc:
            target.verdict, target.problem = "missing", f"not on the server ({_short(exc)})"
            continue
        target.node, target.node_id = node, node.nodeid.to_string()
        if node_class != ua.NodeClass.Variable:
            target.verdict, target.problem = "missing", f"is a {node_class.name}, not a tag"
            continue
        try:
            data = await node.read_data_value()
            access = (await node.read_attribute(ua.AttributeIds.UserAccessLevel)).Value.Value
            data_type = await node.read_data_type()
        except Exception as exc:
            target.verdict, target.problem = "missing", f"cannot be read ({_short(exc)})"
            continue
        current = data.Value.Value if data.Value is not None else None
        if data.Value is not None and current is not None:
            target.variant = data.Value.VariantType
        elif data_type.NamespaceIndex == 0 and isinstance(data_type.Identifier, int):
            try:
                target.variant = ua.VariantType(data_type.Identifier)
            except ValueError:
                target.variant = None
        if isinstance(current, list):
            target.array_length = len(current)
        target.writable = bool((int(access or 0) >> ua.AccessLevel.CurrentWrite) & 1)

        reason = judge_driver(tag, scenario.drivers)
        if not reason and kepware and tag.is_node_id:
            reason = await _real_device_sign(client, tag) or ""
        if reason:
            target.verdict, target.problem = "refused", reason
        elif not target.writable:
            target.verdict, target.problem = "read-only", "the server allows reading it, not writing"
        else:
            for write in scenario.writes:
                if write.alias != tag.alias:
                    continue
                try:
                    if write.increment is not None:
                        if target.variant not in INT_RANGES and target.variant not in FLOATS:
                            raise ValueError(f"a count must be a number tag; this one is {target.type_name()}")
                    else:
                        coerce(write.value, target)
                except ValueError as exc:
                    target.verdict, target.problem = "type", f"line {write.line}: {exc}"
                    break
    return targets


def _short(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    return text.splitlines()[0][:120]


def report(targets: dict[str, Target], out: Callable[[str], None] = print) -> int:
    """Print one line per tag; return how many cannot be written."""
    bad = 0
    out(f"{'tag':<18} {'type':<12} {'access':<10} node")
    for alias, target in targets.items():
        access = "write" if target.writable else ("read" if target.node is not None else "-")
        out(f"{alias:<18} {target.type_name():<12} {access:<10} {target.node_id or target.tag.address}")
        if target.problem:
            bad += 1
            out(f"  {target.verdict.upper()}: {target.problem}")
    good = len(targets) - bad
    out(f"{len(targets)} tags: {good} can be written, {bad} cannot.")
    return bad


async def play(client: Client, scenario: Scenario, targets: dict[str, Target], *,
               speed: float = 1.0, start: float = 0.0, out: Callable[[str], None] = print,
               now: Callable[[], float] = time.monotonic, sleep=asyncio.sleep) -> int:
    """Write every value at its time. Returns how many writes were made.

    Refuses to start unless every target was judged writable: a scenario half
    played is a test of nothing. Refused outright in shadow mode, which
    promises that this MES changes nothing outside its own database."""
    shadow.guard("opc.scenario_play", detail=f"Nothing written from {scenario.source}.")
    blocked = [t for t in targets.values() if t.problem]
    if blocked:
        raise ScenarioError([f"{t.tag.alias}: {t.verdict}: {t.problem}" for t in blocked])
    writes = [w for w in scenario.writes if w.at >= start]
    began = now()
    done = 0
    for write in writes:
        due = (write.at - start) / speed
        wait = due - (now() - began)
        if wait > 0:
            await sleep(wait)
        target = targets[write.alias]
        if write.increment is not None:
            current = await target.node.read_value()
            if isinstance(current, bool) or not isinstance(current, int | float):
                raise ScenarioError([f"line {write.line}: {write.alias} reads {current!r}, not a count"])
            value = current + write.increment
            shown = f"{_show(value)} (+{_num(write.increment)})"
        else:
            value = write.value
            shown = _show(value)
        variant = coerce(value, target)
        # No timestamps on the write: Kepware, like many servers, refuses a
        # write that carries a SourceTimestamp.
        try:
            await target.node.write_attribute(ua.AttributeIds.Value, ua.DataValue(variant))
        except Exception as exc:
            raise ScenarioError([f"{clock(write.at)} {write.alias}: the server refused the write "
                                 f"({_short(exc)}); stopped after {done} writes"]) from None
        done += 1
        why = f"   ({write.why})" if write.why else ""
        out(f"{clock(write.at)} set {write.alias} = {shown}{why}")
    return done


def timeline(scenario: Scenario, out: Callable[[str], None] = print) -> None:
    for write in scenario.writes:
        why = f"   ({write.why})" if write.why else ""
        out(f"{clock(write.at)} set {write.alias} = {write.describe()}{why}")


# --------------------------------------------------- a server to rehearse on

SERVE_TYPES = {
    "Boolean": ua.VariantType.Boolean, "Int16": ua.VariantType.Int16, "UInt16": ua.VariantType.UInt16,
    "Int32": ua.VariantType.Int32, "UInt32": ua.VariantType.UInt32, "Int64": ua.VariantType.Int64,
    "Float": ua.VariantType.Float, "Double": ua.VariantType.Double, "String": ua.VariantType.String,
}


def _guess_type(scenario: Scenario, tag: Tag) -> tuple[ua.VariantType, Any]:
    if tag.type:
        vt = SERVE_TYPES[tag.type]
    else:
        first = next((w for w in scenario.writes if w.alias == tag.alias), None)
        value = first.value if first and first.increment is None else 0
        sample = value[0] if isinstance(value, list) else value
        vt = (ua.VariantType.Boolean if isinstance(sample, bool) else ua.VariantType.String
              if isinstance(sample, str) else ua.VariantType.Double
              if isinstance(sample, float) else ua.VariantType.Int32)
    first = next((w for w in scenario.writes if w.alias == tag.alias and w.increment is None), None)
    if first is not None and isinstance(first.value, list):
        return vt, [_zero(vt)] * len(first.value)
    return vt, _zero(vt)


def _zero(vt: ua.VariantType) -> Any:
    return {ua.VariantType.Boolean: False, ua.VariantType.String: ""}.get(
        vt, 0.0 if vt in FLOATS else 0)


async def rehearsal_server(scenario: Scenario, endpoint: str) -> Server:
    """A server holding every tag of the scenario as a writable memory tag at
    the scenario's own addresses, like a Kepware Memory Based channel. Not
    started; the caller starts and stops it."""
    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    server.set_server_name("fsmes scenario rehearsal")
    namespace = scenario.namespace or "urn:fsmes:scenario"
    index = await server.register_namespace(namespace)
    folders: dict[str, Any] = {}
    for tag in scenario.tags.values():
        vt, initial = _guess_type(scenario, tag)
        if tag.is_node_id:
            node_id = ua.NodeId.from_string(tag.address)
            while node_id.NamespaceIndex > len(await server.get_namespace_array()) - 1:
                await server.register_namespace(f"urn:fsmes:scenario:{len(await server.get_namespace_array())}")
            node = await server.nodes.objects.add_variable(
                node_id, tag.address.rpartition("=")[2], ua.Variant(initial, vt))
        else:
            *parents, leaf = [p.split(":", 1)[-1] for p in tag.address.split("/")]
            parent = server.nodes.objects
            key = ""
            for name in parents:
                key += "/" + name
                if key not in folders:
                    folders[key] = await parent.add_object(index, name)
                parent = folders[key]
            node = await parent.add_variable(index, leaf, ua.Variant(initial, vt))
        await node.set_writable()
    return server
