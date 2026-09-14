"""A plant's master data as data, not as a script.

Until packs existed, a plant's equipment, materials and routings were a
Python file the registry named and `fsmes plant <name> init` ran as a
subprocess with the plant's environment. It worked, and it is the one thing
decision 0022 refuses outright: arbitrary code nobody can review before it
touches a plant, wearing a configuration file's name.

This is the replacement. One directory, one JSON file per kind, read and
written by `fsmes pack apply`:

    masterdata/
      equipment.json           the ISA-95 tree, parents first
      materials.json           what this plant makes and consumes
      bom.json                 which components a material takes, and where
      routings.json            the operations, in order, on named equipment
      quality_specs.json       what a characteristic must measure
      lots.json                material on hand at the start
      work_orders.json         orders to release, so counters have somewhere to book
      maintenance_plans.json   the recurring jobs, and what makes each due
      shifts.json              the patterns this plant works

The last three arrived on 2026-09-14, when the bottling lab plant's line
moved into a pack. It had been seeded by `fsmes seed-kepsim`, which builds a
bill of materials, five maintenance plans and two shift patterns as well as
the equipment and the routing - and a format that could not carry them would
have made "the same line, seeded the same way" a quieter plant than the one
it replaced. Extending the format was the honest half of that trade; the
alternative was a plant that silently lost its BOM and its calendar.

**Rated cycle times are not repeated here.** An equipment entry may leave
`ideal_cycle_seconds` out, and it is read from the pack's own tag map - which
is what the lab seeds already did by hand, and for the reason that matters:
OEE performance is ideal cycle x count / runtime, so a rate that disagrees
with the line that generated the data produces a number that means nothing.

**Applying is idempotent.** Anything already there by code is left exactly as
it is, and the receipt says how many of each kind were made and how many were
already present. It never updates: a pack that changed a routing an order has
already run against would rewrite history, and a pack has no business doing
that. `fsmes pack status` reports the drift instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

#: The files this reads, and what each holds. A file in the directory that is
#: not one of these is a problem: master data nobody reads is master data
#: somebody thinks is loaded.
KINDS: dict[str, str] = {
    "equipment": "the ISA-95 tree: enterprise, site, area, work centre, work unit",
    "materials": "what this plant makes and consumes",
    "bom": "which components a material takes, and at which operation",
    "routings": "the operations, in order, each on a named machine",
    "quality_specs": "what a characteristic must measure for a material",
    "lots": "material on hand when the plant starts",
    "work_orders": "orders to create, and whether to release them",
    "maintenance_plans": "the recurring jobs on a machine, and what makes each due",
    "shifts": "the shift patterns this plant works",
}

REQUIRED: dict[str, tuple[str, ...]] = {
    "equipment": ("code", "name", "level"),
    "materials": ("code", "name"),
    "bom": ("parent", "component", "quantity"),
    "routings": ("code", "name", "material", "operations"),
    "quality_specs": ("material", "characteristic"),
    "lots": ("code", "material", "quantity"),
    "work_orders": ("code", "material", "quantity"),
    "maintenance_plans": ("code", "name", "equipment", "trigger", "interval"),
    "shifts": ("code", "name", "starts", "ends"),
}

OPTIONAL: dict[str, tuple[str, ...]] = {
    "equipment": ("parent", "ideal_cycle_seconds"),
    "materials": ("unit", "type"),
    "bom": ("operation_seq",),
    "routings": (),
    "quality_specs": ("unit", "min", "max"),
    "lots": (),
    "work_orders": ("priority", "release"),
    "maintenance_plans": ("instructions", "document_code", "expected_minutes"),
    "shifts": ("days", "equipment"),
}

#: What `[[maintenance_plans]] trigger` may say, and what each counts. Spelt
#: out here rather than deferred to the enum's own error, because a pack is
#: checked offline and the person fixing it is reading this file's sentences.
TRIGGERS: dict[str, str] = {
    "runtime_hours": "hours the machine actually ran",
    "calendar_days": "elapsed days, use or no use",
    "produced_qty": "units it has made",
}


@dataclass(frozen=True)
class Data:
    directory: Path
    kinds: dict[str, list[dict]] = field(default_factory=dict)

    def rows(self, kind: str) -> list[dict]:
        return self.kinds.get(kind, [])

    @property
    def total(self) -> int:
        return sum(len(rows) for rows in self.kinds.values())


def read(directory: Path) -> Data:
    """Every master-data file in this directory. Missing files are missing,
    not empty: a pack that carries three of the six kinds is normal."""
    directory = Path(directory)
    kinds: dict[str, list[dict]] = {}
    for kind in KINDS:
        path = directory / f"{kind}.json"
        if not path.is_file():
            continue
        loaded = json.loads(path.read_text(encoding="utf-8"))
        kinds[kind] = loaded if isinstance(loaded, list) else []
    return Data(directory=directory, kinds=kinds)


def problems(directory: Path) -> list[str]:
    """Everything wrong with this directory, offline, as sentences.

    Structure only. Whether `RT-BRACKET` names a routing that makes sense for
    this plant is the plant's question; whether it names a material this pack
    also declares is this function's.
    """
    directory = Path(directory)
    out: list[str] = []
    for path in sorted(directory.glob("*.json")):
        if path.stem not in KINDS:
            out.append(f"{path.name} is not a kind of master data this product reads. "
                       f"The kinds are {', '.join(sorted(KINDS))}.")
    try:
        data = read(directory)
    except json.JSONDecodeError as exc:
        return [*out, f"{directory.name} holds JSON that will not parse: {exc}"]

    for kind, rows in data.kinds.items():
        if not isinstance(rows, list):
            out.append(f"{kind}.json is a list of entries.")
            continue
        allowed = set(REQUIRED[kind]) | set(OPTIONAL[kind])
        for index, row in enumerate(rows, start=1):
            where = f"{kind}.json #{index}"
            if not isinstance(row, dict):
                out.append(f"{where} is an entry with named fields.")
                continue
            for missing in (k for k in REQUIRED[kind] if row.get(k) in (None, "")):
                out.append(f"{where} has no {missing}.")
            for unknown in sorted(set(row) - allowed):
                out.append(f"{where} carries {unknown!r}, which is not a field "
                           f"{kind} has. It takes {', '.join(sorted(allowed))}.")

    known_materials = {row.get("code") for row in data.rows("materials")}
    known_equipment = {row.get("code") for row in data.rows("equipment")}
    for kind, fields in (("routings", ("material",)), ("quality_specs", ("material",)),
                         ("lots", ("material",)), ("work_orders", ("material",)),
                         ("bom", ("parent", "component"))):
        for index, row in enumerate(data.rows(kind), start=1):
            for name in fields:
                value = row.get(name)
                if value and value not in known_materials:
                    out.append(f"{kind}.json #{index} names material {value!r}, which "
                               "materials.json does not declare.")
    for index, row in enumerate(data.rows("routings"), start=1):
        for step in row.get("operations") or []:
            if not isinstance(step, dict) or not step.get("equipment"):
                out.append(f"routings.json #{index} has an operation with no equipment.")
            elif step["equipment"] not in known_equipment:
                out.append(f"routings.json #{index} runs on {step['equipment']!r}, which "
                           "equipment.json does not declare.")
    for index, row in enumerate(data.rows("equipment"), start=1):
        parent = row.get("parent")
        if parent and parent not in known_equipment:
            out.append(f"equipment.json #{index} hangs under {parent!r}, which "
                       "equipment.json does not declare.")
    for index, row in enumerate(data.rows("maintenance_plans"), start=1):
        machine = row.get("equipment")
        if machine and machine not in known_equipment:
            out.append(f"maintenance_plans.json #{index} is a job on {machine!r}, which "
                       "equipment.json does not declare.")
        trigger = row.get("trigger")
        if trigger and trigger not in TRIGGERS:
            out.append(f"maintenance_plans.json #{index} comes due on {trigger!r}, which "
                       f"is not something this product counts. It counts "
                       f"{', '.join(sorted(TRIGGERS))}.")
    for index, row in enumerate(data.rows("shifts"), start=1):
        machine = row.get("equipment")
        if machine and machine not in known_equipment:
            out.append(f"shifts.json #{index} belongs to {machine!r}, which "
                       "equipment.json does not declare.")
        for field_name in ("starts", "ends"):
            if not _is_clock(row.get(field_name)):
                out.append(f"shifts.json #{index} has a {field_name} of "
                           f"{row.get(field_name)!r}; a shift starts and ends at a time "
                           "of day written HH:MM.")
        days = row.get("days")
        if days is not None and (not isinstance(days, str) or len(days) != 7
                                 or set(days) - {"0", "1"}):
            out.append(f"shifts.json #{index} has days {days!r}; that is a seven "
                       'character mask of 0 and 1, Monday first - "1111100" is weekdays.')
    # A component that is its own parent is a bill of materials that never
    # terminates, and the explosion would recurse until something gave way.
    for index, row in enumerate(data.rows("bom"), start=1):
        if row.get("parent") and row.get("parent") == row.get("component"):
            out.append(f"bom.json #{index} makes {row['parent']!r} a component of itself.")
    return out


def _is_clock(value) -> bool:
    """`HH:MM` or `HH:MM:SS`, and nothing else. A shift that starts at "6am"
    parses in no library this product uses."""
    if not isinstance(value, str):
        return False
    try:
        time.fromisoformat(value)
    except ValueError:
        return False
    return True


# ----------------------------------------------------------------- applying


def seed(session, directory: Path, cycles: dict[str, float] | None = None) -> dict[str, dict]:
    """Write this pack's master data, and say what was made and what was there.

    Idempotent by code: an entry whose code already exists is counted as
    present and left alone, never updated.
    """
    from sqlalchemy import select

    from fsmes.domain import (
        BomItem,
        Equipment,
        EquipmentLevel,
        MaintenancePlan,
        Material,
        MaterialLot,
        MaterialType,
        QualitySpec,
        Routing,
        RoutingOperation,
        ShiftPattern,
        TriggerKind,
        WorkOrder,
    )
    from fsmes.services import workorders

    data = read(directory)
    cycles = cycles or {}
    receipt: dict[str, dict] = {kind: {"made": 0, "present": 0} for kind in data.kinds}

    def count(kind: str, made: bool) -> None:
        receipt[kind]["made" if made else "present"] += 1

    equipment: dict[str, Equipment] = {}
    for row in data.rows("equipment"):
        code = row["code"]
        existing = session.scalar(select(Equipment).where(Equipment.code == code))
        if existing is not None:
            equipment[code] = existing
            count("equipment", made=False)
            continue
        made = Equipment(
            code=code,
            name=row["name"],
            level=EquipmentLevel(row["level"]),
            parent=equipment.get(row.get("parent")),
            ideal_cycle_seconds=row.get("ideal_cycle_seconds", cycles.get(code)),
        )
        session.add(made)
        equipment[code] = made
        count("equipment", made=True)

    materials: dict[str, Material] = {}
    for row in data.rows("materials"):
        code = row["code"]
        existing = session.scalar(select(Material).where(Material.code == code))
        if existing is not None:
            materials[code] = existing
            count("materials", made=False)
            continue
        made = Material(code=code, name=row["name"], unit=row.get("unit", "ea"),
                        type=MaterialType(row.get("type", "raw")))
        session.add(made)
        materials[code] = made
        count("materials", made=True)

    for row in data.rows("bom"):
        parent, component = materials[row["parent"]], materials[row["component"]]
        seq = row.get("operation_seq")
        # The unique key is (parent, component, operation) - the same
        # component may legitimately be consumed at two stations - so the
        # "already there" question has to be asked with all three.
        session.flush()
        existing = session.scalar(select(BomItem.id).where(
            BomItem.parent_id == parent.id, BomItem.component_id == component.id,
            BomItem.operation_seq == seq))
        if existing:
            count("bom", made=False)
            continue
        session.add(BomItem(parent=parent, component=component,
                            quantity=row["quantity"], operation_seq=seq))
        count("bom", made=True)

    for row in data.rows("routings"):
        code = row["code"]
        if session.scalar(select(Routing.id).where(Routing.code == code)):
            count("routings", made=False)
            continue
        routing = Routing(code=code, name=row["name"], material=materials[row["material"]])
        routing.operations = [
            RoutingOperation(seq=step.get("seq", (i + 1) * 10), name=step["name"],
                             equipment=equipment[step["equipment"]])
            for i, step in enumerate(row["operations"])
        ]
        session.add(routing)
        count("routings", made=True)

    for row in data.rows("quality_specs"):
        material = materials[row["material"]]
        existing = session.scalar(select(QualitySpec.id).where(
            QualitySpec.material_id == material.id,
            QualitySpec.characteristic == row["characteristic"]))
        if existing:
            count("quality_specs", made=False)
            continue
        session.add(QualitySpec(material=material, characteristic=row["characteristic"],
                                unit=row.get("unit", ""), min_value=row.get("min"),
                                max_value=row.get("max")))
        count("quality_specs", made=True)

    for row in data.rows("lots"):
        code = row["code"]
        if session.scalar(select(MaterialLot.id).where(MaterialLot.code == code)):
            count("lots", made=False)
            continue
        session.add(MaterialLot(code=code, material=materials[row["material"]],
                                quantity=row["quantity"], original_quantity=row["quantity"]))
        count("lots", made=True)

    if data.rows("work_orders"):
        # The services below look orders up by code, so the rows above have to
        # be in the session's own view of the database before the first one.
        session.flush()
    for row in data.rows("work_orders"):
        code = row["code"]
        if session.scalar(select(WorkOrder.id).where(WorkOrder.code == code)):
            count("work_orders", made=False)
            continue
        workorders.create(session, code=code, material_code=row["material"],
                          quantity=row["quantity"], priority=row.get("priority", 10),
                          actor="pack-apply")
        if row.get("release", True):
            workorders.release(session, code, actor="pack-apply")
        count("work_orders", made=True)

    for row in data.rows("maintenance_plans"):
        code = row["code"]
        if session.scalar(select(MaintenancePlan.id).where(MaintenancePlan.code == code)):
            count("maintenance_plans", made=False)
            continue
        session.add(MaintenancePlan(
            code=code, name=row["name"], equipment=equipment[row["equipment"]],
            trigger=TriggerKind(row["trigger"]), interval=float(row["interval"]),
            instructions=row.get("instructions"), document_code=row.get("document_code"),
            expected_minutes=float(row.get("expected_minutes", 30.0))))
        count("maintenance_plans", made=True)

    for row in data.rows("shifts"):
        code = row["code"]
        if session.scalar(select(ShiftPattern.id).where(ShiftPattern.code == code)):
            count("shifts", made=False)
            continue
        session.add(ShiftPattern(
            code=code, name=row["name"], starts=time.fromisoformat(row["starts"]),
            ends=time.fromisoformat(row["ends"]), days=row.get("days", "1111100"),
            # A shift with no equipment belongs to the whole site, which is
            # the column's own meaning for null - not a missing value.
            equipment=equipment.get(row["equipment"]) if row.get("equipment") else None))
        count("shifts", made=True)
    return receipt
