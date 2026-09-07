"""The assistant endpoint.

Everything the model sees about the plant is gathered here, from the same
services the screens use, and scoped to what the signed-in person may see.
The model is handed facts and asked to explain them - it never queries
anything itself, so it cannot reach past the person's own permissions.
"""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from fsmes.api.deps import DbDep, UserDep
from fsmes.config import get_settings
from fsmes.domain import NonConformance, Person, WorkOrder
from fsmes.services import agent, assistant, auth, documents
from fsmes.services import analysis as analysis_service

router = APIRouter()


class AskIn(BaseModel):
    question: str
    # Where the person is standing. The assistant uses it to avoid telling
    # someone to open the screen they are already looking at.
    screen: str | None = None


def _facts(db, capabilities: set[str]) -> dict:
    """A small, honest picture of the plant for the model to reason over."""
    if "plant.read" not in capabilities:
        return {"note": "this person may not read plant data"}

    facts: dict = {}
    try:
        orders = db.scalars(
            select(WorkOrder).order_by(WorkOrder.id.desc()).limit(6)).all()
        facts["recent_orders"] = [
            {"code": o.code, "material": o.material.code, "status": o.status.value,
             "quantity": o.quantity, "good": o.good_qty, "scrap": o.scrap_qty}
            for o in orders
        ]
    except Exception:
        pass

    try:
        open_ncs = db.scalars(
            select(NonConformance).where(NonConformance.status == "open").limit(5)).all()
        facts["open_nonconformances"] = [
            {"code": n.code, "description": n.description} for n in open_ncs]
    except Exception:
        pass

    with contextlib.suppress(Exception):
        facts["oee"] = analysis_service.oee_breakdown(db, hours=8.0).get("line")
        facts["downtime"] = analysis_service.downtime_pareto(db, hours=8.0)

    # The plant's own approved procedure outranks a model's idea of one.
    # Handing these over means an answer about how something is done can
    # quote what was signed rather than invent it.
    with contextlib.suppress(Exception):
        facts["approved_instructions"] = [
            {"code": d.code, "revision": d.revision, "title": d.title,
             "about": d.anchors(), "text": d.body[:1200]}
            for d in documents.approved(db)[:4]
        ]

    return facts


def _guide_out(guide: dict) -> dict:
    out = {"id": guide["id"], "title": guide["title"], "steps": guide["steps"]}
    for key in ("recorded_by", "revision", "approved_by", "kind"):
        if guide.get(key) is not None:
            out[key] = guide[key]
    return out


@router.post("/ask")
def ask(body: AskIn, user: UserDep, db: DbDep) -> dict:
    """Answer, or offer to walk the person through it.

    A 'how do I' question returns a guide: real steps against real controls on
    real screens. Anything else is answered from the plant's current state.
    """
    role = auth.current_role(db, user) or user["role"]
    capabilities = auth.capabilities_for(db, role)

    guide = assistant.route(body.question, capabilities, db)
    if guide:
        return {
            "kind": "guide",
            "guide": _guide_out(guide),
            "say": f"I can walk you through it — {guide['title'].lower()}. "
                   f"{len(guide['steps'])} steps.",
        }

    return {
        "kind": "answer",
        "say": assistant.answer(body.question, _facts(db, capabilities), capabilities),
    }


# ------------------------------------------------------------------- agent

class AgentIn(BaseModel):
    message: str
    session: str | None = None
    screen: str | None = None


class ResolveIn(BaseModel):
    session: str
    proposal: str
    reason: str | None = None


_local_ready = False


def _ensure_local() -> str:
    """The tools address this plant by name, over its own API."""
    global _local_ready
    settings = get_settings()
    plant = settings.plant_name or "plant"
    if not _local_ready:
        host = settings.api_host if settings.api_host not in ("0.0.0.0", "") else "127.0.0.1"
        agent.serve_locally(plant, f"http://{host}:{settings.api_port}")
        _local_ready = True
    return plant


def _who(db, user: dict) -> tuple[str, set[str], str]:
    role = auth.current_role(db, user) or user["role"]
    person = db.scalar(select(Person).where(Person.code == user["sub"]))
    return role, auth.capabilities_for(db, role), (person.name if person else user["sub"])


def _names(db) -> dict:
    """Real codes for the example suggestions; every lookup may fail quietly."""
    from fsmes.domain import QualitySpec
    names: dict = {}
    with contextlib.suppress(Exception):
        from fsmes.services import equipment as equipment_service
        units = equipment_service.work_units(db)
        if units:
            names["machine"] = units[0].code
    with contextlib.suppress(Exception):
        spec = db.scalar(select(QualitySpec).limit(1))
        if spec is not None:
            names["characteristic"] = spec.characteristic
            names["material"] = spec.material.code
            lo, hi = spec.min_value, spec.max_value
            if lo is not None and hi is not None:
                names["mid"] = f"{(lo + hi) / 2:g}"
    with contextlib.suppress(Exception):
        open_order = db.scalar(select(WorkOrder).where(WorkOrder.status.in_(("released", "running")))
                               .order_by(WorkOrder.id.desc()).limit(1))
        if open_order is not None:
            names["order"] = open_order.code
        planned = db.scalar(select(WorkOrder).where(WorkOrder.status == "planned")
                            .order_by(WorkOrder.id.desc()).limit(1))
        if planned is not None:
            names["planned_order"] = planned.code
    with contextlib.suppress(Exception):
        if "material" not in names:
            from fsmes.domain import Material
            first = db.scalar(select(Material).limit(1))
            if first is not None:
                names["material"] = first.code
    with contextlib.suppress(Exception):
        from fsmes.domain import LotStatus, MaterialLot
        lot = db.scalar(select(MaterialLot).where(MaterialLot.status == LotStatus.AVAILABLE).limit(1))
        if lot is not None:
            names["lot"] = lot.code
    return names


@router.get("/suggestions")
def agent_suggestions(user: UserDep, db: DbDep, screen: str | None = None) -> dict:
    """Things to try on this screen, in plain words, using this plant's own codes."""
    _role, capabilities, _name = _who(db, user)
    return {"suggestions": assistant.suggestions(screen, capabilities, _names(db))}


@router.get("/agent/status")
def agent_status(user: UserDep) -> dict:
    """Is the cloud brain on, and what has it cost this month."""
    return agent.status()


@router.post("/agent")
def agent_message(body: AgentIn, user: UserDep, db: DbDep) -> dict:
    """Say what you want. A 'how do I' still gets a guide; anything else goes
    to the agent, which reads freely and proposes every change."""
    role, capabilities, name = _who(db, user)
    guide = assistant.route(body.message, capabilities, db)
    if guide:
        return {
            "kind": "guide", "session": body.session,
            "guide": _guide_out(guide),
            "say": f"I can walk you through it — {guide['title'].lower()}. {len(guide['steps'])} steps.",
        }
    plant = _ensure_local()
    sess = agent.get_session(body.session, user["sub"]) or agent.open_session(user["sub"], plant, capabilities)
    return agent.message(sess, body.message, name=name, role=role)


@router.post("/agent/confirm")
def agent_confirm(body: ResolveIn, user: UserDep) -> dict:
    """The person clicked Do it. The write runs on their behalf."""
    sess = agent.get_session(body.session, user["sub"])
    if sess is None:
        raise HTTPException(404, "that conversation has expired - ask again")
    return agent.confirm(sess, body.proposal)


@router.post("/agent/decline")
def agent_decline(body: ResolveIn, user: UserDep) -> dict:
    sess = agent.get_session(body.session, user["sub"])
    if sess is None:
        raise HTTPException(404, "that conversation has expired - ask again")
    return agent.decline(sess, body.proposal, body.reason)


@router.get("/guides")
def guides(user: UserDep, db: DbDep) -> dict:
    """What this person can be shown how to do.

    Filtered by capability, because teaching someone a task they will be
    refused at the last step is worse than saying it is not theirs to do.
    """
    role = auth.current_role(db, user) or user["role"]
    capabilities = auth.capabilities_for(db, role)
    return {
        "guides": [
            {"id": g["id"], "title": g["title"], "steps": len(g["steps"]),
             "kind": g.get("kind", "guide")}
            for g in assistant.visible_guides(capabilities, db)
        ]
    }


@router.get("/guides/{guide_id}")
def guide(guide_id: str, user: UserDep, db: DbDep) -> dict:
    role = auth.current_role(db, user) or user["role"]
    capabilities = auth.capabilities_for(db, role)
    found = assistant.guide_by_id(guide_id, capabilities, db)
    if found is None:
        return {"error": f"no guide {guide_id!r} available to you"}
    return _guide_out(found)
