"""Master data endpoints: equipment hierarchy, materials/BOM, routings, personnel."""

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from fsmes.api.deps import ActorDep, DbDep, require
from fsmes.domain import Equipment, EquipmentLevel, Material, MaterialType, Person, Routing
from fsmes.services import masterdata

router = APIRouter()


class EquipmentIn(BaseModel):
    code: str
    name: str
    level: EquipmentLevel
    parent: str | None = None
    ideal_cycle_seconds: float | None = None
    # The account this node bills to; inherited by everything beneath it
    # unless overridden. A code, never an amount - the ERP owns valuation.
    cost_center: str | None = None


class EquipmentOut(BaseModel):
    code: str
    name: str
    level: EquipmentLevel
    parent: str | None
    ideal_cycle_seconds: float | None
    cost_center: str | None = None


def _equipment_out(eq: Equipment) -> EquipmentOut:
    return EquipmentOut(
        code=eq.code,
        name=eq.name,
        level=eq.level,
        parent=eq.parent.code if eq.parent else None,
        ideal_cycle_seconds=eq.ideal_cycle_seconds,
        cost_center=eq.cost_center,
    )


@router.get("/equipment")
def list_equipment(
    db: DbDep,
    level: EquipmentLevel | None = None,
    q: str | None = Query(None, description="Match a code or a name."),
) -> list[EquipmentOut]:
    """Every node of the plant, or the ones that match. Still a bare list -
    a plant's equipment is hundreds of rows, and every screen that fills a
    picker from it wants all of them - but filterable, so a screen or an
    agent can ask for a line's machines by name instead of reading the plant."""
    query = select(Equipment).order_by(Equipment.id)
    if level:
        query = query.where(Equipment.level == level)
    if q:
        like = f"%{q}%"
        query = query.where(Equipment.code.like(like) | Equipment.name.like(like))
    return [_equipment_out(eq) for eq in db.scalars(query)]


@router.post("/equipment", status_code=201, dependencies=[require("masterdata.write")])
def create_equipment(body: EquipmentIn, db: DbDep, actor: ActorDep) -> EquipmentOut:
    eq = masterdata.create_equipment(
        db,
        code=body.code,
        name=body.name,
        level=body.level,
        parent_code=body.parent,
        ideal_cycle_seconds=body.ideal_cycle_seconds,
        cost_center=body.cost_center,
        actor=actor,
    )
    return _equipment_out(eq)


class MaterialIn(BaseModel):
    code: str
    name: str
    unit: str = "ea"
    type: MaterialType = MaterialType.RAW


class MaterialOut(MaterialIn):
    pass


@router.get("/materials")
def list_materials(
    db: DbDep,
    type: MaterialType | None = None,
    q: str | None = Query(None, description="Match a code or a name."),
) -> list[MaterialOut]:
    query = select(Material).order_by(Material.code)
    if type:
        query = query.where(Material.type == type)
    if q:
        like = f"%{q}%"
        query = query.where(Material.code.like(like) | Material.name.like(like))
    return [MaterialOut(code=m.code, name=m.name, unit=m.unit, type=m.type) for m in db.scalars(query)]


@router.post("/materials", status_code=201, dependencies=[require("masterdata.write")])
def create_material(body: MaterialIn, db: DbDep, actor: ActorDep) -> MaterialOut:
    m = masterdata.create_material(db, code=body.code, name=body.name, unit=body.unit, type=body.type, actor=actor)
    return MaterialOut(code=m.code, name=m.name, unit=m.unit, type=m.type)


class BomItemIn(BaseModel):
    component: str
    quantity: float
    operation_seq: int | None = None


@router.get("/materials/{code}/bom")
def get_bom(code: str, db: DbDep) -> list[dict]:
    material = masterdata.get_material(db, code)
    return [
        {"component": item.component.code, "quantity": item.quantity,
         "operation_seq": item.operation_seq}
        for item in sorted(material.bom_items,
                           key=lambda i: (i.operation_seq if i.operation_seq is not None
                                          else 9999, i.component.code))
    ]


@router.post("/materials/{code}/bom", status_code=201, dependencies=[require("masterdata.write")])
def add_bom_item(code: str, body: BomItemIn, db: DbDep, actor: ActorDep) -> dict:
    item = masterdata.add_bom_item(
        db, parent_code=code, component_code=body.component, quantity=body.quantity,
        operation_seq=body.operation_seq, actor=actor
    )
    return {"component": item.component.code, "quantity": item.quantity,
            "operation_seq": item.operation_seq}


class RoutingOpIn(BaseModel):
    seq: int
    name: str
    equipment: str


class RoutingIn(BaseModel):
    code: str
    name: str
    material: str
    operations: list[RoutingOpIn]


@router.get("/routings")
def list_routings(
    db: DbDep,
    material: str | None = None,
    q: str | None = Query(None, description="Match a routing code or name."),
) -> list[dict]:
    query = select(Routing).order_by(Routing.code)
    if material:
        query = query.join(Material, Routing.material_id == Material.id).where(Material.code == material)
    if q:
        like = f"%{q}%"
        query = query.where(Routing.code.like(like) | Routing.name.like(like))
    return [
        {
            "code": r.code,
            "name": r.name,
            "material": r.material.code,
            "operations": [{"seq": op.seq, "name": op.name, "equipment": op.equipment.code} for op in r.operations],
        }
        for r in db.scalars(query)
    ]


@router.post("/routings", status_code=201, dependencies=[require("masterdata.write")])
def create_routing(body: RoutingIn, db: DbDep, actor: ActorDep) -> dict:
    routing = masterdata.create_routing(
        db,
        code=body.code,
        name=body.name,
        material_code=body.material,
        operations=[op.model_dump() for op in body.operations],
        actor=actor,
    )
    return {"code": routing.code, "operations": len(routing.operations)}


class PersonIn(BaseModel):
    code: str
    name: str
    role: str = "operator"


@router.get("/personnel")
def list_personnel(
    db: DbDep,
    role: str | None = None,
    q: str | None = Query(None, description="Match a code or a name."),
) -> list[PersonIn]:
    """Everyone, or everyone matching. Three hundred people is an ordinary
    plant; a screen pages them and an agent should ask by name."""
    query = select(Person).order_by(Person.code)
    if role:
        query = query.where(Person.role == role)
    if q:
        like = f"%{q}%"
        query = query.where(Person.code.like(like) | Person.name.like(like))
    return [PersonIn(code=p.code, name=p.name, role=p.role) for p in db.scalars(query)]


@router.post("/personnel", status_code=201, dependencies=[require("users.manage")])
def create_person(body: PersonIn, db: DbDep, actor: ActorDep) -> PersonIn:
    p = masterdata.create_person(db, code=body.code, name=body.name, role=body.role, actor=actor)
    return PersonIn(code=p.code, name=p.name, role=p.role)
