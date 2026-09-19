"""Equipment endpoints: current states, manual state changes, per-machine OEE."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from fsmes.api.deps import ActorDep, DbDep, ReadDbDep, require
from fsmes.domain import EquipmentStateName
from fsmes.services import Invalid, equipment, reasons
from fsmes.services import connection as connection_service

router = APIRouter()


@router.get("/states")
def current_states(db: DbDep) -> list[dict]:
    return [
        {
            "equipment": s.equipment.code,
            "state": s.state,
            "reason": s.reason,
            "reason_code": s.reason_code,
            "since": s.started_at,
        }
        for s in equipment.current_states(db)
    ]


@router.get("/connections")
def connections(db: DbDep) -> dict:
    """Whether this MES can see each machine, and since when.

    Separate from `/states` because it is a separate fact: a machine can be
    down and reachable, or fine and unreachable, and a screen that has to
    pick one of those has lost the more useful half (decision 0030).

    Every machine appears, including the ones nothing has ever reported a
    connection for - they are `unknown`, which is not the same as connected
    and is not a fault. The envelope states the total, so a list that is
    shorter than the plant cannot read as the whole plant.
    """
    units = equipment.work_units(db)
    open_rows = connection_service.open_connections(db, [unit.id for unit in units])
    counts = connection_service.watching(db)
    return {
        "machines": [
            {"equipment": unit.code, "name": unit.name,
             **connection_service.summary(open_rows.get(unit.id))}
            for unit in units
        ],
        "machines_total": counts["machines"],
        "connected": counts["connected"],
        "disconnected": counts["disconnected"],
        "unknown": counts["unknown"],
    }


class StateIn(BaseModel):
    state: EquipmentStateName
    reason: str | None = None
    # A code from the plant's approved downtime vocabulary. Required for
    # `down` once the plant has one, ignored-as-absent when it has none.
    reason_code: str | None = None


@router.post("/{code}/state", dependencies=[require("equipment.state")])
def set_state(code: str, body: StateIn, db: DbDep, actor: ActorDep) -> dict:
    """Set a machine's state, and label the stop.

    **Once the plant has an approved downtime vocabulary, going down takes a
    code from it.** A typed sentence is refused here rather than stored beside
    six codes that were supposed to replace it - four spellings of one reason
    is the failure the vocabulary exists to end, and a list nobody has to use
    is not a list. A plant that has approved nothing is unchanged: the text
    box is what it has, and the text is what it gets.

    The other three writers of a reason - a trigger, an inbound feed, an agent
    tool - are deliberately not gated this way. They write text as they always
    did, and the pareto says how much of a window came from the list and how
    much did not, rather than pretending.
    """
    vocabulary = reasons.catalog(db)
    reason, code_given = body.reason, body.reason_code
    if code_given:
        if code_given not in vocabulary:
            raise Invalid(
                f"{code_given!r} is not one of this plant's {len(vocabulary)} approved "
                "downtime reasons. Read GET /equipment/downtime-reasons for the list.")
        # The sentence the rest of the product already reads comes from the
        # approved term, so a code and its text can never disagree.
        reason = reason or reasons.names(db).get(code_given)
    elif vocabulary and body.state is EquipmentStateName.DOWN:
        raise Invalid(
            f"this plant has {len(vocabulary)} approved downtime reasons, so a stop is "
            "labelled from the list: send `reason_code`. Read GET "
            "/equipment/downtime-reasons for the list - it carries an explicit code "
            "for a reason nobody has determined yet, which is an answer, where a "
            "blank is not.")
    s = equipment.set_state(db, equipment_code=code, state=body.state, reason=reason,
                            reason_code=code_given, actor=actor)
    return {"equipment": code, "state": s.state, "since": s.started_at,
            "reason": s.reason, "reason_code": s.reason_code}


@router.get("/{code}/oee")
def oee(code: str, db: ReadDbDep, hours: float = 8.0) -> dict:
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
