"""A draft tag map from a KEPServerEX project export.

A plant that runs Kepware already has, in its project file, the one list the
MES needs first: every channel, every device, every tag with its type. This
module reads that file (the JSON form: *File > Save As > JSON*) and writes a **draft** of the
MES's own tag map from it, so nobody types 171 node ids by hand.

It is a draft, and it says so on every line it was not sure of:

- one machine per device, its tags addressed by node id,
  `ns=2;s=<Channel>.<Device>[.<Group>...].<Tag>`, which is how Kepware's OPC
  UA server names a tag;
- which tag is the state, which ones count good and scrap, which one is the
  process value and which ones are identifiers is **guessed from the tag's
  name and type** by the small word list below. Every guess carries a
  `_guess` note saying why ("name contains Reject");
- every tag the list could not place is listed under `_unmapped`, by device,
  with its type. Nothing is invented and nothing is dropped: the placed tags
  and the unmapped ones add up to the export's own count, and the file says
  both totals.

What an export cannot say, the draft does not say either. The rated cycle time
is not in it, so `cycle_seconds` is left out and the command says to set it
(`seed-line` then leaves OEE performance unknown; `pack apply` still takes the
loader's 5 s default, which is why the note is loud). What a fault number means is not in it, so a fault
code read as the state is `{"0": "running", "*": "down"}` and the stop is
named "FaultCode 41" until the plant's reason codes are written into
`reason_map`. Every machine is read-only (`order_tag` null).

It reads a file and writes a file. It never touches a server; the next step
is `fsmes opc-verify` against the live one.

**The export's shape.** The parts this reads, and only these:

    project                       (or the project object itself at the top)
      channels[]                  common.ALLTYPES_NAME,
                                  servermain.MULTIPLE_TYPES_DEVICE_DRIVER
        devices[]                 common.ALLTYPES_NAME
          tags[]                  common.ALLTYPES_NAME, servermain.TAG_DATA_TYPE
          tag_groups[]            common.ALLTYPES_NAME, tags[], tag_groups[]

The collections are the Configuration API's own endpoints
(`/config/v1/project/channels/{channel}/devices/{device}/tag_groups/{group}/tags/{tag}`)
and the keys are the property names it returns; `docs/operate/kepware-export.md`
says where each was checked. A key that is missing is read as unknown, never
as a default: a tag with no data type is "type unknown", and it is not counted.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

NAME = "common.ALLTYPES_NAME"
DRIVER = "servermain.MULTIPLE_TYPES_DEVICE_DRIVER"
DATA_TYPE = "servermain.TAG_DATA_TYPE"

#: Kepware's data type numbers, as far as they were checked (see the docs page).
#: A number not here is written as "type <n>" and is never guessed a role.
DATA_TYPES = {0: "String", 1: "Boolean", 2: "Char", 3: "Byte", 4: "Short", 5: "Word",
              6: "Long", 7: "DWord", 8: "Float", 9: "Double"}
_WHOLE = {"Char", "Byte", "Short", "Word", "Long", "DWord"}
_REAL = {"Float", "Double"}


class ExportError(ValueError):
    """The file is not a project export from Kepware this can read, said plainly."""


# The word list. A tag's name is split into words (Good_Count_A, GoodCountA and
# good-count-a all read as good, count, a) and matched against these, in this
# order of trust. It is short on purpose: a word that is not here leaves the
# tag unmapped, which a person can place in a minute, where a clever rule
# would place it wrongly and nobody would look.
STOP_WORDS = ("downtime", "fault", "alarm", "error")      # a number that is 0 while it may run
SCRAP_WORDS = ("reject", "rejects", "scrap", "ng", "bad")
GOOD_WORDS = ("good", "ok", "pass")
AGGREGATE_WORDS = ("total", "lifetime")
COUNT_WORDS = ("count", "counter", "total", "produced", "lifetime")
STATE_WORDS = ("state", "status", "mode", "run", "running")
TEXT_WORDS = ("id", "serial", "lot", "batch", "sn", "code", "name", "recipe")
HEARTBEAT_WORDS = ("heartbeat", "watchdog")


def words(name: str) -> list[str]:
    """`GoodCount_A` -> ['good', 'count', 'a']; `Tray ID` -> ['tray', 'id']."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    return [w.lower() for w in re.split(r"[^A-Za-z0-9]+", spaced) if w]


def _hit(name: str, vocabulary: tuple[str, ...]) -> str | None:
    """The first word of `vocabulary` the name contains, as it is spelled there."""
    found = set(words(name))
    for word in vocabulary:
        if word in found:
            return word
    return None


@dataclass(frozen=True)
class ExportTag:
    path: tuple[str, ...]          # the groups it sits in, outermost first, then its name
    type_code: int | None

    @property
    def name(self) -> str:
        return self.path[-1]

    @property
    def type(self) -> str:
        if self.type_code is None:
            return "type unknown"
        return DATA_TYPES.get(self.type_code, f"type {self.type_code}")


@dataclass
class ExportDevice:
    channel: str
    name: str
    tags: list[ExportTag] = field(default_factory=list)

    def node(self, tag: ExportTag) -> str:
        return "ns=2;s=" + ".".join((self.channel, self.name, *tag.path))


def _named(entry: dict, what: str) -> str:
    name = entry.get(NAME) if isinstance(entry, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise ExportError(f"a {what} in the export has no {NAME}, so nothing can address it.")
    return name


def _type_code(entry: dict) -> int | None:
    value = entry.get(DATA_TYPE)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _walk(node: dict, path: tuple[str, ...]) -> list[ExportTag]:
    out = [ExportTag(path=(*path, _named(t, "tag")), type_code=_type_code(t))
           for t in node.get("tags") or []]
    for group in node.get("tag_groups") or []:
        out.extend(_walk(group, (*path, _named(group, "tag group"))))
    return out


def read_export(path: Path) -> tuple[dict, list[dict]]:
    """The project object, and its channels, or an `ExportError` saying why not."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ExportError(f"{Path(path).name} is not JSON ({exc.msg}, line {exc.lineno}). "
                          f"Save the project from Kepware as JSON, not encrypted.") from None
    project = data.get("project", data) if isinstance(data, dict) else None
    channels = project.get("channels") if isinstance(project, dict) else None
    if not isinstance(channels, list):
        raise ExportError(f"{Path(path).name} has no `channels` list, so it is not a Kepware "
                          f"project export (expected {{\"project\": {{\"channels\": [...]}}}}).")
    return project, channels


def devices_of(channels: list[dict], chosen: list[str], wanted: list[str] | None) -> list[ExportDevice]:
    """The devices on the chosen channels, in the export's order, every tag of each.

    Several channels, because a plant often gives each PLC a channel of its
    own: one line is then a dozen channels with one device on each."""
    names = [_named(c, "channel") for c in channels]
    missing = [c for c in chosen if c not in names]
    if missing:
        raise ExportError(f"there is no channel {', '.join(map(repr, missing))} in the export. "
                          f"It has {len(names)}: {', '.join(names) or 'none'}.")
    found = [ExportDevice(name, _named(d, "device"), _walk(d, ()))
             for name, entry in zip(names, channels, strict=True) if name in chosen
             for d in entry.get("devices") or []]
    if wanted:
        have = {d.name for d in found}
        missing = [w for w in wanted if w not in have]
        if missing:
            where = f"channel {chosen[0]!r}" if len(chosen) == 1 else f"those {len(chosen)} channels"
            raise ExportError(f"{where} has no device {', '.join(map(repr, missing))}. "
                              f"It has {len(found)}: {', '.join(d.name for d in found) or 'none'}.")
        found = [d for d in found if d.name in wanted]
    return found


def _keys(tags: list[ExportTag]) -> list[str]:
    """The name each tag goes by inside its machine: its own name, or, when
    two groups both hold one by that name, every one of them with its group
    path in front (`ToolA.Die_Serial`, `ToolB.Die_Serial`) so neither is the plain one."""
    seen: dict[str, int] = {}
    for tag in tags:
        seen[tag.name] = seen.get(tag.name, 0) + 1
    return [tag.name if seen[tag.name] == 1 else ".".join(tag.path) for tag in tags]


def draft_machine(device: ExportDevice) -> tuple[dict | None, list[dict]]:
    """One machine's draft entry, and the tags it could not place.

    The entry is None when nothing on the device could be placed: a machine
    that subscribes to nothing is refused by the loader, and the honest form
    of "nothing here looked like a machine" is the whole device unmapped.
    """
    keyed = list(zip(device.tags, _keys(device.tags), strict=True))
    nodes: dict[str, str] = {}
    guess: dict[str, str] = {}
    unmapped: list[dict] = []
    good: list[str] = []
    scrap: list[str] = []
    totals: list[tuple[str, str]] = []
    stops: list[tuple[int, bool, int, str, str]] = []
    extras: list[str] = []
    analog = None

    def place(tag: ExportTag, key: str, why: str) -> None:
        nodes[key] = device.node(tag)
        guess[key] = why

    for tag, key in keyed:
        kind = tag.type
        if kind in _WHOLE or kind == "Boolean":
            stop = _hit(tag.name, STOP_WORDS)
            if stop and kind in _WHOLE:
                # Rank by the word, then a name that says it is a code, then
                # export order: a tie never falls to the alphabet.
                stops.append((STOP_WORDS.index(stop), "code" not in words(tag.name), len(stops), key, stop))
                place(tag, key, "")              # said below, once the state is chosen
                continue
            if stop:
                extras.append(key)
                place(tag, key, f"recorded only: name contains {stop.title()} and it is on/off "
                                f"(Boolean), which says that it stopped but not why")
                continue
            if kind in _WHOLE and _hit(tag.name, HEARTBEAT_WORDS):
                place(tag, key, f"recorded only: name contains {_hit(tag.name, HEARTBEAT_WORDS).title()}, "
                                f"a heartbeat says the PLC is alive, not what the machine made")
                extras.append(key)
                continue
            word = _hit(tag.name, SCRAP_WORDS)
            if word and kind in _WHOLE:
                scrap.append(key)
                place(tag, key, f"counts scrap: name contains {word.title()} and it is a whole number ({kind})")
                continue
            word = _hit(tag.name, GOOD_WORDS)
            if word and kind in _WHOLE:
                good.append(key)
                place(tag, key, f"counts good: name contains {word.title()} and it is a whole number ({kind})")
                continue
            word = _hit(tag.name, COUNT_WORDS)
            if word and kind in _WHOLE:
                totals.append((key, word))
                place(tag, key, "")              # said below, once good is known
                continue
            word = _hit(tag.name, STATE_WORDS)
            if word:
                extras.append(key)
                place(tag, key, f"recorded only: name contains {word.title()}, a state word whose values "
                                f"only the plant knows; to read the state from it, set state_tag and "
                                f"write its values into state_map")
                continue
        if kind == "String" and (word := _hit(tag.name, TEXT_WORDS)):
            extras.append(key)
            place(tag, key, f"recorded as text: name contains {word.upper() if len(word) <= 2 else word.title()} "
                            f"and it is a String")
            continue
        if kind in _REAL:
            if analog is None:
                analog = key
                place(tag, key, f"the process value: the first real number ({kind}) on the device")
            else:
                extras.append(key)
                place(tag, key, f"recorded: a further real number ({kind})")
            continue
        unmapped.append({"tag": key, "type": kind, "node": device.node(tag)})

    # Counters. A Good counter is the count; a Total beside it almost always
    # includes it, and booking both is production that never happened.
    named_good = bool(good)
    for key, word in totals:
        if named_good:
            extras.append(key)
            guess[key] = (f"recorded, not booked: name contains {word.title()}, and this device also has "
                          f"a Good counter, which a total usually already includes")
        else:
            good.append(key)
            guess[key] = (f"counts good: name contains {word.title()} and it is a whole number; there is no "
                          f"separate Good counter, so the total is read as the good count")

    names = {key: tag.name for tag, key in keyed}
    # Running totals. Beside per-side or per-lane counters, a counter named
    # Total or Lifetime is usually their sum. Totals with nothing else beside
    # them (a lifetime count per side) are all booked.
    for booked, side in ((good, "good"), (scrap, "scrap")):
        sums = [k for k in booked if _hit(names[k], AGGREGATE_WORDS)]
        if not sums or len(sums) == len(booked):
            continue
        for key in sums:
            booked.remove(key)
            extras.append(key)
            guess[key] = (f"recorded, not booked: name contains {_hit(names[key], AGGREGATE_WORDS).title()}, "
                          f"and the device has other {side} counters, which a total usually adds up")

    # Flags. Where some Good or Scrap tags say they count and others do not,
    # the others are usually flags (OK to process, cycle was good), not tallies.
    for booked, side in ((good, "good"), (scrap, "scrap")):
        flags = [k for k in booked if not _hit(names[k], COUNT_WORDS)]
        if not flags or len(flags) == len(booked):
            continue
        for key in flags:
            booked.remove(key)
            extras.append(key)
            guess[key] = (f"recorded, not booked: a {side} word but no Count in the name, beside {side} "
                          f"tags that do say Count; most likely a flag, not a tally")

    entry: dict = {"equipment": device.name, "object": device.name, "order_tag": None}
    if stops:
        stops.sort()
        *_, state_key, word = stops[0]
        entry["state_tag"] = state_key
        entry["state_map"] = {"0": "running", "*": "down"}
        entry["reason_tag"] = state_key
        guess[state_key] = (f"the state and the stop's reason: name contains {word.title()}, read as 0 while "
                            f"it runs and the stop's number otherwise; what each number means is not in the "
                            f"export, so write it into reason_map")
        for *_, key, other in stops[1:]:
            extras.append(key)
            guess[key] = f"recorded only: name contains {other.title()}; {state_key} was taken as the state"
    if good or scrap:
        entry["counters"] = {"good": good, "scrap": scrap}
    else:
        entry["counts"] = False
        guess["_counts"] = "no tag here is named like a counter, so this machine books no production"
    if analog:
        entry["analog"] = analog
    entry["extra_tags"] = [k for k in nodes if k in extras]
    entry["nodes"] = nodes
    entry["_guess"] = guess
    if len(nodes) == 0:
        return None, unmapped
    return entry, unmapped


def draft_tag_map(export: Path, channels: list[str] | str,
                  devices: list[str] | None = None) -> tuple[dict, list[str]]:
    """The draft, and the lines a person should read before using it."""
    chosen = [channels] if isinstance(channels, str) else list(channels)
    _project, everything = read_export(export)
    found = devices_of(everything, chosen, devices)
    if not found:
        raise ExportError(f"{_these(chosen)} {'has' if len(chosen) == 1 else 'have'} no devices in the "
                          f"export, so there is nothing to map.")
    drivers = sorted({c.get(DRIVER) for c in everything
                      if c.get(NAME) in chosen and isinstance(c.get(DRIVER), str)})
    repeated = {d.name for d in found if sum(1 for o in found if o.name == d.name) > 1}
    machines: list[dict] = []
    unmapped: dict[str, list[dict]] = {}
    notes: list[str] = []
    total = placed = 0
    for device in found:
        total += len(device.tags)
        entry, left = draft_machine(device)
        if entry is not None and device.name in repeated:
            entry["equipment"] = f"{device.channel}-{device.name}"
            entry["_guess"]["_equipment"] = (f"named {entry['equipment']}: another chosen channel also "
                                             f"has a device called {device.name}")
        if left:
            unmapped[device.name] = left
        if entry is None:
            notes.append(f"{device.name}: none of its {len(device.tags)} tags looked like a machine's; "
                         f"all of them are under _unmapped and no machine was written for it")
            continue
        placed += len(entry["nodes"])
        machines.append(entry)
        notes.append(f"{device.name}: a Kepware export has no cycle time; set cycle_seconds (until then "
                     f"`seed-line` leaves OEE performance unknown, and `pack apply` takes the loader's "
                     f"5 s default)")
        if "reason_tag" not in entry:
            notes.append(f"{device.name}: no fault or downtime code found; it has no state tag, so it "
                         f"will not show running or down until one is set")
    left_total = sum(len(v) for v in unmapped.values())
    data = {
        "_comment": (
            f"DRAFT from {Path(export).name}, {_these(chosen)}"
            f"{f' ({', '.join(drivers)})' if drivers else ''}, on {date.today().isoformat()}, "
            f"by `fsmes tagmap from-kepware`. Every `_guess` is a guess from the tag's name and type: "
            f"confirm each one. Every machine is read-only (order_tag null). Next: `fsmes opc-verify`."),
        "_channels": chosen,
        "_totals": {"devices": len(found), "machines": len(machines), "tags": total,
                    "placed": placed, "unmapped": left_total},
        "machines": machines,
        "_unmapped": unmapped,
    }
    return data, notes


def _these(chosen: list[str]) -> str:
    return f"channel {chosen[0]}" if len(chosen) == 1 else f"{len(chosen)} channels"


def write_draft(export: Path, out: Path, channels: list[str] | str,
                devices: list[str] | None = None) -> tuple[dict, list[str]]:
    """Draft and write, then read the file back through the real loader, so a
    draft the agent would refuse is refused here instead."""
    from fsmes.integrations.opc.tag_map import load_tag_map

    data, notes = draft_tag_map(export, channels, devices)
    if not data["machines"]:
        raise ExportError(f"none of the {data['_totals']['tags']} tags on {_these(data['_channels'])} looked "
                          f"like a machine's, so no draft was written. Check the channel and device "
                          f"names, or map these devices by hand.")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    load_tag_map(out)
    return data, notes
