"""Equipment endpoints: current states, manual state changes, per-machine OEE."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from fsmes.api.deps import ActorDep, DbDep, require
from fsmes.domain import EquipmentStateName
from fsmes.services import equipment

router = APIRouter()


@router.get("/states")
def current_states(db: DbDep) -> list[dict]:
    return [
        {
            "equipment": s.equipment.code,
            "state": s.state,
            "reason": s.reason,
            "since": s.started_at,
        }
        for s in equipment.current_states(db)
    ]


class StateIn(BaseModel):
    state: EquipmentStateName
    reason: str | None = None


@router.post("/{code}/state", dependencies=[require("equipment.state")])
def set_state(code: str, body: StateIn, db: DbDep, actor: ActorDep) -> dict:
    s = equipment.set_state(db, equipment_code=code, state=body.state, reason=body.reason, actor=actor)
    return {"equipment": code, "state": s.state, "since": s.started_at}


@router.get("/{code}/oee")
def oee(code: str, db: DbDep, hours: float = 8.0) -> dict:
    return equipment.oee(db, equipment_code=code, hours=hours)


# ---------------------------------------------------------------- the surface
# Literal paths first: FastAPI matches in registration order, and `/{code}`
# would otherwise swallow `/tree` and `/alarms`.

from fsmes.services import masterdata, tags  # noqa: E402


@router.get("/tree")
def equipment_tree(db: DbDep) -> dict:
    """The plant as a tree - any depth - with cost centers resolved and the
    current state of every machine."""
    return {"roots": tags.tree(db)}


@router.get("/alarms")
def all_alarms(db: DbDep) -> list[dict]:
    """Active alarm bits, by name, on every machine."""
    return tags.alarms(db, equipment.work_units(db))


@router.get("/tags")
def all_tags(db: DbDep) -> dict:
    """Every tag on every machine, flat, with a health line per machine -
    the engineering view of the whole tag fabric."""
    return tags.browse(db, equipment.work_units(db))


@router.get("/{code}/tags")
def machine_tags(code: str, db: DbDep) -> dict:
    """Every tag the machine publishes: latest value, age, and what the
    manifest knows (kind, unit, bounds, setpoint pairing, alarm bits)."""
    return tags.snapshot(db, masterdata.get_equipment(db, code))


@router.get("/{code}/alarms")
def machine_alarms(code: str, db: DbDep,
                   hours: float = Query(0, ge=0, le=720,
                                        description="Also list every change of the alarm word this far back.")) -> dict:
    """The alarm bits set right now; with `hours`, also what the machine
    raised over that window - the question a failed check asks later."""
    unit = masterdata.get_equipment(db, code)
    out = tags.alarms(db, [unit])[0]
    if hours:
        out["hours"] = hours
        out["history"] = tags.alarm_history(db, unit, hours)
    return out


@router.get("/{code}")
def machine_head(code: str, db: DbDep) -> dict:
    """Where the machine sits, what it is doing, and whose cost center it
    bills to - what a machine page opens with."""
    return tags.head(db, masterdata.get_equipment(db, code))
