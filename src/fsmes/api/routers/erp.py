"""The ERP outbox, visible: what is waiting, what failed, what died, and a
way to revive a dead message once whatever killed it is fixed."""

from __future__ import annotations

from fastapi import APIRouter

from fsmes.api.deps import ActorDep, DbDep, require
from fsmes.services import erp

router = APIRouter()


@router.get("/outbox")
def outbox(db: DbDep, limit: int = 30) -> dict:
    """Counts by status and kind, the oldest pending message's age, and the
    most recent messages with their attempts and errors."""
    return erp.outbox_summary(db, limit=limit)


@router.post("/outbox/{message_id}/retry", dependencies=[require("orders.close")])
def retry(message_id: int, db: DbDep, actor: ActorDep) -> dict:
    """Put a dead (or failing) message back in the queue for the next cycle.
    A supervisor's call: the thing that killed it should be fixed first."""
    message = erp.retry(db, message_id, actor=actor)
    return {"id": message.id, "status": message.status.value, "attempts": message.attempts}
