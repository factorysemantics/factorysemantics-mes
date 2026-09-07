"""ERP tools: the outbox an agent could not see.

Until now a confirmation that kept failing retried forever and nobody but a
Prometheus gauge knew. These make the queue readable - what is waiting, what
failed and why, what died - and let a supervisor-granted agent revive a
dead message once the cause is fixed.
"""

from __future__ import annotations


def register(mcp, call, write, identify) -> dict:
    @mcp.tool()
    def erp_outbox(plant: str, limit: int = 30) -> dict:
        """The ERP interface's queue: counts by status (pending, sent, error,
        dead) and kind (operation confirmations, order completions, inbound
        schedules), how old the oldest pending message is, and the latest
        messages with their attempts and last error."""
        return {"plant": plant, **call(plant, "GET", f"/erp/outbox?limit={limit}")}

    @mcp.tool()
    def erp_retry(plant: str, message_id: int, dry_run: bool = False,
                  on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
        """Put a dead or failing outbox message back in the queue. Needs
        orders.close (a supervisor's call) - fix what killed it first."""
        identify(on_behalf_of, client_ref)
        return write(plant, f"/erp/outbox/{message_id}/retry", {}, dry_run,
                     f"retry ERP message {message_id}")

    return {f.__name__: f for f in (erp_outbox, erp_retry)}
