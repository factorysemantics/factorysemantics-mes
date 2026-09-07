"""Per-unit traceability: backwards to what went in, forwards to where it went.

The two questions a recall asks, and the reason serialisation exists:

    "what is in this pallet?"        - walk down the containment tree
    "where did this lot end up?"     - walk up from every unit that used it

Order-level genealogy answers neither precisely. It can say a suspect lot
reached an order; it cannot say which pallet, which is the difference between
holding a pallet and holding a week.

Built to the scale the cutlery plant asked for (labs/cutlery): ten million
serialised pieces a day, arriving from the markers a stack at a time. So
units come into existence in batches with their container, one call and one
audit entry per stack; the serial counter is a row, not a count of the
table; and every walk of the tree is a query per level, never a query per
unit - a pallet of six thousand pieces is answered in a handful of reads.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from fsmes.domain import (
    LotConsumption,
    MaterialLot,
    SerialSequence,
    SerialUnit,
    UnitComponent,
    UnitInspection,
    UnitStatus,
)
from fsmes.services import Conflict, Invalid, NotFound, audit, masterdata, workorders

# How many ids one IN(...) carries. SQLite allows 32,766 bound parameters;
# a page of ten thousand keeps the statements few without going near it.
CHUNK = 10_000
# How deep a containment tree is walked. Piece, stack, pack, pallet, truck
# is five; a sixth is somebody's mistake, not a plant.
MAX_DEPTH = 6
# How many units one batch may bring into existence.
MAX_BATCH = 5_000


def get(session: Session, serial: str) -> SerialUnit:
    unit = session.scalar(select(SerialUnit).where(SerialUnit.serial == serial))
    if unit is None:
        raise NotFound(f"no unit {serial}")
    return unit


def _chunks(values: list, size: int = CHUNK) -> Iterable[list]:
    for i in range(0, len(values), size):
        yield values[i:i + size]


# ---------------------------------------------------------------- numbering
def next_serial(session: Session, prefix: str) -> str:
    """The next serial for a prefix, from its counter row.

    The counter starts where the table already is - the highest number any
    existing unit with the prefix carries - so a plant that numbered by
    counting keeps counting from the same place, and a marker's own serials
    mixed in cannot make the next generated one collide.
    """
    row = session.get(SerialSequence, prefix)
    if row is None:
        highest = 0
        for serial in session.scalars(select(SerialUnit.serial).where(SerialUnit.serial.like(f"{prefix}-%"))):
            tail = serial[len(prefix) + 1:]
            if tail.isdigit():
                highest = max(highest, int(tail))
        row = SerialSequence(prefix=prefix, next=highest + 1)
        session.add(row)
        session.flush()
    number = row.next
    row.next = number + 1
    session.flush()
    return f"{prefix}-{number:06d}"


# ---------------------------------------------------------------- one at a time
def produce(session: Session, *, material_code: str, order_code: str | None = None,
            equipment_code: str | None = None, serial: str | None = None,
            inherit_components: bool = True, actor: str = "system") -> SerialUnit:
    """Bring one identified unit into existence.

    `inherit_components` copies the order's consumption records onto the unit,
    which is the pragmatic thing for a line that issues material by the pallet
    and produces by the bottle: it is the best claim the data supports, and a
    claim the data supports is worth more than a precise one it does not.
    """
    material = masterdata.get_material(session, material_code)
    order = workorders.get(session, order_code) if order_code else None
    equipment = masterdata.get_equipment(session, equipment_code) if equipment_code else None

    code = serial or next_serial(session, material.code)
    if session.scalar(select(SerialUnit.id).where(SerialUnit.serial == code)):
        raise Conflict(f"serial {code} already exists")

    unit = SerialUnit(
        serial=code, material_id=material.id, status=UnitStatus.GOOD,
        produced_by_order_id=order.id if order else None,
        produced_on_id=equipment.id if equipment else None)
    session.add(unit)
    session.flush()

    if inherit_components and order is not None:
        for consumption in session.scalars(select(LotConsumption).where(
                LotConsumption.work_order_id == order.id)):
            session.add(UnitComponent(
                unit_id=unit.id, lot_id=consumption.lot_id,
                quantity=0.0,   # the unit's share is not separately measured
                operation_id=consumption.operation_id))
        session.flush()

    audit.record(session, actor=actor, action="unit.produced",
                 entity_type="unit", entity_id=code,
                 after={"material": material.code,
                        "order": order.code if order else None,
                        "equipment": equipment.code if equipment else None})
    return unit


# ---------------------------------------------------------------- a stack at a time
def _ancestors(session: Session, unit_id: int) -> list[int]:
    """The chain of containers above a unit, nearest first."""
    chain: list[int] = []
    cursor = session.scalar(select(SerialUnit.parent_id).where(SerialUnit.id == unit_id))
    while cursor is not None and len(chain) < MAX_DEPTH + 1:
        chain.append(cursor)
        cursor = session.scalar(select(SerialUnit.parent_id).where(SerialUnit.id == cursor))
    return chain


def produce_batch(session: Session, *, units: list[dict], order_code: str | None = None,
                  equipment_code: str | None = None, material_code: str | None = None,
                  container: dict | None = None, into: str | None = None,
                  contains: list[str] | None = None, actor: str = "system") -> dict:
    """Bring a batch of identified units into existence, packed.

    What a stacker's scanner sends when a stack closes: the twenty-four
    pieces it holds, the stack's own serial, and nothing else. One call, one
    transaction, one audit entry - the shape the ten-million-a-day plant
    needs, and what a real marker integration sends anyway; nobody posts a
    piece at a time at a hundred a second.

    `units`     new units: [{"serial", "material"?, "order"?, "equipment"?}];
                the batch's material/order/equipment are the defaults.
    `container` a new unit to create first and put everything into:
                {"serial", "material", "order"?, "equipment"?}.
    `into`      an existing unit to put everything into instead.
    `contains`  existing units to move into the container as well - a
                wrapper putting a stack into a pack, a palletizer putting
                240 packs on a pallet.

    Lot components are not copied per unit here: at this rate that is a
    second table of the same size for a claim the order already makes.
    `trace_back` and `where_used` read the order's consumption when a unit
    has no components of its own, and say that is the basis.
    """
    units = units or []
    contains = contains or []
    if len(units) > MAX_BATCH:
        raise Invalid(f"a batch is at most {MAX_BATCH} units; this one is {len(units)}")
    if container and into:
        raise Invalid("give a new container or an existing one to pack into, not both")
    if not units and not container and not contains:
        raise Invalid("nothing to produce or pack")

    materials: dict[str, int] = {}

    def material_id(code: str | None) -> int:
        if not code:
            raise Invalid("every unit needs a material, its own or the batch's")
        if code not in materials:
            materials[code] = masterdata.get_material(session, code).id
        return materials[code]

    orders: dict[str, int] = {}

    def order_id(code: str | None) -> int | None:
        if not code:
            return None
        if code not in orders:
            orders[code] = workorders.get(session, code).id
        return orders[code]

    machines: dict[str, int] = {}

    def equipment_id(code: str | None) -> int | None:
        if not code:
            return None
        if code not in machines:
            machines[code] = masterdata.get_equipment(session, code).id
        return machines[code]

    # Every new serial must be new. One indexed lookup for the whole batch.
    wanted = [u["serial"] for u in units] + ([container["serial"]] if container else [])
    if len(set(wanted)) != len(wanted):
        raise Invalid("a batch names the same serial twice")
    taken: list[str] = []
    for chunk in _chunks(wanted):
        taken.extend(session.scalars(select(SerialUnit.serial).where(SerialUnit.serial.in_(chunk))))
    if taken:
        raise Conflict(f"serial(s) already exist: {', '.join(sorted(taken)[:5])}"
                       + (f" and {len(taken) - 5} more" if len(taken) > 5 else ""))

    parent_id: int | None = None
    parent_serial: str | None = None
    if container:
        holder = SerialUnit(
            serial=container["serial"], material_id=material_id(container.get("material")),
            status=UnitStatus.GOOD,
            produced_by_order_id=order_id(container.get("order", order_code)),
            produced_on_id=equipment_id(container.get("equipment", equipment_code)))
        session.add(holder)
        session.flush()
        parent_id, parent_serial = holder.id, holder.serial
    elif into:
        holder = get(session, into)
        parent_id, parent_serial = holder.id, holder.serial

    moved = 0
    if contains:
        if parent_id is None:
            raise Invalid("`contains` needs a container to move the units into")
        found: dict[str, int] = {}
        for chunk in _chunks(contains):
            found.update({s: i for i, s in session.execute(
                select(SerialUnit.id, SerialUnit.serial).where(SerialUnit.serial.in_(chunk)))})
        missing = [s for s in contains if s not in found]
        if missing:
            raise NotFound(f"no unit {missing[0]}" + (f" (and {len(missing) - 1} more)" if len(missing) > 1 else ""))
        ids = set(found.values())
        if parent_id in ids:
            raise Invalid("a unit cannot contain itself")
        # Packing a pallet into one of its own packs would make the tree a
        # loop and every trace an infinite walk.
        if ids & set(_ancestors(session, parent_id)):
            raise Invalid(f"{parent_serial} is already inside one of the units it would contain")
        for chunk in _chunks(list(ids)):
            session.execute(update(SerialUnit).where(SerialUnit.id.in_(chunk)).values(parent_id=parent_id))
        moved = len(ids)

    if units:
        rows = [{
            "serial": u["serial"],
            "material_id": material_id(u.get("material", material_code)),
            "status": UnitStatus.GOOD,
            "produced_by_order_id": order_id(u.get("order", order_code)),
            "produced_on_id": equipment_id(u.get("equipment", equipment_code)),
            "parent_id": parent_id,
        } for u in units]
        for chunk in _chunks(rows, 1000):
            session.execute(insert(SerialUnit), chunk)
    session.flush()

    audit.record(session, actor=actor, action="unit.batch", entity_type="unit",
                 entity_id=parent_serial or wanted[0],
                 after={"units": len(units), "first": units[0]["serial"] if units else None,
                        "last": units[-1]["serial"] if units else None,
                        "container": parent_serial, "packed": moved,
                        "order": order_code, "equipment": equipment_code})
    return {"created": len(units), "container": parent_serial, "packed": moved}


def pack(session: Session, *, serial: str, into: str, actor: str = "system") -> SerialUnit:
    """Put one unit inside another: a bottle into a case, a case onto a pallet."""
    unit = get(session, serial)
    container = get(session, into)
    if unit.id == container.id:
        raise Invalid("a unit cannot contain itself")

    # Walk up from the container: packing a pallet into one of its own bottles
    # would make the tree a loop and every trace an infinite walk.
    if unit.id in _ancestors(session, container.id):
        raise Invalid(f"{into} is already inside {serial}")

    unit.parent_id = container.id
    session.flush()
    audit.record(session, actor=actor, action="unit.packed", entity_type="unit",
                 entity_id=serial, after={"into": into})
    return unit


# ---------------------------------------------------------------- walking down
def _descendants(session: Session, root_id: int, cap: int | None = None) -> tuple[list[list[tuple]], bool]:
    """Every unit beneath a root, one level at a time, as bare rows
    (id, serial, material_id, status, parent_id). A query per level, never
    per unit. `cap` bounds the rows per level; past it the walk stops and
    says so."""
    levels: list[list[tuple]] = []
    frontier = [root_id]
    truncated = False
    for _ in range(MAX_DEPTH):
        level: list[tuple] = []
        for chunk in _chunks(frontier):
            level.extend(session.execute(
                select(SerialUnit.id, SerialUnit.serial, SerialUnit.material_id, SerialUnit.status,
                       SerialUnit.parent_id)
                .where(SerialUnit.parent_id.in_(chunk))
                .order_by(SerialUnit.serial)).all())
        if not level:
            break
        if cap is not None and len(level) > cap:
            level = level[:cap]
            truncated = True
        levels.append(level)
        frontier = [row[0] for row in level]
        if truncated:
            break
    return levels, truncated


def _descendant_ids(session: Session, root_ids: list[int]) -> list[int]:
    """Every unit beneath any of the roots, ids only."""
    out: list[int] = []
    frontier = list(root_ids)
    for _ in range(MAX_DEPTH):
        level: list[int] = []
        for chunk in _chunks(frontier):
            level.extend(session.scalars(select(SerialUnit.id).where(SerialUnit.parent_id.in_(chunk))))
        if not level:
            break
        out.extend(level)
        frontier = level
    return out


def _unit_out(unit: SerialUnit) -> dict:
    return {
        "serial": unit.serial,
        "material": unit.material.code,
        "status": unit.status.value,
        "produced_at": unit.produced_at,
        "order": unit.order.code if unit.order else None,
        "equipment": unit.equipment.code if unit.equipment else None,
        "packed_into": unit.parent.serial if unit.parent else None,
        "note": unit.note,
    }


def contents(session: Session, serial: str, limit: int = 50, cap: int = 50_000) -> dict:
    """Everything inside this unit, as the tree it actually is - with at
    most `limit` children drawn per node, and every node saying how many
    it holds. A pallet of six thousand pieces is a count and a sample, not
    six thousand rows; the counts and the by-material summary cover the
    whole pallet. `cap` bounds rows read per level; past it the summary is
    marked partial."""
    unit = get(session, serial)
    levels, truncated = _descendants(session, unit.id, cap=cap)
    material_codes = masterdata.material_codes(session)

    children: dict[int, list[tuple]] = defaultdict(list)
    for level in levels:
        for row in level:
            children[row[4]].append(row)
    by_material: Counter = Counter()
    by_status: Counter = Counter()
    total = 0
    for level in levels:
        for row in level:
            total += 1
            by_material[material_codes.get(row[2], str(row[2]))] += 1
            by_status[getattr(row[3], "value", row[3])] += 1

    def node(row: tuple) -> dict:
        kids = children.get(row[0], [])
        return {
            "serial": row[1],
            "material": material_codes.get(row[2], str(row[2])),
            "status": getattr(row[3], "value", row[3]),
            "contains": [node(k) for k in kids[:limit]],
            "contains_total": len(kids),
        }

    top = children.get(unit.id, [])
    return {
        **_unit_out(unit),
        "contains": [node(k) for k in top[:limit]],
        "contains_total": len(top),
        "units_inside": total,
        "by_material": dict(sorted(by_material.items())),
        "by_status": dict(sorted(by_status.items())),
        "partial": truncated,
        "limit": limit,
    }


# ---------------------------------------------------------------- walking back
def _components_for(session: Session, unit_ids: list[int]) -> tuple[dict[int, list[UnitComponent]], set[int]]:
    """Explicit components per unit, and the units that have none."""
    explicit: dict[int, list[UnitComponent]] = defaultdict(list)
    for chunk in _chunks(unit_ids):
        for component in session.scalars(select(UnitComponent).where(UnitComponent.unit_id.in_(chunk))):
            explicit[component.unit_id].append(component)
    return explicit, {uid for uid in unit_ids if uid not in explicit}


def _order_lots(session: Session, order_ids: Iterable[int]) -> dict[int, list[LotConsumption]]:
    out: dict[int, list[LotConsumption]] = defaultdict(list)
    ids = [o for o in set(order_ids) if o is not None]
    for chunk in _chunks(ids):
        for consumption in session.scalars(select(LotConsumption).where(LotConsumption.work_order_id.in_(chunk))):
            out[consumption.work_order_id].append(consumption)
    return out


def trace_back(session: Session, serial: str) -> dict:
    """What went into this unit, and into everything inside it.

    The question asked when a customer complains about one pack. A unit with
    its own component records answers from them (basis "unit"); a unit
    without - the batch path never writes them - answers from what its order
    consumed (basis "order"), which is the best claim the data supports, and
    the answer says which it is.
    """
    unit = get(session, serial)
    inside = [unit.id, *_descendant_ids(session, [unit.id])]

    # Explicit components, and the orders of the units that have none.
    explicit, without = _components_for(session, inside)
    order_of: dict[int, int | None] = {}
    serial_of: dict[int, str] = {unit.id: unit.serial}
    for chunk in _chunks(inside):
        for uid, code, order_id in session.execute(
                select(SerialUnit.id, SerialUnit.serial, SerialUnit.produced_by_order_id)
                .where(SerialUnit.id.in_(chunk))):
            serial_of[uid] = code
            order_of[uid] = order_id
    by_order = _order_lots(session, (order_of.get(uid) for uid in without))

    lots: dict[int, MaterialLot] = {}

    def lot(lot_id: int) -> MaterialLot:
        if lot_id not in lots:
            lots[lot_id] = session.get(MaterialLot, lot_id)
        return lots[lot_id]

    seen: dict[str, dict] = {}

    def note(lot_id: int, uid: int, basis: str, quantity: float | None) -> None:
        the_lot = lot(lot_id)
        entry = seen.setdefault(the_lot.code, {
            "lot": the_lot.code, "material": the_lot.material.code,
            "from_order": None, "used_in": [], "units": 0, "basis": basis, "quantity": quantity})
        if the_lot.produced_by_order_id:
            entry["from_order"] = getattr(session.get(type(unit.order) if unit.order else MaterialLot,
                                                      the_lot.produced_by_order_id), "code", None)
        entry["units"] += 1
        if len(entry["used_in"]) < 20 and serial_of[uid] not in entry["used_in"]:
            entry["used_in"].append(serial_of[uid])
        if basis == "unit" and entry["basis"] == "order":
            entry["basis"] = "unit"

    for uid, components in explicit.items():
        for component in components:
            note(component.lot_id, uid, "unit", component.quantity)
    for uid in without:
        for consumption in by_order.get(order_of.get(uid), []):
            note(consumption.lot_id, uid, "order", None)

    return {
        "serial": unit.serial,
        "material": unit.material.code,
        "status": unit.status.value,
        "order": unit.order.code if unit.order else None,
        "units_inside": len(inside) - 1,
        "components": sorted(seen.values(), key=lambda c: c["lot"]),
    }


def where_used(session: Session, lot_code: str) -> dict:
    """Every unit carrying this lot, and the outermost package holding each.

    The recall question. Reporting the top-level container is the point: a
    warehouse holds pallets, not bottles, and a list of ten thousand bottle
    serials is not an instruction anybody can act on.

    Units carry a lot either by their own component record or - the batch
    path - because the order that produced them consumed it. Both are
    found; the walk up to the pallets is a grouped query per level, so
    three million pieces resolve to their seventeen hundred pallets without
    three million objects in memory.
    """
    lot = session.scalar(select(MaterialLot).where(MaterialLot.code == lot_code))
    if lot is None:
        raise NotFound(f"no lot {lot_code}")

    # Level zero: how many carrying units sit under each parent (or loose).
    # {parent_id or None: count} from the explicit records and from the
    # orders that consumed the lot, without double-counting a unit in both.
    explicit_ids = list(session.scalars(select(UnitComponent.unit_id).where(UnitComponent.lot_id == lot.id)))
    order_ids = list(session.scalars(select(LotConsumption.work_order_id).where(LotConsumption.lot_id == lot.id)))
    counts: Counter = Counter()
    loose: Counter = Counter()      # units with no container at all, by unit id -> 1
    seen_units = 0
    if explicit_ids:
        for chunk in _chunks(explicit_ids):
            for parent_id, n in session.execute(
                    select(SerialUnit.parent_id, func.count()).where(SerialUnit.id.in_(chunk))
                    .group_by(SerialUnit.parent_id)):
                counts[parent_id] += n
                seen_units += n
    if order_ids:
        query = (select(SerialUnit.parent_id, func.count())
                 .where(SerialUnit.produced_by_order_id.in_(list(set(order_ids)))))
        if explicit_ids:
            query = query.where(SerialUnit.id.not_in(explicit_ids[:CHUNK]))
        for parent_id, n in session.execute(query.group_by(SerialUnit.parent_id)):
            counts[parent_id] += n
            seen_units += n
    if None in counts:
        loose[None] = counts.pop(None)

    # Walk up: each level maps a container to its own parent, carrying the
    # unit counts with it, until nothing has a parent.
    tops: Counter = Counter()
    frontier = counts
    for _ in range(MAX_DEPTH):
        if not frontier:
            break
        parent_of: dict[int, int | None] = {}
        for chunk in _chunks(list(frontier)):
            parent_of.update(session.execute(
                select(SerialUnit.id, SerialUnit.parent_id).where(SerialUnit.id.in_(chunk))).all())
        next_level: Counter = Counter()
        for unit_id, n in frontier.items():
            parent = parent_of.get(unit_id)
            if parent is None:
                tops[unit_id] += n
            else:
                next_level[parent] += n
        frontier = next_level
    for unit_id, n in frontier.items():   # deeper than any plant packs
        tops[unit_id] += n

    packages = []
    material_codes = masterdata.material_codes(session)
    for chunk in _chunks(list(tops)):
        for uid, serial, material_id, status in session.execute(
                select(SerialUnit.id, SerialUnit.serial, SerialUnit.material_id, SerialUnit.status)
                .where(SerialUnit.id.in_(chunk))):
            packages.append({"package": serial, "material": material_codes.get(material_id, str(material_id)),
                             "status": getattr(status, "value", status), "units": tops[uid]})
    packages.sort(key=lambda p: p["package"])
    units_affected = seen_units
    return {
        "lot": lot.code,
        "material": lot.material.code,
        "units_affected": units_affected,
        "loose_units": loose.get(None, 0),
        "basis": "unit" if explicit_ids and not order_ids else "order" if order_ids and not explicit_ids
        else "unit and order" if explicit_ids else "none",
        # What a warehouse can actually be asked to pull.
        "packages_to_hold": packages,
        "verdict": (
            f"{units_affected} unit(s) in {len(packages)} package(s) carry {lot.code}"
            + (f", {loose[None]} of them not packed" if loose.get(None) else "")
            if units_affected else f"nothing serialised carries {lot.code}"
        ),
    }


# ---------------------------------------------------------------- status
def set_status(session: Session, serial: str, status: str, *,
               note: str | None = None, cascade: bool = False,
               actor: str = "system") -> list[SerialUnit]:
    """Change a unit's status, optionally everything inside it.

    Cascade is what a quarantine actually means: holding a pallet without
    holding the cases on it holds nothing. Done as one update per level of
    the tree, so holding a pallet of six thousand pieces is four statements.
    """
    try:
        wanted = UnitStatus(status)
    except ValueError as exc:
        raise Invalid(
            f"unknown status {status!r}. Expected one of "
            f"{', '.join(s.value for s in UnitStatus)}") from exc

    unit = get(session, serial)
    ids = [unit.id]
    if cascade:
        ids += _descendant_ids(session, [unit.id])
    for chunk in _chunks(ids):
        values = {"status": wanted}
        if note:
            values["note"] = note
        session.execute(update(SerialUnit).where(SerialUnit.id.in_(chunk)).values(**values))
    session.flush()
    session.expire_all()
    audit.record(session, actor=actor, action="unit.status", entity_type="unit",
                 entity_id=serial,
                 after={"status": wanted.value, "units": len(ids), "note": note})
    touched = []
    for chunk in _chunks(ids):
        touched.extend(session.scalars(select(SerialUnit).where(SerialUnit.id.in_(chunk))))
    return touched


# ---------------------------------------------------------------- inspection groups
# What a vision station sends when it has judged a unit: the unit's serial,
# what it measured, whether it passed - and for a stack, a wrap or a pallet,
# what the unit now contains. The OPC agent assembles the station's group
# tags into one of these per event and hands a batch here, and the whole
# batch becomes rows in a handful of statements: at ten million pieces a
# day a call per piece is the wrong shape by a factor of a thousand.

INSPECTION_KINDS = ("piece", "stack", "wrap", "pallet")


def ingest_inspections(session: Session, events: list[dict], actor: str = "opc-agent") -> dict:
    """Write a batch of inspection events as units, inspections and containment.

    Each event: {"kind": piece|stack|wrap|pallet, "equipment": code, "seq": int,
    "ts": datetime, "serial": str, "material": code, "order": code|None,
    "passed": bool, "fail_mask": int, "values": [floats],
    "members": [serials] (stack, wrap, pallet), "plate": {"serial", "material"} (wrap)}.

    Returns counts: units created, inspections recorded, members packed,
    duplicates skipped (a serial the plant already holds - a replayed event),
    and members the plant had never seen (a stack claiming a piece no marker
    reported - recorded as a finding, the stack still created).
    """
    if not events:
        return {"units": 0, "inspections": 0, "packed": 0, "duplicates": 0, "unknown_members": 0}

    materials = masterdata.material_codes(session)
    material_id = {code: mid for mid, code in materials.items()}
    equipment_id: dict[str, int] = {}
    order_id: dict[str, int | None] = {}

    def eq(code: str) -> int:
        if code not in equipment_id:
            equipment_id[code] = masterdata.get_equipment(session, code).id
        return equipment_id[code]

    def order(code: str | None) -> int | None:
        if not code:
            return None
        if code not in order_id:
            order_id[code] = workorders.get(session, code).id
        return order_id[code]

    # 1. Every serial this batch would create, and which already exist.
    wanted: list[dict] = []
    for e in events:
        wanted.append({"serial": e["serial"], "material": e["material"], "order": e.get("order"),
                       "equipment": e["equipment"], "passed": e.get("passed", True), "ts": e["ts"]})
        if e.get("plate"):
            wanted.append({"serial": e["plate"]["serial"], "material": e["plate"]["material"], "order": e.get("order"),
                           "equipment": e["equipment"], "passed": True, "ts": e["ts"]})
    serials = [w["serial"] for w in wanted]
    existing: set[str] = set()
    for chunk in _chunks(serials):
        existing.update(session.scalars(select(SerialUnit.serial).where(SerialUnit.serial.in_(chunk))))
    seen_here: set[str] = set()
    rows = []
    for w in wanted:
        if w["serial"] in existing or w["serial"] in seen_here:
            continue
        seen_here.add(w["serial"])
        if w["material"] not in material_id:
            raise NotFound(f"no material {w['material']}")
        rows.append({
            "serial": w["serial"], "material_id": material_id[w["material"]],
            "status": UnitStatus.GOOD if w["passed"] else UnitStatus.SCRAPPED,
            "produced_by_order_id": order(w["order"]), "produced_on_id": eq(w["equipment"]),
            "produced_at": w["ts"], "parent_id": None,
        })
    duplicates = len(wanted) - len(rows)

    # 2. The units, in bulk, ids back.
    id_of: dict[str, int] = {}
    for chunk in _chunks(rows, 1000):
        for uid, serial in session.execute(
                insert(SerialUnit).returning(SerialUnit.id, SerialUnit.serial), chunk):
            id_of[serial] = uid

    # 3. The inspections, one per event whose unit was created here.
    inspections = []
    for e in events:
        uid = id_of.get(e["serial"])
        if uid is None:
            continue
        inspections.append({"unit_id": uid, "equipment_id": eq(e["equipment"]), "seq": int(e.get("seq", 0)),
                            "ts": e["ts"], "passed": bool(e.get("passed", True)),
                            "fail_mask": int(e.get("fail_mask", 0)), "values": e.get("values")})
    for chunk in _chunks(inspections, 1000):
        session.execute(insert(UnitInspection), chunk)

    # 4. Containment: members (and a wrap's plate) move under their container.
    packed = 0
    unknown = 0
    by_parent: dict[int, list[str]] = defaultdict(list)
    for e in events:
        parent = id_of.get(e["serial"])
        if parent is None:
            continue
        members = list(e.get("members") or [])
        if e.get("plate"):
            members.append(e["plate"]["serial"])
        by_parent[parent].extend(members)
    for parent, members in by_parent.items():
        for chunk in _chunks(members):
            result = session.execute(
                update(SerialUnit).where(SerialUnit.serial.in_(chunk), SerialUnit.parent_id.is_(None))
                .values(parent_id=parent))
            packed += result.rowcount or 0
            unknown += len(chunk) - (result.rowcount or 0)
    session.flush()

    audit.record(session, actor=actor, action="inspection.batch", entity_type="unit",
                 entity_id=events[0]["serial"],
                 after={"events": len(events), "units": len(rows), "packed": packed,
                        "duplicates": duplicates, "unknown_members": unknown,
                        "stations": sorted({e["equipment"] for e in events})})
    return {"units": len(rows), "inspections": len(inspections), "packed": packed,
            "duplicates": duplicates, "unknown_members": unknown}


def attach_members(session: Session, parent_serial: str, members: list[str]) -> int:
    """Put members under a container that already exists - the retry for a
    stack or a wrap whose event arrived before its pieces were written.
    Returns how many members are still unknown or already held elsewhere."""
    parent_id = session.scalar(select(SerialUnit.id).where(SerialUnit.serial == parent_serial))
    if parent_id is None or not members:
        return len(members)
    held = 0
    for chunk in _chunks(members):
        already = select(func.count(SerialUnit.id)).where(SerialUnit.serial.in_(chunk),
                                                          SerialUnit.parent_id == parent_id)
        held += session.scalar(already) or 0
        result = session.execute(
            update(SerialUnit).where(SerialUnit.serial.in_(chunk), SerialUnit.parent_id.is_(None))
            .values(parent_id=parent_id))
        held += result.rowcount or 0
    session.flush()
    return len(members) - held
