"""Master data lookups and creation."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.domain import (
    BomItem,
    Equipment,
    EquipmentLevel,
    Material,
    MaterialType,
    Person,
    Routing,
    RoutingOperation,
)
from fsmes.services import Conflict, Invalid, NotFound, audit


def get_material(session: Session, code: str) -> Material:
    obj = session.scalar(select(Material).where(Material.code == code))
    if obj is None:
        raise NotFound(f"material {code!r} not found")
    return obj


def descendants(session: Session, root: Equipment) -> list[Equipment]:
    """Everything under a node, at any depth, excluding the node itself.

    The hierarchy has always been arbitrarily deep in the schema and exactly
    two levels deep in practice: every reader did one `parent_id ==` hop and
    called it done. That works for line→machine and silently loses every
    machine the day someone models line→cell→machine, which is how a real
    plant with six lines is actually laid out.

    Breadth-first in Python rather than a recursive CTE: the tree is tens of
    rows, not thousands, and SQLite and PostgreSQL disagree about enough CTE
    syntax that portability is worth more here than one query.
    """
    found: list[Equipment] = []
    seen = {root.id}
    frontier = [root.id]
    while frontier:
        children = list(session.scalars(
            select(Equipment)
            .where(Equipment.parent_id.in_(frontier))
            .order_by(Equipment.code)))
        frontier = []
        for child in children:
            if child.id in seen:          # a cycle would otherwise hang the plant
                continue
            seen.add(child.id)
            found.append(child)
            frontier.append(child.id)
    return found


def work_units_under(session: Session, root: Equipment) -> list[Equipment]:
    """The machines beneath a line — through cells, groups, whatever a site
    put in between."""
    return [eq for eq in descendants(session, root)
            if eq.level == EquipmentLevel.WORK_UNIT]


def pick_machine(session: Session, operation) -> int:
    """Which machine runs an operation that names a whole cell.

    Deterministic by code, not "whatever the database returned first", so two
    identical orders plan identically and a test can assert an answer. This
    is the placeholder for real late-binding dispatch: a plant with
    interchangeable machines wants the one that is free at the time, which
    needs the schedule, not master data.
    """
    centre = operation.work_center
    if centre is None:
        raise Invalid(
            f"operation {operation.seq} names neither a machine nor a work centre")
    members = work_units_under(session, centre)
    if not members:
        raise Invalid(
            f"work centre {centre.code!r} has no machines, so operation "
            f"{operation.seq} has nowhere to run")
    return min(members, key=lambda eq: eq.code).id


def work_center_of(session: Session, equipment: Equipment) -> Equipment | None:
    """The line a machine belongs to, however deep it sits beneath it.

    The upward counterpart of `work_units_under`: a machine in a cell in a
    line answers with the line. Returns None for a machine hanging outside
    any work centre, which is a real state and not an error.
    """
    node: Equipment | None = equipment
    seen = set()
    while node is not None and node.id not in seen:
        if node.level == EquipmentLevel.WORK_CENTER:
            return node
        seen.add(node.id)
        node = node.parent
    return None


def cost_center(session: Session, equipment: Equipment) -> str | None:
    """The cost center this machine's work is accounted to.

    Inherited: a machine with none takes its cell's, a cell with none takes
    its line's. A plant therefore sets it once per line and overrides only
    where accounting genuinely differs, which is how cost centers are
    maintained in every ERP this will talk to.
    """
    node: Equipment | None = equipment
    seen = set()
    while node is not None and node.id not in seen:
        if node.cost_center:
            return node.cost_center
        seen.add(node.id)
        node = node.parent
    return None


def get_equipment(session: Session, code: str) -> Equipment:
    obj = session.scalar(select(Equipment).where(Equipment.code == code))
    if obj is None:
        raise NotFound(f"equipment {code!r} not found")
    return obj


def _ensure_unique(session: Session, model, code: str) -> None:
    if session.scalar(select(model).where(model.code == code)):
        raise Conflict(f"{model.__tablename__} code {code!r} already exists")


def create_equipment(
    session: Session,
    *,
    code: str,
    name: str,
    level: EquipmentLevel,
    parent_code: str | None = None,
    ideal_cycle_seconds: float | None = None,
    cost_center: str | None = None,
    actor: str = "system",
) -> Equipment:
    _ensure_unique(session, Equipment, code)
    parent = get_equipment(session, parent_code) if parent_code else None
    obj = Equipment(code=code, name=name, level=level, parent=parent, ideal_cycle_seconds=ideal_cycle_seconds,
                    cost_center=cost_center)
    session.add(obj)
    session.flush()
    audit.record(
        session,
        actor=actor,
        action="equipment.created",
        entity_type="equipment",
        entity_id=code,
        after={"name": name, "level": level.value, "parent": parent_code},
    )
    return obj


def create_material(
    session: Session,
    *,
    code: str,
    name: str,
    unit: str = "ea",
    type: MaterialType = MaterialType.RAW,
    actor: str = "system",
) -> Material:
    _ensure_unique(session, Material, code)
    obj = Material(code=code, name=name, unit=unit, type=type)
    session.add(obj)
    session.flush()
    audit.record(
        session,
        actor=actor,
        action="material.created",
        entity_type="material",
        entity_id=code,
        after={"name": name, "unit": unit, "type": type.value},
    )
    return obj


def add_bom_item(
    session: Session, *, parent_code: str, component_code: str, quantity: float,
    operation_seq: int | None = None, actor: str = "system"
) -> BomItem:
    """Add a component line, optionally naming the station that consumes it.

    A line consumes different components at different stations. Recording
    which is what lets a supervisor stage the right material at the right
    machine, and what lets a recall name a station rather than an order.
    """
    parent = get_material(session, parent_code)
    component = get_material(session, component_code)
    obj = BomItem(parent=parent, component=component, quantity=quantity,
                  operation_seq=operation_seq)
    session.add(obj)
    session.flush()
    audit.record(
        session,
        actor=actor,
        action="bom.item_added",
        entity_type="material",
        entity_id=parent_code,
        after={"component": component_code, "quantity": quantity},
    )
    return obj


def create_routing(
    session: Session,
    *,
    code: str,
    name: str,
    material_code: str,
    operations: list[dict],
    actor: str = "system",
) -> Routing:
    """Define how a material is made.

    operations: [{"seq": 10, "name": "Mix", "equipment": "MIX01"}, ...]

    Each step names either `equipment` (this machine) or `work_center` (any
    machine in this cell), and may carry `setup_seconds`,
    `run_seconds_per_unit` and `labour_seconds_per_unit`. A step naming
    neither place is refused rather than stored: an operation with nowhere to
    happen is master data that will fail at dispatch, hours later, to
    somebody who did not write it.
    """
    _ensure_unique(session, Routing, code)
    material = get_material(session, material_code)
    routing = Routing(code=code, name=name, material=material)
    for op in operations:
        machine = get_equipment(session, op["equipment"]) if op.get("equipment") else None
        centre = get_equipment(session, op["work_center"]) if op.get("work_center") else None
        if machine is None and centre is None:
            raise Invalid(
                f"routing {code!r} operation {op.get('seq')}: name an "
                f"'equipment' or a 'work_center' — an operation needs "
                f"somewhere to happen")
        routing.operations.append(RoutingOperation(
            seq=op["seq"], name=op["name"],
            equipment=machine, work_center=centre,
            setup_seconds=op.get("setup_seconds"),
            run_seconds_per_unit=op.get("run_seconds_per_unit"),
            labour_seconds_per_unit=op.get("labour_seconds_per_unit"),
        ))
    session.add(routing)
    session.flush()
    audit.record(
        session,
        actor=actor,
        action="routing.created",
        entity_type="routing",
        entity_id=code,
        after={"material": material_code, "operations": [op["seq"] for op in operations]},
    )
    return routing


def create_person(session: Session, *, code: str, name: str, role: str = "operator", actor: str = "system") -> Person:
    _ensure_unique(session, Person, code)
    obj = Person(code=code, name=name, role=role)
    session.add(obj)
    session.flush()
    audit.record(
        session,
        actor=actor,
        action="person.created",
        entity_type="person",
        entity_id=code,
        after={"name": name, "role": role},
    )
    return obj


def material_codes(session: Session) -> dict[int, str]:
    """{id: code} for every material - the lookup a bulk walk of serial
    units needs instead of a relationship load per row."""
    return dict(session.execute(select(Material.id, Material.code)).all())
