"""System endpoints: health, Prometheus-style metrics, audit trail queries."""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select, text

from fsmes.api.deps import DbDep, require
from fsmes.domain import AuditLog, ErpMessage, MessageStatus, OrderStatus, TagValue, WorkOrder

router = APIRouter()


@router.get("/health")
def health(db: DbDep) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(db: DbDep) -> str:
    lines = []
    for status in OrderStatus:
        count = db.scalar(select(func.count(WorkOrder.id)).where(WorkOrder.status == status)) or 0
        lines.append(f'mes_work_orders{{status="{status.value}"}} {count}')
    pending = db.scalar(select(func.count(ErpMessage.id)).where(ErpMessage.status == MessageStatus.PENDING)) or 0
    lines.append(f"mes_erp_messages_pending {pending}")
    # The highest id, not a count: counting two million rows on every scrape
    # was the most expensive thing this API did. Ids are monotonic; the number
    # says how many were ever written, which is what a rate wants.
    lines.append(f"mes_tag_values_max_id {db.scalar(select(func.max(TagValue.id))) or 0}")
    lines.append(f"mes_audit_entries_max_id {db.scalar(select(func.max(AuditLog.id))) or 0}")
    return "\n".join(lines) + "\n"


@router.get("/ai", dependencies=[require("audit.read")])
def local_ai() -> dict:
    """The local AI layer: model server, GPU, and every assigned job the
    model has - when it last did anything and where its output went.

    Machine-wide, not per-plant: one Ollama and one GPU serve every plant on
    the box, so both plants' Ops screens show the same panel. Reads only.
    """
    from fsmes.services import ai_status

    return ai_status.status()


@router.get("/audit", dependencies=[require("audit.read")])
def audit_trail(
    db: DbDep,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor: str | None = None,
    limit: int = 100,
) -> list[dict]:
    query = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000))
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditLog.entity_id == entity_id)
    if actor:
        query = query.where(AuditLog.actor == actor)
    return [
        {
            "ts": entry.ts,
            "actor": entry.actor,
            "on_behalf_of": entry.on_behalf_of,
            "action": entry.action,
            "entity_type": entry.entity_type,
            "entity_id": entry.entity_id,
            "before": entry.before,
            "after": entry.after,
        }
        for entry in db.scalars(query)
    ]
