"""The ERP contract, typed: what crosses the border and what every field means.

Until now both directions were bare dicts whose shape lived in two functions'
`.get()` calls. These models are the contract an adapter implements against
and an ERP team can read. SAP-shaped on purpose: a confirmation per
*operation* carrying quantities, cost center, times and component
consumption is what CO11N wants and what ERPNext's Job Cards and Stock
Entries are built from. One contract, every target.

No money. The MES reports quantities and time against a cost center; the
ERP owns what they are worth.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ProductionRequest(BaseModel):
    """An order arriving from the ERP."""

    code: str
    material: str
    quantity: float
    due_date: datetime | None = None
    priority: int = 50
    erp_reference: str | None = None

    @classmethod
    def from_payload(cls, payload: dict) -> ProductionRequest:
        """Accept the loose spellings adapters have always produced."""
        code = payload.get("code") or payload.get("order") or payload.get("id")
        material = payload.get("material") or payload.get("product")
        if not code:
            raise ValueError("ERP order payload has no order code")
        if not material:
            raise ValueError(f"ERP order {code} has no material")
        due = payload.get("due_date")
        if isinstance(due, str) and due.strip() == "":
            due = None
        return cls(
            code=str(code), material=str(material),
            quantity=float(payload.get("quantity") or 0),
            due_date=due,
            priority=int(payload.get("priority") or 50),
            erp_reference=str(payload.get("erp_reference") or payload.get("id") or code),
        )


class ComponentUse(BaseModel):
    """One lot consumed at an operation."""

    lot: str
    material: str
    quantity: float
    equipment: str | None = None


class OperationConfirmation(BaseModel):
    """What one operation of one order did: the per-step confirmation an
    ERP posts labour, machine time and consumption against."""

    kind: Literal["operation_confirmation"] = "operation_confirmation"
    message_key: str
    order: str
    erp_reference: str | None = None
    material: str
    seq: int
    operation: str
    equipment: str | None = None        # the machine (ISA-95 work unit)
    work_center: str | None = None      # the line it belongs to
    cost_center: str | None = None      # inherited unless the cell overrides
    input_qty: float                    # what reached this step
    good_qty: float
    scrap_qty: float
    wip_qty: float                      # input - good - scrap; negative is reported, not clamped
    setup_seconds: float | None = None  # planned, from the routing snapshot
    machine_seconds: float | None = None   # actual: completed - started
    labour_seconds: float | None = None    # planned per unit x units handled
    started_at: datetime | None = None
    completed_at: datetime | None = None
    components: list[ComponentUse] = Field(default_factory=list)


class OrderCompletion(BaseModel):
    """The order-level close: the finished-goods lot and the totals."""

    kind: Literal["order_completion"] = "order_completion"
    message_key: str
    order: str
    erp_reference: str | None = None
    material: str
    ordered_qty: float
    good_qty: float
    scrap_qty: float
    # What the line made beyond what was ordered. Derivable from the two
    # numbers above, and sent anyway: an ERP that posts `good_qty` without
    # noticing it exceeds `ordered_qty` is exactly how an over-run becomes
    # a stock discrepancy nobody can explain a month later.
    over_qty: float = 0.0
    lot: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


Confirmation = OperationConfirmation | OrderCompletion


def parse_confirmation(payload: dict) -> Confirmation:
    """A stored outbox payload back into its model. The pre-contract kind
    `production_confirmation` (order totals only) reads as an OrderCompletion,
    so messages queued before this contract still deliver."""
    kind = payload.get("kind", "production_confirmation")
    if kind == "operation_confirmation":
        return OperationConfirmation.model_validate(payload)
    data = {k: v for k, v in payload.items() if k != "kind"}
    data.setdefault("message_key", f"{payload.get('order')}:completion")
    data.setdefault("ordered_qty", payload.get("ordered_qty", 0.0))
    return OrderCompletion.model_validate(data)


def as_payload(confirmation: Confirmation) -> dict:
    """What goes over the wire and into the outbox: plain JSON."""
    return confirmation.model_dump(mode="json")
