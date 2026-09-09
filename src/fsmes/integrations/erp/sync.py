"""The ERP sync worker: one loop, two directions.

Inbound: fetch production requests from the adapter, import them (each
recorded as an ErpMessage). Outbound: deliver what the outbox holds and is
due. A failure marks the message and backs off; after enough attempts the
message is dead and a person decides. The interface never loses a message
and never stalls on a bad one.

Discipline that matters at scale: database transactions are kept short and
NEVER span an HTTP call, so the ERP being slow can't hold up the plant floor.
"""

import asyncio

import structlog

from fsmes.db import session_scope
from fsmes.domain import ErpMessage
from fsmes.integrations.erp.base import ErpAdapter
from fsmes.integrations.erp.contract import parse_confirmation
from fsmes.services import erp

log = structlog.get_logger("erp.sync")


def cycle(adapter: ErpAdapter, scope) -> None:
    """One sync pass. `scope` is a callable returning a session context manager
    (session_scope in production; tests inject their own)."""
    for request in adapter.fetch_orders():
        with scope() as session:
            message = erp.process_inbound(session, request)
            status, error = message.status.value, message.error
        adapter.acknowledge(request.code)
        log.info("order received from ERP", order=request.code, status=status, error=error)

    with scope() as session:
        pending = [(m.id, dict(m.payload)) for m in erp.pending_outbound(session)]
    for message_id, payload in pending:
        try:
            adapter.send_confirmation(parse_confirmation(payload))
        except Exception as exc:
            with scope() as session:
                message = erp.mark_error(session.get(ErpMessage, message_id), exc)
                status, attempts = message.status.value, message.attempts
            log.warning("confirmation delivery failed", order=payload.get("order"),
                        kind=payload.get("kind"), attempts=attempts, status=status, error=str(exc))
        else:
            with scope() as session:
                erp.mark_sent(session.get(ErpMessage, message_id))
            log.info("confirmation sent to ERP", order=payload.get("order"), kind=payload.get("kind"))


async def run(adapter: ErpAdapter, poll_seconds: float) -> None:
    log.info("ERP sync online", adapter=type(adapter).__name__, poll_seconds=poll_seconds)
    while True:
        try:
            # The adapter's HTTP calls block; a thread keeps the event loop free
            # (essential in demo mode, where the ERP runs on this same loop).
            await asyncio.to_thread(cycle, adapter, session_scope)
        except Exception:
            log.exception("ERP sync cycle failed")
        await asyncio.sleep(poll_seconds)
