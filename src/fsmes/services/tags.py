"""Every tag a machine publishes, as the screens and the agents see it.

The tag fabric made each machine publish a dozen tags; until this module the
product read two of them - the state, and one declared process value. This is
the read side of the fabric: the latest value of every tag with what the
manifest knows about it, the alarm word decoded into names, the equipment
tree with its cost centers, and work in progress between the stations of a
line.

Three sources, in descending order of authority, for *which* tags a machine
has: the generated manifest (`tags.json` beside the replayed line - kind,
unit, bounds, alarm bit meanings), the tag map (what the agent subscribes
to), and tag history (what has actually been written). A real plant with no
generator has the last two; the screens must not go blank for want of the
first.
"""

from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.config import get_settings
from fsmes.db import utcnow
from fsmes.domain import (
    Equipment,
    EquipmentLevel,
    EquipmentState,
    OrderStatus,
    TagValue,
    WorkOrder,
    WorkOrderOperation,
)
from fsmes.integrations.opc.tag_map import load_manifest, load_tag_map
from fsmes.kernel.tags import STRUCTURAL_TAGS
from fsmes.services import execution, masterdata, workorders

# A tag written once a second that has not written for a minute is not live.
# Reported as a fact beside the age; the screen decides how loudly to say it.
STALE_AFTER_SECONDS = 60.0

# Kinds written only when they change. A State that last changed an hour ago
# is not stale on a machine that has been running for an hour - the way to
# know those tags are live is that the machine as a whole is still talking.
CHANGE_DRIVEN = frozenset({"state", "alarm", "sp", "counter"})

# The order the kinds are worth reading in. State first, then what the
# machine is doing to the product, then the counters that book production.
_KIND_ORDER = {"state": 0, "alarm": 1, "pv": 2, "sp": 2, "counter": 3}


# ------------------------------------------------------------------ sources

def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


@lru_cache(maxsize=8)
def _catalog(tag_map: str, map_mtime: float, replay_dir: str, manifest_mtime: float) -> dict[str, dict]:
    """equipment code -> {"tags": [...subscribed names...], "meta": {tag: manifest entry}}.

    Cached on both files' mtimes, so regenerating a line or editing the map
    takes effect on the next request rather than the next restart.
    """
    manifest = load_manifest(Path(replay_dir)) if manifest_mtime >= 0 else {"tables": {}}
    tables = manifest.get("tables", {})
    out: dict[str, dict] = {}
    try:
        machines = load_tag_map(Path(tag_map), manifest)
    except (OSError, ValueError, KeyError):
        # A malformed or missing map must not take the screens down; the
        # machine's tags are then discovered from history instead.
        return out
    for machine in machines:
        table = tables.get(machine.object) or tables.get(machine.equipment) or {}
        out[machine.equipment] = {
            "tags": list(machine.tags),
            "meta": dict(table.get("tags", {})),
            "analog": machine.analog,
        }
    return out


def catalog() -> dict[str, dict]:
    settings = get_settings()
    tag_map = Path(settings.tag_map_file)
    replay = Path(settings.replay_dir)
    return _catalog(str(tag_map), _mtime(tag_map), str(replay), _mtime(replay / "tags.json"))


def decode_alarm(word: float | int | None, bits: dict | None) -> list[str]:
    """The names of the bits set in an alarm word. A set bit the manifest
    does not name is reported as `bit N` rather than dropped: an alarm the
    documentation forgot is still an alarm."""
    if word is None:
        return []
    value = int(word)
    names = bits or {}
    active = []
    for i in range(32):
        if value >> i & 1:
            active.append(names.get(str(i), f"bit {i}"))
    return active


# ----------------------------------------------------------------- snapshot

def _latest(db: Session, unit: Equipment, name: str) -> TagValue | None:
    return db.execute(
        select(TagValue)
        .where(TagValue.tag == f"{unit.code}.{name}")
        .order_by(TagValue.ts.desc(), TagValue.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _published(db: Session, unit: Equipment) -> list[str]:
    """What this machine has actually written, from history. The slow path,
    for a machine no map or manifest describes."""
    rows = db.execute(
        select(TagValue.tag).where(TagValue.equipment_id == unit.id).distinct()
    ).scalars()
    prefix = f"{unit.code}."
    return sorted(t[len(prefix):] for t in rows if t.startswith(prefix))


def snapshot(db: Session, unit: Equipment, cat: dict[str, dict] | None = None) -> dict:
    """Every tag on one machine: latest value, age, and what the manifest knows.

    `cat` is the catalog to consult (tests pass one; the API passes None and
    the settings decide). Tags the manifest describes come with kind, unit,
    bounds and setpoint pairing; tags only history knows come with a value
    and an honest `kind: null`.
    """
    entry = (cat if cat is not None else catalog()).get(unit.code) or {}
    meta: dict[str, dict] = entry.get("meta", {})
    names: list[str] = []
    for name in [*entry.get("tags", []), *meta]:
        if name not in names:
            names.append(name)
    source = "manifest" if meta else ("tag map" if names else "history")
    if not names:
        names = _published(db, unit)

    now = utcnow()
    latest = {name: _latest(db, unit, name) for name in names}
    ages = {name: (now - row.ts).total_seconds() for name, row in latest.items() if row is not None}
    # How long since the machine said anything at all. A change-driven tag is
    # judged by this, not by its own age.
    quiet_for = min(ages.values()) if ages else None

    def stale(name: str, kind: str | None) -> bool | None:
        age = ages.get(name)
        if age is None:
            return None
        if kind in CHANGE_DRIVEN:
            return quiet_for > STALE_AFTER_SECONDS
        return age > STALE_AFTER_SECONDS

    tags = []
    for name in names:
        row = latest[name]
        info = meta.get(name, {})
        age = ages.get(name)
        value = None if row is None else (row.value_num if row.value_num is not None else row.value_text)
        item = {
            "tag": name,
            "value": value,
            "ts": row.ts if row else None,
            "age_seconds": round(age, 1) if age is not None else None,
            "stale": stale(name, info.get("kind")),
            "kind": info.get("kind"),
            "unit": info.get("unit"),
            "writable": bool(info.get("writable", False)),
            "min": info.get("min"),
            "max": info.get("max"),
            "nominal": info.get("nominal"),
            "follows": info.get("follows"),
            "drives": info.get("drives"),
            "note": info.get("note"),
            "structural": name in STRUCTURAL_TAGS,
            "primary": name == entry.get("analog"),
        }
        if info.get("kind") == "alarm" or name == "AlarmWord":
            item["active"] = decode_alarm(value if isinstance(value, (int, float)) else None, info.get("bits"))
        tags.append(item)

    tags.sort(key=lambda t: (_KIND_ORDER.get(t["kind"] or "", 4), not t["primary"], t["tag"]))
    return {
        "equipment": unit.code,
        "name": unit.name,
        "at": now,
        "source": source,
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "quiet_for_seconds": round(quiet_for, 1) if quiet_for is not None else None,
        "tags": tags,
    }


def alarms(db: Session, units: list[Equipment], cat: dict[str, dict] | None = None) -> list[dict]:
    """Which alarm bits are set on each machine, right now."""
    cat = cat if cat is not None else catalog()
    out = []
    for unit in units:
        info = (cat.get(unit.code) or {}).get("meta", {}).get("AlarmWord", {})
        row = _latest(db, unit, "AlarmWord")
        word = row.value_num if row is not None else None
        out.append({
            "equipment": unit.code,
            "name": unit.name,
            "word": int(word) if word is not None else None,
            "active": decode_alarm(word, info.get("bits")),
            "ts": row.ts if row else None,
        })
    return out


def alarm_history(db: Session, unit: Equipment, hours: float, cat: dict[str, dict] | None = None) -> list[dict]:
    """What a machine raised over the last `hours`: every change of its alarm
    word, decoded. The "now" view answers "is it alarming"; this answers
    "did it", which is the question a failed check an hour later asks."""
    if hours <= 0:
        return []
    cat = cat if cat is not None else catalog()
    bits = (cat.get(unit.code) or {}).get("meta", {}).get("AlarmWord", {}).get("bits")
    cutoff = utcnow() - timedelta(hours=hours)
    rows = db.execute(
        select(TagValue.ts, TagValue.value_num)
        .where(TagValue.tag == f"{unit.code}.AlarmWord", TagValue.ts >= cutoff)
        .order_by(TagValue.ts, TagValue.id)
    ).all()
    out: list[dict] = []
    last: int | None = None
    for ts, value in rows:
        word = int(value) if value is not None else None
        if word is None or word == last:
            continue
        out.append({"ts": ts, "word": word, "active": decode_alarm(word, bits)})
        last = word
    return out


# --------------------------------------------------------------------- head

def _open_states(db: Session) -> dict[int, EquipmentState]:
    rows = db.scalars(select(EquipmentState).where(EquipmentState.ended_at.is_(None)))
    return {s.equipment_id: s for s in rows}


def head(db: Session, unit: Equipment) -> dict:
    """What a machine page opens with: where it sits, what it is doing."""
    chain = []
    node = unit.parent
    while node is not None:
        chain.append({"code": node.code, "name": node.name, "level": node.level.value})
        node = node.parent
    chain.reverse()
    state = _open_states(db).get(unit.id)
    current = next(iter(workorders.dispatch_list(db, unit.code)), None)
    return {
        "code": unit.code,
        "name": unit.name,
        "level": unit.level.value,
        "path": chain,
        "line": next((c for c in reversed(chain) if c["level"] == EquipmentLevel.WORK_CENTER.value), None),
        "cost_center": masterdata.cost_center(db, unit),
        "ideal_cycle_seconds": unit.ideal_cycle_seconds,
        "state": state.state.value if state else "unknown",
        "reason": state.reason if state else None,
        "since": state.started_at if state else None,
        "current_order": current.order.code if current else None,
        "current_operation": current.name if current else None,
        "current_seq": current.seq if current else None,
    }


# --------------------------------------------------------------------- tree

def tree(db: Session) -> list[dict]:
    """The equipment hierarchy, any depth, with cost centers resolved and the
    current state on every work unit."""
    states = _open_states(db)
    everything = db.scalars(select(Equipment).order_by(Equipment.code)).all()
    by_parent: dict[int | None, list[Equipment]] = {}
    for node in everything:
        by_parent.setdefault(node.parent_id, []).append(node)

    def build(node: Equipment) -> dict:
        out = {
            "code": node.code,
            "name": node.name,
            "level": node.level.value,
            "cost_center": masterdata.cost_center(db, node),
            "children": [build(child) for child in by_parent.get(node.id, [])],
        }
        if node.level is EquipmentLevel.WORK_UNIT:
            state = states.get(node.id)
            out["state"] = state.state.value if state else "unknown"
            out["since"] = state.started_at if state else None
        return out

    return [build(root) for root in by_parent.get(None, [])]


# ---------------------------------------------------------------------- WIP

def line_wip(db: Session, line: Equipment) -> dict:
    """Work in progress at each station of a line, summed over the orders
    on the floor. Derived from `execution.wip`, so it inherits its honesty:
    a negative number is reported, not clamped, and `consistent` says so."""
    units = masterdata.work_units_under(db, line)
    codes = {u.code for u in units}
    ids = {u.id for u in units}
    on_floor = db.scalars(
        select(WorkOrder)
        .join(WorkOrderOperation, WorkOrderOperation.work_order_id == WorkOrder.id)
        .where(WorkOrder.status.in_((OrderStatus.RELEASED, OrderStatus.RUNNING)),
               WorkOrderOperation.equipment_id.in_(ids))
        .distinct()
        .order_by(WorkOrder.code)
    ).all()

    stations: dict[str, dict] = {}
    orders = []
    consistent = True
    for order in on_floor:
        report = execution.wip(db, order.code)
        consistent = consistent and report["consistent"]
        orders.append({"order": order.code, "material": report["material"],
                       "wip_total": report["wip_total"], "consistent": report["consistent"]})
        for stage in report["stages"]:
            code = stage["equipment"]
            if code not in codes:
                continue
            station = stations.setdefault(code, {"code": code, "seq": stage["seq"], "wip_qty": 0.0,
                                                 "cost_center": stage["cost_center"], "orders": []})
            station["seq"] = min(station["seq"], stage["seq"])
            station["wip_qty"] += stage["wip_qty"]
            station["orders"].append({"order": order.code, "wip_qty": stage["wip_qty"],
                                      "status": stage["status"]})
    names = {u.code: u.name for u in units}
    ordered = sorted(stations.values(), key=lambda s: (s["seq"], s["code"]))
    for station in ordered:
        station["name"] = names.get(station["code"], station["code"])
    return {
        "line": line.code,
        "orders": orders,
        "stations": ordered,
        "wip_total": sum(s["wip_qty"] for s in ordered),
        "consistent": consistent,
        "idle_stations": sorted(codes - set(stations)),
    }


# ------------------------------------------------------------------ browse

def browse(db: Session, units: list[Equipment], cat: dict[str, dict] | None = None) -> dict:
    """Every tag on every machine, flat, with a health line per machine.

    The engineering view of the whole fabric: which machines have gone
    quiet, which tags stopped arriving, which alarm bits are set, and where
    the writable setpoints and their bounds are. Built from `snapshot`, so
    it cannot disagree with a machine's own page.
    """
    cat = cat if cat is not None else catalog()
    machines = []
    rows = []
    for unit in units:
        snap = snapshot(db, unit, cat=cat)
        alarm = next((t for t in snap["tags"] if t.get("active")), None)
        machines.append({
            "code": unit.code,
            "name": unit.name,
            "quiet_for_seconds": snap["quiet_for_seconds"],
            "tags": len(snap["tags"]),
            "stale": sum(1 for t in snap["tags"] if t["stale"]),
            "alarms": alarm["active"] if alarm else [],
            "source": snap["source"],
        })
        for t in snap["tags"]:
            rows.append({"equipment": unit.code, **t})
    return {
        "at": utcnow(),
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "machines": machines,
        "rows": rows,
    }


def latest_value(db: Session, equipment_code: str, tag: str) -> float | None:
    """The last numeric value recorded for one tag, or None."""
    unit = masterdata.get_equipment(db, equipment_code)
    row = _latest(db, unit, tag)
    return row.value_num if row is not None else None
