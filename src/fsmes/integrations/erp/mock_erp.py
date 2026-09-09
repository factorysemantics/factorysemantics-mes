"""The mock ERP — the business system side of the twin.

An in-memory stand-in exposing the endpoints the REST adapter expects:
orders go in (as if planners created them), MES acknowledges and later
confirms, and everything is inspectable over HTTP.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="MES-TWIN Mock ERP", description="Pretends to be the business system above the MES.")

ORDERS: dict[str, dict] = {}
CONFIRMATIONS: list[dict] = []


class OrderIn(BaseModel):
    code: str
    material: str
    quantity: float
    due_date: str | None = None
    priority: int = 50


@app.get("/")
def summary() -> dict:
    return {
        "orders": {status: sum(1 for o in ORDERS.values() if o["status"] == status) for status in
                   {"new", "acked", "confirmed"}},
        "confirmations": len(CONFIRMATIONS),
    }


@app.post("/orders", status_code=201)
def create_order(body: OrderIn) -> dict:
    if body.code in ORDERS:
        raise HTTPException(409, f"order {body.code} already exists")
    ORDERS[body.code] = {**body.model_dump(), "erp_reference": f"ERP-{len(ORDERS) + 1:06d}", "status": "new"}
    return ORDERS[body.code]


@app.get("/orders")
def list_orders(status: str | None = None) -> list[dict]:
    return [o for o in ORDERS.values() if status is None or o["status"] == status]


@app.post("/orders/{code}/ack")
def acknowledge(code: str) -> dict:
    order = ORDERS.get(code)
    if order is None:
        raise HTTPException(404, f"order {code} not found")
    if order["status"] == "new":
        order["status"] = "acked"
    return order


@app.post("/confirmations", status_code=201)
def receive_confirmation(payload: dict) -> dict:
    CONFIRMATIONS.append(payload)
    order = ORDERS.get(str(payload.get("order")))
    # An operation confirmation is progress; only the completion confirms the order.
    if order is not None and payload.get("kind", "production_confirmation") != "operation_confirmation":
        order["status"] = "confirmed"
    return {"received": True}


@app.get("/confirmations")
def list_confirmations(kind: str | None = None) -> list[dict]:
    return [c for c in CONFIRMATIONS if kind is None or c.get("kind", "production_confirmation") == kind]
