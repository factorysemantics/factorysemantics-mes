"""The line view's data: what the line looks like, and what it just made.

Two questions, deliberately kept apart. The layout changes when a plant engineer
moves a machine — twice a year, maybe. The events change a hundred times a
minute. Serving them from one endpoint would mean re-sending the whole plant
every half second to learn that two bottles came off the washer.

The layout answers "where is everything". It is assembled from three sources, in
descending order of authority: the optional layout file (a human drew this), the
routing (the MES knows the process order), and inference from the equipment's own
name. A line nobody has drawn still renders — stations in routing order, evenly
spaced — because a view that needs a config file before it shows anything is a
view nobody turns on.

The events answer "what has the line made since I last asked". Every unit in that
answer is a `ProductionLog` row: a booking the MES already committed to, under
the OPC agent's one inviolable rule that production is never invented. This
module reads those rows and reformats them. It does not count anything itself,
and it must never start.
"""

import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.config import get_settings
from fsmes.domain import (
    AuditLog,
    Equipment,
    EquipmentLevel,
    EquipmentState,
    ProductionLog,
    RoutingOperation,
    TagValue,
)
from fsmes.integrations.opc.tag_map import load_tag_map
from fsmes.kernel.tags import STRUCTURAL_TAGS
from fsmes.services import NotFound, masterdata, workorders

# One poll should never hand the renderer more work than it can animate. A client
# that has been away longer than this is told the feed was truncated and resyncs
# from the current tail, which is honest — the units it missed happened, it just
# cannot show them arriving.
MAX_UNITS = 1000

DEFAULT_SPACING = 9.0  # metres between station centres
DEFAULT_CAPACITY = 20  # units a conveyor segment can hold before it looks backed up
DEFAULT_PER_TRAY = 12  # units a robot moves in one pick

# Machine silhouettes, matched against "<code> <name>" lowercased. Order matters:
# a depalletiser is not a palletiser, and it contains the word.
_KINDS: tuple[tuple[str, str], ...] = (
    ("depallet", "depalletiser"),
    ("loader", "depalletiser"),
    ("denest", "denester"),
    ("wash", "washer"),
    ("inspect", "inspector"),
    ("quality", "inspector"),
    ("refill", "filler"),
    ("fill", "filler"),
    ("pallet", "palletiser"),
    ("mix", "mixer"),
    ("pack", "packer"),
    ("load", "depalletiser"),
)

def _infer_kind(code: str, name: str) -> str:
    haystack = f"{code} {name}".lower()
    for needle, kind in _KINDS:
        if needle in haystack:
            return kind
    return "generic"


@lru_cache(maxsize=4)
def _layout_file(path: str, mtime: float) -> dict:
    """The drawn layout, or an empty one. `mtime` is in the cache key so editing
    the file takes effect on the next request instead of the next restart."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _read_layout_file() -> dict:
    path = Path(get_settings().line_layout_file)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    return _layout_file(str(path), mtime)


@lru_cache(maxsize=4)
def _tag_map_analogs(path: str, mtime: float) -> dict[str, str]:
    """equipment code -> the browse name of its process value, per the tag map."""
    try:
        return {m.equipment: m.analog for m in load_tag_map(Path(path))}
    except (OSError, ValueError, KeyError):
        # A malformed or missing tag map must not take the whole view down; the
        # analog name is then discovered from tag history instead.
        return {}


def tag_map_analogs() -> dict[str, str]:
    """Each machine's declared process value, per the tag map."""
    path = Path(get_settings().tag_map_file)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    return _tag_map_analogs(str(path), mtime)


def _work_centres(db: Session) -> list[tuple[Equipment, list[Equipment]]]:
    """Every line that has machines on it, most-populated first.

    Most-populated rather than alphabetical because this decides what the view
    opens on, and the interesting line is the one with machines on it.
    """
    centres = db.scalars(
        select(Equipment).where(Equipment.level == EquipmentLevel.WORK_CENTER).order_by(Equipment.code)
    ).all()
    out = []
    for centre in centres:
        # Through cells and groups, not just one hop down: a line whose
        # machines hang off cells used to report as having no machines.
        units = masterdata.work_units_under(db, centre)
        if units:
            out.append((centre, list(units)))
    out.sort(key=lambda pair: (-len(pair[1]), pair[0].code))
    return out


def _resolve_line(db: Session, line_code: str | None) -> tuple[Equipment, list[Equipment]]:
    centres = _work_centres(db)
    if not centres:
        raise NotFound("no line has any machines on it yet — seed a plant first")
    if line_code is None:
        return centres[0]
    for centre, units in centres:
        if centre.code == line_code:
            return centre, units
    raise NotFound(f"line {line_code} not found (known: {', '.join(c.code for c, _ in centres)})")


def _process_order(db: Session, units: list[Equipment]) -> dict[int, int]:
    """equipment id -> routing seq, from whichever routing covers most of the line.

    A machine can appear in several routings; the one that describes the most of
    this line is the one that describes this line.
    """
    ids = {unit.id for unit in units}
    if not ids:
        return {}
    rows = db.execute(
        select(RoutingOperation.routing_id, RoutingOperation.equipment_id, RoutingOperation.seq).where(
            RoutingOperation.equipment_id.in_(ids)
        )
    ).all()
    by_routing: dict[int, dict[int, int]] = {}
    for routing_id, equipment_id, seq in rows:
        # First mention wins if a routing visits the same machine twice.
        by_routing.setdefault(routing_id, {}).setdefault(equipment_id, seq)
    if not by_routing:
        return {}
    return max(by_routing.values(), key=len)


def machine_layer(db: Session) -> dict | None:
    """What actually fed us: the OPC server the agent last connected to.

    Read from the audit trail rather than from configuration, because the agent
    and the API are separate processes and can be pointed at different things —
    reporting the API's own settings would let the view claim a source it has
    never spoken to. `None` means no agent has connected since this database
    was created, so nothing on screen came from a machine at all.
    """
    entry = db.scalar(
        select(AuditLog)
        .where(AuditLog.action == "opc.connected")
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    if entry is None:
        return None
    after = entry.after or {}
    endpoint = after.get("endpoint", entry.entity_id)
    tag_map = after.get("tag_map", "")
    # Name the server from the map that describes it. The tag map is the file
    # that knows the difference; guessing from the port number would be wrong
    # the first time somebody moves Kepware off 49320.
    if "kepware" in tag_map.lower():
        name = "KEPServerEX"
    elif "kepsim" in tag_map.lower():
        name = "replay server"
    else:
        name = "OPC UA server"
    return {
        "name": name,
        "endpoint": endpoint,
        "tag_map": tag_map,
        "security": after.get("security"),
        "addressing": after.get("addressing"),
        "connected_at": entry.ts,
    }


def layout(db: Session, line_code: str | None = None) -> dict:
    """The scene: which machines, in what order, in what shape, where."""
    centre, units = _resolve_line(db, line_code)
    drawn = _read_layout_file()
    line_conf = (drawn.get("lines") or {}).get(centre.code) or {}
    defaults = drawn.get("defaults") or {}
    station_conf = line_conf.get("stations") or {}
    analogs = tag_map_analogs()

    seqs = _process_order(db, units)
    # Machines the routing does not mention still belong to the line; park them
    # after the ones it does, in code order, rather than dropping them.
    ordered = sorted(units, key=lambda u: (seqs.get(u.id) is None, seqs.get(u.id, 0), u.code))

    spacing = float(line_conf.get("spacing", defaults.get("spacing", DEFAULT_SPACING)))
    capacity = int(line_conf.get("conveyor_capacity", defaults.get("conveyor_capacity", DEFAULT_CAPACITY)))
    per_tray = int(line_conf.get("units_per_tray", defaults.get("units_per_tray", DEFAULT_PER_TRAY)))

    stations = []
    for index, unit in enumerate(ordered):
        conf = station_conf.get(unit.code) or {}
        stations.append(
            {
                "code": unit.code,
                "name": unit.name,
                "kind": conf.get("kind") or _infer_kind(unit.code, unit.name),
                "seq": seqs.get(unit.id),
                "x": float(conf.get("x", index * spacing)),
                "z": float(conf.get("z", 0.0)),
                "rot": float(conf.get("rot", 0.0)),
                "cycle_seconds": unit.ideal_cycle_seconds,
                "analog": analogs.get(unit.code),
            }
        )

    return {
        "line": {"code": centre.code, "name": centre.name},
        "source": machine_layer(db),
        "unit": {
            "label": line_conf.get("unit_label", defaults.get("unit_label", "unit")),
            "per_tray": per_tray,
        },
        "conveyor": {"capacity": capacity, "spacing": spacing},
        "stations": stations,
        "lines": [
            {"code": c.code, "name": c.name, "stations": len(u)} for c, u in _work_centres(db)
        ],
    }


def analog_reading(db: Session, unit: Equipment) -> dict | None:
    """The machine's latest process value, as {"name": ..., "value": ...}.

    Real lines disagree about what a machine's process value is called —
    MotorTemp, WashTemp, FillWeight, FeedRate, AirPressure — which is precisely
    why the tag map records it per machine. Ask the tag map first.

    When the tag map cannot answer (a second line, a map swapped since startup),
    ask tag history what this machine has actually been publishing, and take
    whatever is not one of the tags every machine carries. Assuming
    "Temperature" reports nothing at all on every line that calls it something
    else.
    """
    name = tag_map_analogs().get(unit.code)
    query = select(TagValue.tag, TagValue.value_num).where(
        TagValue.equipment_id == unit.id, TagValue.value_num.is_not(None)
    )
    if name:
        query = query.where(TagValue.tag == f"{unit.code}.{name}")
    else:
        query = query.where(
            TagValue.tag.notin_([f"{unit.code}.{tag}" for tag in STRUCTURAL_TAGS]))
    row = db.execute(query.order_by(TagValue.id.desc()).limit(1)).first()
    if row is None:
        return None
    tag, value = row
    return {"name": tag.split(".", 1)[-1], "value": value}


def events(db: Session, line_code: str | None = None, since: int = -1) -> dict:
    """Station status, plus every unit booked since `since`.

    `since` is a `ProductionLog.id`, not a timestamp: it is monotonic, it cannot
    repeat, and it is immune to clock skew between the API and whatever booked
    the row. A negative cursor means "I have just arrived" — the answer is the
    current tail and no units, so the belts fill with live production rather
    than with history the renderer would have to invent positions for.
    """
    centre, units = _resolve_line(db, line_code)
    seqs = _process_order(db, units)
    ordered = sorted(units, key=lambda u: (seqs.get(u.id) is None, seqs.get(u.id, 0), u.code))
    by_id = {unit.id: unit.code for unit in ordered}

    head = db.scalar(select(ProductionLog.id).order_by(ProductionLog.id.desc()).limit(1)) or 0

    booked: list[dict] = []
    truncated = False
    if since >= 0 and head > since:
        rows = db.scalars(
            select(ProductionLog)
            .where(ProductionLog.id > since, ProductionLog.equipment_id.in_(by_id))
            .order_by(ProductionLog.id)
            .limit(MAX_UNITS + 1)
        ).all()
        truncated = len(rows) > MAX_UNITS
        for log in rows[:MAX_UNITS]:
            booked.append(
                {
                    "equipment": by_id[log.equipment_id],
                    "good": log.good_qty,
                    "scrap": log.scrap_qty,
                    "ts": log.ts,
                }
            )
        if truncated:
            # Resume from the last row actually handed over, not from the head,
            # so nothing is skipped once the client catches up.
            head = rows[MAX_UNITS - 1].id

    stations = []
    for unit in ordered:
        state = db.scalar(
            select(EquipmentState).where(
                EquipmentState.equipment_id == unit.id, EquipmentState.ended_at.is_(None)
            )
        )
        current = next(iter(workorders.dispatch_list(db, unit.code)), None)
        stations.append(
            {
                "code": unit.code,
                "state": state.state if state else "unknown",
                "reason": state.reason if state else None,
                "since": state.started_at if state else None,
                "analog": analog_reading(db, unit),
                "order": current.order.code if current else None,
                "operation": current.name if current else None,
                "good": current.good_qty if current else 0.0,
                "scrap": current.scrap_qty if current else 0.0,
            }
        )

    return {
        "line": centre.code,
        "cursor": head,
        "truncated": truncated,
        "stations": stations,
        "units": booked,
    }


# The public name for what the line view, the analysis screen and now the
# line page all need: "which line, and its machines".
resolve_line = _resolve_line
